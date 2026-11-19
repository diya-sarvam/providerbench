"""Per-token prices, fetched by deterministic code and kept in data/prices.json.

Each provider file lists [[pricing]] sources, tried in order; the first that yields a price for a
model wins. Sources:

  models_endpoint  GET <base>/models and read a price from the model object (Together, OpenRouter
                   and a few others include one). Per-token or per-1M values are both recognised.
  openrouter       the provider's own listing on OpenRouter for the model's `openrouter` slug
                   (GET /api/v1/models/<slug>/endpoints, public, no key), matched by provider name
                   and tag. Also gives the quantization the provider serves.
  scrape           GET `url` and apply `pattern` (a regex with named groups model, input, output and
                   optionally cached_input), prices per 1M tokens in `currency`.
  manual           entered in the UI or with `pb prices set`; never overwritten by a refresh.

Prices are stored per 1M tokens in the provider's currency, with source, url, fetched_at.
"""
import json
import re
import time
from pathlib import Path

from . import client, config
from .data import http_get

ROOT = Path(__file__).resolve().parent.parent
PRICES = config.HOME / "prices.json"
FX_URL = "https://open.er-api.com/v6/latest/USD"
DEFAULT_FX = {"USD": 1.0, "INR": 88.0}


def load():
    try:
        return json.loads(PRICES.read_text())
    except (OSError, ValueError):
        return {"prices": {}, "fx": DEFAULT_FX, "fx_at": None}


def save(doc):
    PRICES.parent.mkdir(parents=True, exist_ok=True)
    PRICES.write_text(json.dumps(doc, indent=1, sort_keys=True))


def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"[\d.]+(?:[eE]-?\d+)?", str(v).replace(",", ""))
    return float(m.group()) if m else None


def _sig(x):
    return float("%.6g" % x) if x is not None else None


def _row(inp, cached, out, currency, source, url="", quant=None):
    inp, cached, out = _sig(inp), _sig(cached), _sig(out)
    return {"input": inp, "cached_input": cached if cached is not None else inp, "output": out,
            "currency": currency, "source": source, "url": url, "fetched_at": time.time(), "quant": quant}


# ------------------------------------------------------------------- sources

def from_models_endpoint(prov, spec, wanted, log):
    status, doc, text = client.get_json(prov, "/models")
    if status != 200 or not isinstance(doc, (dict, list)):
        log("  %s /models: %s" % (prov.id, status or text[:100]))
        return {}
    items = doc.get("data", doc) if isinstance(doc, dict) else doc
    by_id = {m.get("id"): m for m in items if isinstance(m, dict)}
    out = {}
    for canon, mid in wanted.items():
        m = by_id.get(mid)
        p = (m or {}).get("pricing")
        if not isinstance(p, dict):
            continue
        inp = _num(p.get("input", p.get("prompt")))
        outp = _num(p.get("output", p.get("completion")))
        cached = _num(p.get("cached_input", p.get("input_cache_read", p.get("cache_read"))))
        if inp is None or outp is None:
            continue
        # OpenRouter-style per-token strings ("0.00000015") vs per-1M numbers (0.15)
        scale = 1e6 if max(inp, outp) < 0.001 else 1.0
        out[canon] = _row(inp * scale, cached * scale if cached is not None else None, outp * scale,
                          spec.get("currency", "USD"), "models_endpoint", prov.url("", "/models"))
    return out


def from_openrouter(prov, spec, wanted, log, models):
    out = {}
    for canon in wanted:
        slug = models.get(canon, {}).get("openrouter")
        if not slug:
            continue
        url = "https://openrouter.ai/api/v1/models/%s/endpoints" % slug
        try:
            eps = json.loads(http_get(url, cache=False))["data"]["endpoints"]
        except Exception as e:  # noqa: BLE001
            log("  openrouter %s: %s" % (slug, e))
            continue
        name, tag = spec.get("provider", prov.name).lower(), spec.get("tag")
        cands = [e for e in eps if (e.get("provider_name") or "").lower() == name]
        if tag:
            exact = [e for e in cands if e.get("tag") == tag]
            cands = exact or [e for e in cands if (e.get("tag") or "").startswith(tag + "/")
                              and not re.search(r"/(flex|priority|fast|turbo|spot|batch)$", e.get("tag") or "")]
        if not cands:
            continue
        e = cands[0]
        p = e.get("pricing") or {}
        inp, outp, cached = _num(p.get("prompt")), _num(p.get("completion")), _num(p.get("input_cache_read"))
        if inp is None or outp is None:
            continue
        q = e.get("quantization")
        out[canon] = _row(inp * 1e6, cached * 1e6 if cached is not None else None, outp * 1e6, "USD",
                          "openrouter", "https://openrouter.ai/%s/providers" % slug,
                          None if q in (None, "unknown") else q)
    return out


def from_scrape(prov, spec, wanted, log):
    try:
        text = http_get(spec["url"], cache=False)
    except RuntimeError as e:
        log("  %s scrape: %s" % (prov.id, e))
        return {}
    found = {}
    for m in re.finditer(spec["pattern"], text, re.S):
        g = m.groupdict()
        inp, outp = _num(g.get("input")), _num(g.get("output"))
        if g.get("model") and inp is not None and outp is not None and g["model"].strip() not in found:
            found[g["model"].strip()] = (inp, _num(g.get("cached_input")), outp)
    out = {}
    for canon, mid in wanted.items():
        if mid in found:
            inp, cached, outp = found[mid]
            out[canon] = _row(inp, cached, outp, spec.get("currency", "USD"), "scrape", spec["url"])
    log("  %s scrape: %d rows on page, %d of our models" % (prov.id, len(found), len(out)))
    return out


def refresh(providers=None, log=print):
    """Re-fetch every provider's prices. Manual rows are kept and never overwritten."""
    providers = providers or config.load_providers()
    models = config.load_models()
    doc = load()
    prices = doc.setdefault("prices", {})
    for prov in providers.values():
        have = prices.setdefault(prov.id, {})
        wanted = {c: mid for c, mid in prov.models.items() if have.get(c, {}).get("source") != "manual"}
        for spec in prov.pricing:
            if not wanted:
                break
            src = spec.get("source")
            try:
                if src == "models_endpoint":
                    if prov.key_env and not prov.key and not spec.get("public"):
                        continue
                    got = from_models_endpoint(prov, spec, wanted, log)
                elif src == "openrouter":
                    got = from_openrouter(prov, spec, wanted, log, models)
                elif src == "scrape":
                    got = from_scrape(prov, spec, wanted, log)
                else:
                    continue
            except Exception as e:  # noqa: BLE001 - one bad source must not stop the others
                log("  %s %s: %s" % (prov.id, src, e))
                continue
            for canon, row in got.items():
                have[canon] = row
                wanted.pop(canon, None)
        log("%-11s %s" % (prov.id, ", ".join("%s=%s" % (c, have[c]["source"]) for c in sorted(have)) or "no prices"
                          + (" (missing: %s)" % ", ".join(sorted(wanted)) if wanted else "")))
    try:
        rates = json.loads(http_get(FX_URL, cache=False))["rates"]
        doc["fx"] = {"USD": 1.0, "INR": rates["INR"]}
        doc["fx_at"] = time.time()
    except Exception as e:  # noqa: BLE001
        log("fx: %s (keeping %s)" % (e, doc.get("fx")))
    save(doc)
    return doc


def set_manual(provider, model, input, output, cached_input=None, currency="USD"):
    doc = load()
    doc.setdefault("prices", {}).setdefault(provider, {})[model] = _row(
        float(input), float(cached_input) if cached_input not in (None, "") else None, float(output),
        currency, "manual")
    save(doc)


def price_for(target, doc=None):
    doc = doc or load()
    return (doc.get("prices", {}).get(target.provider.id) or {}).get(target.model)


def to_usd(amount, currency, doc=None):
    fx = (doc or load()).get("fx") or DEFAULT_FX
    return amount / fx.get(currency, 1.0)


def cost_usd(price, prompt, cached, completion, doc=None):
    """USD for one request, or None when there is no price."""
    if not price:
        return None
    prompt, cached, completion = prompt or 0, cached or 0, completion or 0
    native = ((prompt - cached) * price["input"] + cached * price["cached_input"]
              + completion * price["output"]) / 1e6
    return to_usd(native, price["currency"], doc)


def rows():
    import os
    doc, mock = load(), os.environ.get("PB_MOCK") == "1"
    return [dict(provider=p, model=m, **r) for p, ms in sorted(doc.get("prices", {}).items())
            if mock or not p.startswith("mock-") for m, r in sorted(ms.items())]
