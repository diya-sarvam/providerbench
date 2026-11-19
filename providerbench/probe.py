"""`pb provider add` / `pb provider check`: learn a provider's shape with plain code and a few
cheap requests, then write providers/<id>.toml.

What it finds out:
  models          GET /models, ids matched to models.toml canonical names (exact alias, then a
                  normalised match: case, org prefix, 5p3 -> 5.3, -it, date suffix)
  stream usage    does a streamed reply end with `usage` when stream_options.include_usage is set
  reasoning       which delta field carries reasoning (reasoning_content / reasoning / none)
  cached tokens   is usage.prompt_tokens_details.cached_tokens reported on a repeated prompt
  thinking off    which body fragment turns reasoning off, tried in a fixed order
  responses api   does POST /responses exist

Probes use max_tokens <= 64 and a few hundred prompt tokens: well under a cent per provider.
"""
import json
import re
from pathlib import Path

from . import client, config

ROOT = Path(__file__).resolve().parent.parent
THINKING_OFF_CANDIDATES = [
    {"reasoning_effort": "none"},
    {"chat_template_kwargs": {"enable_thinking": False}},
    {"chat_template_kwargs": {"thinking": False}},
    {"thinking": {"type": "disabled"}},
    {"reasoning": {"enabled": False}},
]
FILLER = " ".join("lorem%d" % i for i in range(400))   # ~600 tokens, enough for most prefix caches


def _toml_val(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, dict):
        return "{ " + ", ".join("%s = %s" % (_toml_key(k), _toml_val(x)) for k, x in v.items()) + " }"
    if isinstance(v, list):
        return "[" + ", ".join(_toml_val(x) for x in v) + "]"
    return json.dumps(v)


def _toml_key(k):
    return k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else json.dumps(k)


def write_toml(pid, d, notes=()):
    lines = ["# Written by `pb provider add`. Check the [models] matches, then open a PR."]
    lines += ["# " + n for n in notes]
    for k in ("name", "base_url", "key_env", "docs"):
        if d.get(k):
            lines.append("%s = %s" % (k, _toml_val(d[k])))
    for table in ("headers", "models", "thinking_off", "extra_body", "quirks"):
        if d.get(table):
            lines.append("")
            lines.append("[%s]" % table)
            for k, v in d[table].items():
                lines.append("%s = %s" % (_toml_key(k), _toml_val(v)))
    for p in d.get("pricing", []):
        lines += ["", "[[pricing]]"] + ["%s = %s" % (k, _toml_val(v)) for k, v in p.items()]
    path = config.PROVIDERS_DIR / ("%s.toml" % pid)
    path.write_text("\n".join(lines) + "\n")
    return path


def _ask(prov, model_id, body):
    return client.chat(prov, model_id, dict({"max_tokens": 64}, **body), timeout=120)


def probe(prov, model_id, log=print):
    q = {}
    r = _ask(prov, model_id, {"messages": [{"role": "user", "content": "What is 17 * 3? Reply with the number."}]})
    if r.get("error"):
        log("  chat: %s" % r["error"][:200])
        q["error"] = r["error"][:200]
        return q
    q["stream_usage"] = bool(r.get("usage_reported"))
    q["reasoning_field"] = "reasoning_content/reasoning" if r["message"]["reasoning"] else "none seen"
    log("  chat ok: ttft %.0f ms, usage %s, reasoning %s" % (r["ttft_ms"], q["stream_usage"],
                                                         "yes" if r["message"]["reasoning"] else "no"))
    msgs = [{"role": "system", "content": "Notes: " + FILLER}, {"role": "user", "content": "Say ok."}]
    _ask(prov, model_id, {"messages": msgs})
    r2 = _ask(prov, model_id, {"messages": msgs})
    q["cached_tokens"] = bool(r2.get("cache_reported"))
    log("  cache: reported=%s cached=%s of %s" % (r2.get("cache_reported"), r2.get("cached_tokens"), r2.get("prompt_tokens")))
    if r["message"]["reasoning"] or (r.get("reasoning_tokens") or 0) > 0:
        for cand in THINKING_OFF_CANDIDATES:
            r3 = _ask(prov, model_id, dict({"messages": [{"role": "user", "content": "What is 17 * 3?"}]}, **cand))
            if not r3.get("error") and not r3["message"]["reasoning"] and not r3.get("reasoning_tokens"):
                q["thinking_off"] = cand
                log("  thinking off: %s" % json.dumps(cand))
                break
        else:
            log("  thinking off: none of the known switches worked")
    status, _, _ = client.get_json(prov, "/responses")
    q["responses_api"] = status not in (404, None)
    return q


def add(pid, base_url, key_env=None, name=None, log=print):
    """Create providers/<pid>.toml from what the provider says about itself."""
    config.load_env()
    key_env = key_env or re.sub(r"[^A-Z0-9]", "_", pid.upper()) + "_API_KEY"
    prov = config.Provider(id=pid, name=name or pid.title(), base_url=base_url.rstrip("/"), key_env=key_env)
    if not prov.key:
        raise SystemExit("save the key first: ./pb keys set %s (or %s=... in ~/.providerbench/.env)" % (pid, key_env))
    status, doc, text = client.get_json(prov, "/models")
    if status != 200 or not doc:
        raise SystemExit("GET %s/models -> %s %s" % (prov.base_url, status, text[:200]))
    items = doc.get("data", doc) if isinstance(doc, dict) else doc
    ids = [m["id"] for m in items if isinstance(m, dict) and m.get("id")]
    log("%d models at %s/models" % (len(ids), prov.base_url))
    canon = config.load_models()
    models, notes = {}, []
    for mid in ids:
        c = config.match_canonical(mid, canon)
        if c and c not in models:
            models[c] = mid
            log("  %-22s <- %s" % (c, mid))
    unmatched = [m for m in ids if m not in models.values()]
    if unmatched:
        notes.append("Not matched to models.toml (run them as %s:<id>, or add aliases): %s"
                     % (pid, ", ".join(unmatched[:15]) + (" ..." if len(unmatched) > 15 else "")))
    prov.models = models
    quirks, thinking_off = {}, {}
    for c, mid in list(models.items())[:3]:
        log("probing %s (%s)" % (c, mid))
        q = probe(prov, mid, log)
        if "thinking_off" in q:
            thinking_off[c] = q.pop("thinking_off")
        q.pop("error", None)
        quirks.update({k: v for k, v in q.items() if k in ("stream_usage", "cached_tokens", "responses_api")})
    d = {"name": prov.name, "base_url": prov.base_url, "key_env": key_env, "models": models,
         "thinking_off": thinking_off, "quirks": quirks,
         "pricing": [{"source": "models_endpoint"}, {"source": "openrouter", "provider": prov.name},
                     {"source": "manual"}]}
    path = write_toml(pid, d, notes)
    log("wrote %s" % path.relative_to(ROOT))
    return path


def check(pid, log=print):
    config.load_env()
    prov = config.load_providers().get(pid)
    if not prov:
        raise SystemExit("no providers/%s.toml" % pid)
    if not prov.has_key():
        raise SystemExit("set %s first" % prov.key_env)
    status, doc, text = client.get_json(prov, "/models")
    served = set()
    if status == 200 and doc:
        served = {m.get("id") for m in (doc.get("data", doc) if isinstance(doc, dict) else doc) if isinstance(m, dict)}
    ok = True
    for c, mid in prov.models.items():
        listed = "listed" if mid in served else ("not in /models" if served else "/models unavailable")
        log("%s -> %s (%s)" % (c, mid, listed))
        q = probe(prov, mid, log)
        ok = ok and "error" not in q
    return ok


def audit(log=print):
    """For every provider whose /models is readable: canonical models it serves but does not map,
    mapped ids it no longer lists, and ids that match nothing in models.toml."""
    config.load_env()
    canon = config.load_models()
    for prov in config.load_providers().values():
        if prov.key_env and not prov.key and not any(s.get("public") for s in prov.pricing):
            status, doc, _ = (None, None, "")
            try:
                status, doc, _ = client.get_json(prov, "/models")
            except RuntimeError:
                pass
            if status != 200:
                log("%-11s skipped: no key (set %s)" % (prov.id, prov.key_env))
                continue
        else:
            status, doc, text = client.get_json(prov, "/models")
            if status != 200 or not doc:
                log("%-11s /models unavailable (%s)" % (prov.id, status))
                continue
        items = doc.get("data", doc) if isinstance(doc, dict) else doc
        ids = [m["id"] for m in items if isinstance(m, dict) and m.get("id")]
        missing, other = [], []
        for mid in ids:
            c = config.match_canonical(mid, canon)
            if c and c not in prov.models:
                missing.append('"%s" = "%s"' % (c, mid))
            elif not c:
                other.append(mid)
        gone = [c for c, mid in prov.models.items() if mid not in ids]
        log("%-11s %d models listed, %d mapped" % (prov.id, len(ids), len(prov.models) - len(gone)))
        for line in missing:
            log("    add to [models]:  %s" % line)
        for c in gone:
            log("    not listed any more: %s = %s" % (c, prov.models[c]))
        if other:
            log("    not in models.toml: %s%s" % (", ".join(other[:12]), " ..." if len(other) > 12 else ""))
