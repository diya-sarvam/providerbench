"""Summaries and the plain-language verdict."""
from . import failures


def _pct(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


MATCHES = ("exact", "same_action", "same_tool", "other_tool", "no_call", "text")


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _cache(recs):
    """Measured hit rate: cached / prompt tokens, over requests where the provider reports caching."""
    rep = [r for r in recs if r.get("cache_reported") and r.get("prompt_tokens")]
    if not rep:
        return None
    return sum(r.get("cached_tokens") or 0 for r in rep) / sum(r["prompt_tokens"] for r in rep)


def _cache_possible(recs):
    """Best achievable hit rate: tokens already prefilled earlier in the run / prompt tokens."""
    rep = [r for r in recs if r.get("cache_possible_tokens") is not None and r.get("prompt_tokens")]
    if not rep:
        return None
    return sum(r["cache_possible_tokens"] for r in rep) / sum(r["prompt_tokens"] for r in rep)


def _cache_reuse(recs):
    """Share of the cacheable prefix the provider actually served from cache (capped at 1)."""
    rep = [r for r in recs if r.get("cache_reported") and r.get("cache_possible_tokens")]
    poss = sum(r["cache_possible_tokens"] for r in rep)
    if not poss:
        return None
    return min(1.0, sum(min(r.get("cached_tokens") or 0, r["cache_possible_tokens"]) for r in rep) / poss)


def _by_turn(recs):
    out = {}
    for r in recs:
        out.setdefault(min(r.get("turn", 0), 9), []).append(r)
    return out


def summarize(records):
    if not records:
        return None
    graded_recs = [r for r in records if r.get("graded", True)]
    passed = sum(r["verdict"] == "pass" for r in graded_recs)
    failed = sum(r["verdict"] == "fail" for r in graded_recs)
    errored = sum(r["verdict"] == "error" for r in records)
    skipped = sum(r["verdict"] == "skipped" for r in records)
    sent = [r for r in records if r["verdict"] != "skipped"]
    ok = [r for r in records if not r.get("error")]
    by_suite = {}
    for r in records:
        by_suite.setdefault(r["suite"], []).append(r)
    suites = {}
    for name, rs in by_suite.items():
        g = rs[0].get("graded", True)
        sp = sum(r["verdict"] == "pass" for r in rs)
        sf = sum(r["verdict"] == "fail" for r in rs)
        rok = [r for r in rs if not r.get("error")]
        s = {"n": len(rs), "graded": g, "passed": sp, "failed": sf,
             "errored": sum(r["verdict"] == "error" for r in rs),
             "skipped": sum(r["verdict"] == "skipped" for r in rs),
             "accuracy": (sp / (sp + sf) if sp + sf else None) if g else None,
             "isl_mean": _mean([r.get("prompt_tokens") for r in rok]),
             "osl_mean": _mean([r.get("completion_tokens") for r in rok]),
             "cache_pct": _cache(rok),
             "cache_possible_pct": _cache_possible(rok),
             "cache_reuse_pct": _cache_reuse(rok),
             "ttft_p50": _pct([r.get("ttft_ms") for r in rok], .5),
             "ttft_p90": _pct([r.get("ttft_ms") for r in rok], .9),
             "tps_p50": _pct([r.get("tps") for r in rok], .5)}
        scores = [r["score"] for r in rs if r.get("score") is not None]
        s["score"] = sum(scores) / len(scores) if scores else None
        suites[name] = s
    fails = {}
    for r in records:
        for c in r.get("codes") or []:
            fails[c] = fails.get(c, 0) + 1
    warm = [r for r in ok if r.get("turn", 0) >= 1]
    cache_reported = any(r.get("cache_reported") for r in ok)
    costs = [r.get("cost_usd") for r in records if r.get("cost_usd") is not None]
    cost = sum(costs) if costs else None
    tok = lambda k: sum(r.get(k) or 0 for r in ok)  # noqa: E731
    spans = [(r["t"], r["t"] + r["e2e_ms"] / 1000) for r in ok if r.get("t") and r.get("e2e_ms")]
    wall = (max(e for _, e in spans) - min(s for s, _ in spans)) if spans else None
    return {
        "n": len(records), "passed": passed, "failed": failed, "errored": errored, "skipped": skipped,
        "accuracy": passed / (passed + failed) if graded_recs and passed + failed else None,
        "by_suite": suites,
        "failures": dict(sorted(fails.items(), key=lambda kv: -kv[1])),
        "ttft_ms": {"p50": _pct([r.get("ttft_ms") for r in ok], .5), "p90": _pct([r.get("ttft_ms") for r in ok], .9)},
        "ttft_cold_ms": {"p50": _pct([r.get("ttft_ms") for r in ok if r.get("turn", 0) == 0], .5)},
        "ttft_warm_ms": {"p50": _pct([r.get("ttft_ms") for r in warm], .5)},
        "tps": {"p50": _pct([r.get("tps") for r in ok], .5)},
        "e2e_ms": {"p50": _pct([r.get("e2e_ms") for r in ok], .5)},
        "isl_mean": _mean([r.get("prompt_tokens") for r in ok]),
        "osl_mean": _mean([r.get("completion_tokens") for r in ok]),
        "throughput_tps": tok("completion_tokens") / wall if wall else None,
        "cache_pct": _cache(ok),
        "cache_possible_pct": _cache_possible(ok),
        "cache_reuse_pct": _cache_reuse(ok),
        "cache_by_turn": {str(k): {"n": len(v), "hit": _cache(v), "possible": _cache_possible(v)}
                          for k, v in sorted(_by_turn(ok).items())},
        "cache_reported": cache_reported,
        "tokens": {"prompt": tok("prompt_tokens"), "cached": tok("cached_tokens"),
                   "completion": tok("completion_tokens"), "reasoning": tok("reasoning_tokens")},
        "cost_usd": cost,
        "cost_per_task_usd": cost / len(sent) if cost is not None and sent else None,   # skipped requests cost nothing
        "cost_per_correct_usd": cost / passed if cost is not None and passed and graded_recs else None,
        "retries": sum(r.get("retries") or 0 for r in records),
    }


def _same(pa, na, pb, nb):
    """True when two pass rates are within ~2 standard errors: too close to call at this size."""
    if not na or not nb:
        return True
    p = (pa * na + pb * nb) / (na + nb)
    se = (p * (1 - p) * (1 / na + 1 / nb)) ** 0.5
    return abs(pa - pb) <= max(2 * se, 1e-9)


def _ratio_phrase(a, b, lower_better):
    """'1.8x faster' style comparison of two positive numbers; None when too close to call."""
    if not a or not b:
        return None
    better, worse = (a, b) if (a < b) == lower_better else (b, a)
    r = max(better, worse) / min(better, worse)
    return r if r >= 1.1 else None


def money(x):
    """$1.20, $0.042, $0.00041: two significant digits below a cent."""
    if x is None:
        return "—"
    if x >= 0.995:
        return "$%.2f" % x
    if x >= 0.01:
        return "$%.3f" % x
    if x <= 0:
        return "$0"
    import math
    return "$%.*f" % (1 - int(math.floor(math.log10(x))), x)


def verdict(targets):
    """targets: [(name, summary)]. A few short sentences, most important first."""
    t = [(n, s) for n, s in targets if s]
    if len(t) < 2:
        return []
    (na, a), (nb, b) = t[0], t[1]
    out = []

    if a["accuracy"] is not None and b["accuracy"] is not None:
        ga, gb = a["passed"] + a["failed"], b["passed"] + b["failed"]
        if _same(a["accuracy"], ga, b["accuracy"], gb):
            out.append("Quality is level: %d/%d vs %d/%d is within noise at this sample size." % (a["passed"], ga, b["passed"], gb))
        else:
            w, l = ((na, a, ga), (nb, b, gb)) if a["accuracy"] > b["accuracy"] else ((nb, b, gb), (na, a, ga))
            out.append("%s got more right: %d/%d vs %d/%d." % (w[0], w[1]["passed"], w[2], l[1]["passed"], l[2]))
        key = "cost_per_correct_usd"
        what = "per correct task"
    else:
        key, what = "cost_per_task_usd", "per request"

    ca, cb = a.get(key), b.get(key)
    if ca and cb:
        cheaper, dear = ((na, ca), (nb, cb)) if ca < cb else ((nb, cb), (na, ca))
        pct = (1 - cheaper[1] / dear[1]) * 100
        if pct >= 5:
            out.append("%s costs %d%% less %s (%s vs %s)." % (cheaper[0], round(pct), what, money(cheaper[1]), money(dear[1])))
        else:
            out.append("Cost %s is about even (%s vs %s)." % (what, money(ca), money(cb)))

    ra, rb = a.get("cache_reuse_pct"), b.get("cache_reuse_pct")
    if ra is not None and rb is not None and abs(ra - rb) >= 0.05:
        w, l = ((na, ra), (nb, rb)) if ra > rb else ((nb, rb), (na, ra))
        out.append("%s reuses more of its cache: %d%% of the cacheable prefix vs %d%%." % (w[0], round(w[1] * 100), round(l[1] * 100)))

    ta, tb = a["ttft_ms"]["p50"], b["ttft_ms"]["p50"]
    r = _ratio_phrase(ta, tb, lower_better=True)
    if r:
        f = na if ta < tb else nb
        out.append("%s is %.1f× faster to first token (p50 %.2f s vs %.2f s)." % (f, r, min(ta, tb) / 1000, max(ta, tb) / 1000))

    pa, pb = a["tps"]["p50"], b["tps"]["p50"]
    r = _ratio_phrase(pa, pb, lower_better=False)
    if r:
        f = na if pa > pb else nb
        out.append("%s streams %.1f× faster (%d vs %d tok/s)." % (f, r, max(pa, pb), min(pa, pb)))

    for n, s in ((na, a), (nb, b)):
        bad = {c: k for c, k in s["failures"].items() if failures.kind(c) in ("transport", "output")}
        if bad:
            top = ", ".join("%d × %s" % (k, failures.label(c)[0].lower() + failures.label(c)[1:]) for c, k in list(bad.items())[:3])
            out.append("%s: %s." % (n, top))
    return out
