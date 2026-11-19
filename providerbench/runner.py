"""A run: the same frozen task set sent to every target at the same time.

- Tasks are sampled once per run from the suites with the run's seed (fresh seed unless given),
  frozen to results/<run>/tasks.json with a content hash, and sent byte-identical to each target.
- A per-run nonce is put at the start of the system prompt so the first request of every task is
  a cache miss on every provider, while turns of one task can still hit the cache.
- Targets run in parallel, each with `concurrency` requests in flight; tasks of one group (an
  agentic session) run in order on one worker.
- Every request is recorded: timings, usage, cost, verdict, failure codes, the raw reply.
- The run stops sending when its spend reaches budget_usd; remaining requests are recorded as
  skipped ("budget"), not hidden.
"""
import hashlib
import json
import os
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config, degen, failures, pricing, report
from .degen import extra as degen_extra
from .client import chat
from .suites import SUITES
from .suites.base import Task

ROOT = Path(__file__).resolve().parent.parent
RESULTS = config.HOME / "runs"            # private run history, outside the repo
CALIBRATION = config.HOME / "calibration.json"
RETRY_CODES = ("http_429", "http_5xx", "connection")
RETRIES = 2
SETTLE_S = 0.5             # between turns of one group, so the provider can commit its prefix cache
RUNS = {}                  # id -> Run, for runs started in this process
LOCK = threading.Lock()


def suite_params(suites):
    """{"tools": 30, "synthetic": {"n": 20, "isl": 2000}} -> {name: {"n": .., **params}} with defaults."""
    out = {}
    for name, v in (suites or {}).items():
        if name not in SUITES:
            raise ValueError("unknown suite %r" % name)
        p = dict(SUITES[name].defaults())
        p.update(v if isinstance(v, dict) else {"n": v})
        if int(p.get("n") or 0) > 0:
            out[name] = p
    return out


def build_tasks(suites, seed):
    """Deterministic: each suite samples with its own rng derived from the seed, so changing the
    count of one suite does not change the sample of another."""
    tasks = []
    params = suite_params(suites)
    for name in SUITES:
        if name not in params:
            continue
        p = dict(params[name])
        n = int(p.pop("n"))
        rng = random.Random("%s:%s" % (seed, name))
        tasks.extend(SUITES[name].load(rng, n, **p))
    return tasks


def preview(spec):
    """The exact sample a run with this spec would send, without sending anything."""
    seed = spec.get("seed")
    if seed in (None, ""):
        seed = random.SystemRandom().randrange(1, 1 << 31)
    seed = int(seed)
    tasks = build_tasks(spec["suites"], seed)
    out = {}
    for name in SUITES:
        ts = [t for t in tasks if t.suite == name]
        if ts:
            rows = SUITES[name].preview(ts)
            out[name] = {"total_groups": len(rows), "groups": rows[:100]}
    return {"seed": seed, "task_hash": task_hash(tasks), "requests": len(tasks), "suites": out}


def task_hash(tasks):
    blob = json.dumps([t.to_dict() for t in tasks], sort_keys=True).encode()
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def with_nonce(messages, nonce):
    msgs = [dict(m) for m in messages]
    tag = "Session %s." % nonce
    if msgs and msgs[0].get("role") == "system" and isinstance(msgs[0].get("content"), str):
        msgs[0]["content"] = tag + "\n\n" + msgs[0]["content"]
    else:
        msgs.insert(0, {"role": "system", "content": tag})
    return msgs


def tools_with_nonce(tools, nonce):
    """Stamp the run tag into the first tool's description too: many chat templates render tool
    schemas before the system prompt, so a tag only in the system prompt would let identical tool
    blocks be served from an earlier run's cache."""
    if not tools:
        return tools
    tools = [dict(x) for x in tools]
    fn = dict(tools[0].get("function") or {})
    fn["description"] = "Session %s. %s" % (nonce, fn.get("description") or "")
    tools[0] = dict(tools[0], function=fn)
    return tools


CACHEABLE_V = 2


def cacheable_tokens(text, prompt_tokens, prefilled, sent_at):
    """Tokens of this prompt that an earlier request in the run had already prefilled.
    prefilled: [(first_token_at, text, prompt_tokens)]. Uses the earlier request's own token count for
    the shared part (exact when the whole earlier request is a prefix, as in multi-turn sessions);
    falls back to the character share of this prompt. Never more than this prompt."""
    if not text or not prompt_tokens:
        return None
    best_l, best_tok = 0, 0
    for at, prev, prev_tok in prefilled:
        if at > sent_at:
            continue
        n = lcp(text, prev)
        if n > best_l:
            best_l = n
            best_tok = prev_tok * n / len(prev) if prev_tok and prev else prompt_tokens * n / len(text)
    return int(min(prompt_tokens, best_tok))


def render(body):
    """The request as one string in prompt order (tools, then messages), for prefix comparison."""
    parts = [json.dumps(body.get("tools") or [], sort_keys=True)]
    for m in body["messages"]:
        c = m.get("content")
        parts.append("%s\x1f%s\x1f%s" % (m.get("role"), c if isinstance(c, str) else json.dumps(c),
                                          json.dumps(m.get("tool_calls") or "", sort_keys=True)))
    return "\x1e".join(parts)


def lcp(a, b):
    """Length of the common prefix of two strings (binary search on C-speed slice compares)."""
    lo, hi = 0, min(len(a), len(b))
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if a[:mid] == b[:mid]:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _evidence(res, flags):
    """Per flag: which detector, which channel, the flagged phrase, and the detector's numbers."""
    out = []
    for f in flags:
        kind, where = f.split(":", 1)
        key = {"loop": "%s_loop", "salad": "%s_salad", "gibberish": "%s_gibberish", "foreign": "%s_foreign"}[kind] % where
        d = res.get(key) or {}
        if kind == "loop":
            info = "a %d-word phrase repeated %d times (%d words, %d%% of the tail)" % (
                d.get("period", 0), d.get("repeats", 0), d.get("span_words", 0), round(100 * (d.get("tail_ratio") or 0)))
            phrase = d.get("phrase", "")
        elif kind == "salad":
            info = "%d words in a row with almost no function words" % d.get("run", 0)
            phrase = d.get("phrase", "")
        elif kind == "gibberish":
            info = {"replacement_char": "%d broken characters (U+FFFD)" % d.get("replacement_chars", 0),
                    "cjk_injection": "%d CJK characters inside Latin text" % d.get("cjk_chars", 0)}.get(
                d.get("trigger"), "symbol soup in %d of %d windows (worst %d%% junk)" % (
                    d.get("bad_windows", 0), d.get("total_windows", 0), round(100 * (d.get("worst_ratio") or 0))))
            phrase = d.get("phrase", "")
        else:
            info = "%d %s characters (%s)" % (d.get("chars", 0), d.get("script", "?"), d.get("trigger", ""))
            phrase = d.get("sample", "")
        out.append({"flag": f, "detector": kind, "channel": where, "info": info, "phrase": (phrase or "")[:300]})
    return out


DEGEN_VERSION = 7          # bump when detectors change: stored records are re-analysed on read


def _span(text, kind, phrase, ratio=6.0):
    """Character range [start, end) of the flagged region inside `text`, for highlighting."""
    if not text:
        return None
    if kind == "entropy":                     # widest run of 400-char windows above the ratio
        hits = [i for i in range(0, max(1, len(text) - 399), 200) if degen_extra.loopiness(text[i:i + 400]) > ratio]
        return [hits[0], min(len(text), hits[-1] + 400)] if hits else [max(0, len(text) - 400), len(text)]
    if kind == "exhausted" or not phrase or not phrase.split():
        return None
    # detectors join words with single spaces; the reply may have newlines or runs of spaces
    rx = re.compile(r"\s+".join(re.escape(w) for w in phrase.split()))
    m = rx.search(text)
    if not m:
        head = phrase.split()[:4]
        m = re.compile(r"\s+".join(re.escape(w) for w in head)).search(text)
        if not m:
            return None
        return [m.start(), min(len(text), m.start() + len(phrase))]
    start, end = m.start(), m.end()
    if kind == "loop":                          # extend over the consecutive repeats
        for nxt in rx.finditer(text, end):
            if nxt.start() - end > max(8, len(phrase) // 2):
                break
            end = nxt.end()
    return [start, end]


def degeneration(messages, msg, finish_reason=None, suite=None):
    """None for suites that do not run detection (beta: trace replay only, where prompts are real tasks)."""
    if suite and not getattr(SUITES.get(suite), "degen_check", False):
        return None
    """Loop / word-salad / gibberish / language-switch detectors over content and reasoning.
    The language-switch flag is dropped when the prompt itself is not in a Latin script, so a
    reply in Hindi to a Hindi prompt is not flagged."""
    content, reasoning = msg.get("content") or "", msg.get("reasoning") or ""
    s = SUITES.get(suite) if suite else None
    extra = degen_extra.checks(content, reasoning, finish_reason, ratio=getattr(s, "entropy_ratio", 6.0),
                               length_stop_expected=getattr(s, "length_stop_expected", False))
    if not (content or reasoning):
        for e in extra:
            e["span"] = None
        return {"v": DEGEN_VERSION, "flags": [e["flag"] for e in extra], "scripts": [], "evidence": extra} if extra else None
    res = degen.analyze(content, reasoning)
    last_user = next((m.get("content") for m in reversed(messages) if m.get("role") == "user"), "") or ""
    prompt_foreign = bool(degen.script_summary(last_user if isinstance(last_user, str) else json.dumps(last_user)))
    flags = [f for f in res["flags"] if not (prompt_foreign and f.startswith("foreign:"))]
    ev = _evidence(res, flags) + extra
    ratio = getattr(s, "entropy_ratio", 6.0)
    for e in ev:
        e["span"] = _span(content if e["channel"] == "content" else reasoning, e["detector"], e.get("phrase") or "", ratio)
    return {"v": DEGEN_VERSION, "flags": flags + [e["flag"] for e in extra], "scripts": res.get("scripts") or [], "evidence": ev}


def _groups(tasks):
    groups = {}
    for t in tasks:
        groups.setdefault(t.group or t.id, []).append(t)
    return [sorted(g, key=lambda t: t.turn) for g in groups.values()]


# ------------------------------------------------------------------ estimate

def _calibration():
    try:
        return json.loads(CALIBRATION.read_text())
    except (OSError, ValueError):
        return {}


def estimate(spec, tasks=None):
    """Pre-run cost estimate in USD per target. Uses past mean output tokens when known."""
    providers = config.load_providers()
    tasks = tasks if tasks is not None else build_tasks(spec["suites"], spec.get("seed") or 0)
    cal, doc = _calibration(), pricing.load()
    out, total = [], 0.0
    for key in spec["targets"]:
        tgt = config.resolve(key, providers)
        price = pricing.price_for(tgt, doc)
        usd, prev_prompt = 0.0, {}
        for t in tasks:
            suite = SUITES[t.suite]
            prompt = int(len(json.dumps(t.messages) + json.dumps(t.tools or [])) / 3.6)
            cached = prev_prompt.get(t.group, 0) if t.turn else 0
            prev_prompt[t.group] = prompt
            outp = t.meta.get("est_output") or cal.get(tgt.slug, {}).get(t.suite) or suite.est_output_tokens
            c = pricing.cost_usd(price, prompt, cached, min(outp, t.max_tokens), doc)
            usd += c or 0
        out.append({"target": key, "tasks": len(tasks), "usd": usd if price else None, "priced": bool(price)})
        total += usd if price else 0
    return {"targets": out, "total_usd": total}


# ------------------------------------------------------------------------ run

class Run:
    def __init__(self, spec, tasks=None):
        self.spec = dict(spec, tool_tag=True)
        self.created = time.time()
        self.seed = spec.get("seed")
        if self.seed in (None, ""):
            self.seed = random.SystemRandom().randrange(1, 1 << 31)
        self.seed = int(self.seed)
        self.id = time.strftime("%Y%m%d-%H%M%S") + "-%04x" % (self.seed & 0xFFFF)
        self.dir = RESULTS / self.id
        providers = config.load_providers(include_mock=spec.get("mock", False))
        self.targets = [config.resolve(k, providers) for k in spec["targets"]]
        missing = [t.provider.name for t in self.targets if not t.provider.has_key()]
        if missing:
            raise ValueError("no API key saved for: %s" % ", ".join(missing))
        self.tasks = tasks
        self.status, self.error = "starting", None
        self.stop = False
        self.spend = 0.0
        self.prices = pricing.load()
        self.state = {t.key: {"done": 0, "total": 0, "spend_usd": 0.0, "live": {}, "records": [],
                              "paused": False, "stopped": False, "inflight": {}, "started": None, "finished": None,
                              "prefilled": []}
                      for t in self.targets}

    # -- public
    def start(self):
        with LOCK:
            RUNS[self.id] = self
        threading.Thread(target=self._main, name="run-" + self.id, daemon=True).start()
        return self

    def snapshot(self):
        targets = []
        for t in self.targets:
            st = self.state[t.key]
            price = pricing.price_for(t, self.prices)
            now = time.time()
            recs = st["records"]
            durs = [r["e2e_ms"] / 1000 for r in recs if r.get("e2e_ms")]
            left = st["total"] - st["done"]
            conc = max(1, int(self.spec.get("concurrency") or 4))
            if st["finished"]:
                tstate = "stopped" if st["stopped"] else "done"
            elif st["stopped"]:
                tstate = "stopping"
            elif st["paused"]:
                tstate = "paused" if not st["inflight"] else "pausing"
            else:
                tstate = "running" if st["started"] else "waiting"
            targets.append({
                "target": t.key, "provider": t.provider.id, "provider_name": t.provider.name,
                "model": t.model, "model_id": t.model_id, "done": st["done"], "total": st["total"],
                "state": tstate,
                "inflight": [{"task_id": k, "elapsed_s": round(now - v, 1)} for k, v in sorted(st["inflight"].items(), key=lambda kv: kv[1])],
                "elapsed_s": round((st["finished"] or now) - st["started"], 1) if st["started"] else None,
                "eta_s": round(sum(durs) / len(durs) * left / min(conc, max(left, 1))) if durs and left and tstate in ("running", "waiting") else None,
                "spend_usd": st["spend_usd"], "live": dict(st["live"]), "price": price,
                "summary": report.summarize(st["records"])})
        names = self._names()
        return {"id": self.id, "created": self.created, "status": self.status, "error": self.error,
                "spec": self.spec, "seed": self.seed, "task_hash": getattr(self, "hash", None),
                "tasks_total": len(self.tasks or []), "spend_usd": self.spend, "targets": targets,
                "verdict": report.verdict([(names[x["target"]], x["summary"]) for x in targets])}

    def _names(self):
        """Short display names: provider names when they are unique, else the full target."""
        provs = [t.provider.name for t in self.targets]
        same_model = len({t.model for t in self.targets}) == 1
        if len(set(provs)) == len(provs):
            return {t.key: t.provider.name if same_model else "%s %s" % (t.provider.name, t.model)
                    for t in self.targets}
        return {t.key: t.key for t in self.targets}

    # -- internals
    def _main(self):
        try:
            self.status = "sampling"
            if self.tasks is None:
                self.tasks = build_tasks(self.spec["suites"], self.seed)
            self.hash = task_hash(self.tasks)
            self.dir.mkdir(parents=True, exist_ok=True)
            (self.dir / "tasks.json").write_text(json.dumps(
                {"seed": self.seed, "hash": self.hash, "tasks": [t.to_dict() for t in self.tasks]}))
            for t in self.targets:
                self.state[t.key]["total"] = len(self.tasks)
            self.status = "running"
            self._save()
            threads = [threading.Thread(target=self._run_target, args=(t,), daemon=True) for t in self.targets]
            for th in threads:
                th.start()
            last = 0
            while any(th.is_alive() for th in threads):
                time.sleep(0.5)
                if time.time() - last > 5:      # progress survives a crash or restart
                    self._save()
                    last = time.time()
            self.status = "stopped" if self.stop else "done"
            self._calibrate()
        except Exception as e:  # noqa: BLE001
            self.status, self.error = "error", "%s: %s" % (type(e).__name__, e)
        self._save()

    def _run_target(self, tgt):
        self.state[tgt.key]["started"] = time.time()
        groups = _groups(self.tasks)
        conc = max(1, int(self.spec.get("concurrency") or 4))
        out = (self.dir / (tgt.slug + ".jsonl")).open("a")
        try:
            if groups and getattr(SUITES[groups[0][0].suite], "warm_first", False):
                self._run_group(tgt, groups[0], out)        # warm the shared prefix before fanning out
                groups = groups[1:]
            with ThreadPoolExecutor(conc, thread_name_prefix=tgt.slug) as pool:
                list(pool.map(lambda g: self._run_group(tgt, g, out), groups))
        finally:
            out.close()
            self.state[tgt.key]["live"] = {}
            self.state[tgt.key]["finished"] = time.time()

    def _run_group(self, tgt, group, out):
        prev_sent, prev_msg = None, None
        for i, task in enumerate(group):
            if i:
                time.sleep(SETTLE_S)
            override = None
            if (task.meta or {}).get("loop") == "closed" and i:
                if prev_sent is None:            # the previous turn failed: a closed loop cannot go on
                    rec = dict(task_id=task.id, suite=task.suite, group=task.group, turn=task.turn, meta=task.meta,
                               t=time.time(), graded=SUITES[task.suite].graded, verdict="skipped", codes=[],
                               error="previous turn failed", retries=0)
                    self._store(tgt, rec, out)
                    continue
                from .suites.session import closed_loop_messages
                override = closed_loop_messages(prev_sent, prev_msg, task.expect["request"])
            rec = self._one(tgt, task, override)
            sent = rec.pop("_sent", None)
            if override is not None:
                rec["closed_loop_added"] = override[len(prev_sent):]
            ok = not rec.get("error") and rec.get("verdict") != "skipped"
            prev_sent = sent if ok else None
            prev_msg = {"content": rec.get("content") or "", "tool_calls": rec.get("tool_calls") or []} if ok else None
            self._store(tgt, rec, out)

    def _store(self, tgt, rec, out):
        with LOCK:
            st = self.state[tgt.key]
            st["records"].append(rec)
            st["done"] += 1
            if rec.get("cost_usd"):
                st["spend_usd"] += rec["cost_usd"]
                self.spend += rec["cost_usd"]
            out.write(json.dumps(rec) + "\n")
            out.flush()

    def _one(self, tgt, task, override=None):
        base = {"task_id": task.id, "suite": task.suite, "group": task.group, "turn": task.turn,
                "meta": task.meta, "t": time.time(), "graded": SUITES[task.suite].graded}
        st = self.state[tgt.key]
        while st["paused"] and not (self.stop or st["stopped"]):
            time.sleep(0.3)                     # paused: in-flight requests finish, no new ones start
        budget = self.spec.get("budget_usd")
        if self.stop or st["stopped"] or (budget and self.spend >= float(budget)):
            stopped = self.stop or st["stopped"]
            return dict(base, verdict="skipped", codes=[] if stopped else ["budget"],
                        error="stopped" if stopped else "budget", retries=0)
        st["inflight"][task.id] = time.time()
        try:
            return self._send(tgt, task, base, override)
        finally:
            st["inflight"].pop(task.id, None)

    def control(self, target, action):
        st = self.state.get(target)
        if st is None:
            raise ValueError("no target %s in this run" % target)
        if action == "pause":
            st["paused"] = True
        elif action == "resume":
            st["paused"] = False
        elif action == "stop":
            st["stopped"], st["paused"] = True, False
        else:
            raise ValueError("action must be pause, resume or stop")

    def _send(self, tgt, task, base, override=None):
        body = {"messages": override if override is not None else with_nonce(task.messages, self.id),
                "max_tokens": task.max_tokens}
        limit = (task.expect or {}).get("context_limit")
        if limit:                                # keep prompt + output inside the context window
            room = int(limit - len(json.dumps(body["messages"])) / 3.6 - 512)
            body["max_tokens"] = max(1024, min(task.max_tokens, room))
        if task.tools:
            body["tools"] = tools_with_nonce(task.tools, self.id)
        for k in ("temperature", "top_p"):
            if self.spec.get(k) is not None:
                body[k] = self.spec[k]
        body = config.deep_merge(body, task.extra_body)
        body = config.deep_merge(body, tgt.provider.body_for(tgt.model, self.spec.get("thinking", "default")))
        # how much of this prompt could be served from cache: the longest prefix this provider has
        # already prefilled in this run (requests whose first token arrived before this one is sent)
        text, sent_at = render(body), time.time()
        st = self.state[tgt.key]
        prefilled_now = list(st["prefilled"])
        retries, reply = 0, None
        while True:
            # each request streams into its own dict; the UI shows the most recently started one
            live = {"task_id": task.id, "suite": task.suite, "thinking": "", "answer": ""}
            self.state[tgt.key]["live"] = live
            reply = chat(tgt.provider, tgt.model_id, body, session="%s-%s" % (self.id, task.group or task.id),
                         live=live, stop=lambda: self.stop or self.state[tgt.key]["stopped"])
            if reply.get("code") in RETRY_CODES and retries < RETRIES and not (self.stop or self.state[tgt.key]["stopped"]):
                retries += 1
                time.sleep(2 * 3 ** (retries - 1))
                continue
            break
        possible = cacheable_tokens(text, reply.get("prompt_tokens"), prefilled_now, sent_at)
        if reply.get("ttft_ms") is not None:
            st["prefilled"].append((sent_at + reply["ttft_ms"] / 1000, text, reply.get("prompt_tokens")))
        price = pricing.price_for(tgt, self.prices)
        cost = pricing.cost_usd(price, reply.get("prompt_tokens"), reply.get("cached_tokens"),
                                reply.get("completion_tokens"), self.prices) if reply.get("completion_tokens") else None
        if reply.get("error"):
            v = {"verdict": "error", "codes": [reply.get("code") or "connection"], "detail": reply["error"][:500],
                 "answer": "", "expected": "", "score": None}
        else:
            try:
                v = SUITES[task.suite].grade(task, reply).to_dict()
            except Exception as e:  # noqa: BLE001 - a grader bug must not kill the run
                v = {"verdict": "fail", "codes": ["no_answer"], "detail": "grader error: %s" % e,
                     "answer": "", "expected": "", "score": None}
        msg = reply.get("message") or {}
        degen = self._degeneration(task, msg, reply.get("finish_reason")) if not reply.get("error") else None
        if degen and degen["flags"]:
            v["codes"] = sorted(set((v.get("codes") or []) + ["degenerate"]))
            v["verdict"] = "fail"
            why = "degenerate: " + ", ".join(degen["flags"])
            v["detail"] = (v.get("detail") + "; " + why) if v.get("detail") else why
        return dict(base, **v, degen=degen, _sent=body["messages"], retries=retries, status=reply.get("status"), error=reply.get("error"),
                    finish_reason=reply.get("finish_reason"),
                    **{k: reply.get(k) for k in ("ttft_ms", "ttfa_ms", "thinking_ms", "e2e_ms", "tps", "connect_ms",
                                                 "prompt_tokens", "cached_tokens", "completion_tokens",
                                                 "reasoning_tokens", "usage_reported", "cache_reported",
                                                 "reasoning_estimated")},
                    cost_usd=cost, cache_possible_tokens=possible, cache_possible_v=CACHEABLE_V, content=msg.get("content", ""), reasoning=msg.get("reasoning", ""),
                    tool_calls=msg.get("tool_calls") or [])

    @staticmethod
    def _degeneration(task, msg, finish_reason=None):
        return degeneration(task.messages, msg, finish_reason, task.suite)

    def _calibrate(self):
        cal = _calibration()
        for t in self.targets:
            per = {}
            for r in self.state[t.key]["records"]:
                if r.get("completion_tokens"):
                    per.setdefault(r["suite"], []).append(r["completion_tokens"])
            for suite, xs in per.items():
                old = cal.setdefault(t.slug, {}).get(suite)
                new = sum(xs) / len(xs)
                cal[t.slug][suite] = int(new if old is None else 0.5 * old + 0.5 * new)
        CALIBRATION.parent.mkdir(parents=True, exist_ok=True)
        CALIBRATION.write_text(json.dumps(cal, indent=1, sort_keys=True))

    def _save(self):
        if not self.dir.exists():
            return
        snap = self.snapshot()
        for t in snap["targets"]:
            t["live"] = {}
        (self.dir / "run.json").write_text(json.dumps(snap, indent=1))


# ------------------------------------------------------------- stored runs

def list_runs():
    out = []
    for d in sorted(RESULTS.glob("*/run.json"), reverse=True):
        try:
            snap = json.loads(d.read_text())
        except ValueError:
            continue
        live = RUNS.get(snap["id"])
        if live:
            snap = live.snapshot()
        if all(t["target"].startswith("mock-") for t in snap["targets"]) and os.environ.get("PB_MOCK") != "1":
            continue                         # runs against the fake providers only show in --mock mode
        out.append({"id": snap["id"], "created": snap["created"], "status": snap["status"],
                    "targets": [t["target"] for t in snap["targets"]], "suites": snap["spec"]["suites"],
                    "headline": (snap.get("verdict") or [""])[0]})
    return out


_SNAP_CACHE = {}
_REC_CACHE = {}


def _backfill_cache_possible(run_id, recs):
    """Recompute cache_possible_tokens with the current method (runs recorded before it, or with an
    older estimate), from the frozen requests and the recorded timings, as the runner does live."""
    if all(r.get("cache_possible_v") == CACHEABLE_V for r in recs if r.get("prompt_tokens")):
        return recs
    tasks = _tasks_of(run_id)
    snap_path = RESULTS / run_id / "run.json"
    try:
        stamped = json.loads(snap_path.read_text()).get("spec", {}).get("tool_tag", False)
    except (OSError, ValueError):
        stamped = False
    pre = []
    for r in sorted(recs, key=lambda r: r.get("t") or 0):
        task = tasks.get(r["task_id"])
        if not task or not r.get("prompt_tokens") or r.get("ttft_ms") is None:
            continue
        if (task.get("meta") or {}).get("loop") == "closed" and task.get("turn"):
            msgs = None                    # closed loop: request depends on the previous reply
        else:
            msgs = with_nonce(task["messages"], run_id)
        if msgs is None:
            continue
        tools = tools_with_nonce(task.get("tools"), run_id) if stamped else task.get("tools")
        text = render({"messages": msgs, "tools": tools})
        r["cache_possible_tokens"] = cacheable_tokens(text, r["prompt_tokens"], pre, r["t"])
        r["cache_possible_v"] = CACHEABLE_V
        pre.append((r["t"] + r["ttft_ms"] / 1000, text, r["prompt_tokens"]))
    return recs


def _regrade(run_id, recs):
    """Re-grade stored replies with today's graders, so history follows the current rules
    (e.g. trace replay stopped scoring agreement with the recorded agent)."""
    tasks = _tasks_of(run_id)
    for r in recs:
        suite = SUITES.get(r.get("suite"))
        if not suite:
            continue
        r["graded"] = suite.graded
        if r.get("error") or r.get("verdict") == "skipped":
            continue
        task = tasks.get(r["task_id"])
        if not task:
            continue
        reply = {"message": {"content": r.get("content") or "", "reasoning": r.get("reasoning") or "",
                             "tool_calls": r.get("tool_calls") or []},
                 "finish_reason": r.get("finish_reason"), "completion_tokens": r.get("completion_tokens")}
        try:
            v = suite.grade(Task.from_dict(task), reply).to_dict()
        except Exception:  # noqa: BLE001 - keep the stored verdict if a grader cannot handle an old record
            continue
        r.update(v)
        if (r.get("degen") or {}).get("flags"):
            r["codes"] = sorted(set(r["codes"] + ["degenerate"]))
            r["verdict"] = "fail"
            why = "degenerate: " + ", ".join(r["degen"]["flags"])
            r["detail"] = (r["detail"] + "; " + why) if r.get("detail") else why
    return recs


def _load_records(run_id, path):
    if not path.exists():
        return []
    recs = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return _regrade(run_id, _backfill_degen(run_id, _backfill_cache_possible(run_id, recs)))


def _backfill_degen(run_id, recs):
    """Runs recorded before the detectors were wired in: run them on the stored replies."""
    if all((r.get("degen") or {}).get("v") == DEGEN_VERSION or r.get("error")
           or ("degen" in r and not getattr(SUITES.get(r.get("suite")), "degen_check", False)) for r in recs):
        return recs
    tasks = _tasks_of(run_id)
    for r in recs:
        if (r.get("degen") or {}).get("v") == DEGEN_VERSION or r.get("error"):
            continue
        if not getattr(SUITES.get(r.get("suite")), "degen_check", False):
            r["degen"] = None                    # detection does not run on this suite (synthetic, quality)
            continue
        task = tasks.get(r["task_id"])
        r["degen"] = degeneration(task["messages"] if task else [], {"content": r.get("content"), "reasoning": r.get("reasoning")},
                                  r.get("finish_reason"), r.get("suite"))
        if r["degen"] and r["degen"]["flags"]:
            r["codes"] = sorted(set((r.get("codes") or []) + ["degenerate"]))
            r["verdict"] = "fail"
    return recs


def get_run(run_id):
    if run_id in RUNS:
        return RUNS[run_id].snapshot()
    d = RESULTS / run_id
    path = d / "run.json"
    if not path.exists():
        return None
    stamp = tuple(sorted((f.name, f.stat().st_mtime) for f in d.iterdir()))
    hit = _SNAP_CACHE.get(run_id)
    if hit and hit[0] == stamp:
        return hit[1]
    snap = json.loads(path.read_text())
    if snap["status"] in ("running", "sampling", "starting"):
        snap["status"] = "interrupted"     # the process that ran it is gone
    # summaries are recomputed from the stored records, so report fixes apply to old runs too
    names = {}
    for tg in snap["targets"]:
        f = d / ("%s.jsonl" % _slug(snap, tg["target"]))
        if f.exists():
            recs = _load_records(run_id, f)
            tg["summary"] = report.summarize(recs)
            tg["summary"]["review"] = review_counts(run_id, tg["target"], recs)
            tg["done"] = len(recs)
        names[tg["target"]] = tg.get("provider_name") or tg["target"]
    if len(set(names.values())) < len(names):
        names = {k: k for k in names}
    snap["verdict"] = report.verdict([(names[x["target"]], x["summary"]) for x in snap["targets"]])
    _SNAP_CACHE[run_id] = (stamp, snap)
    return snap


def records(run_id, target, only=None, suite=None, q=None, offset=0, limit=None):
    if suite or q or offset or limit:
        recs = _all_records(run_id, target)
        if only == "fail":
            recs = [r for r in recs if r["verdict"] in ("fail", "error")]
        if suite:
            recs = [r for r in recs if r["suite"] == suite]
        if q:
            recs = [r for r in recs if q.lower() in r["task_id"].lower() or q in (r.get("codes") or [])]
        total = len(recs)
        recs = recs[int(offset or 0): int(offset or 0) + int(limit or 200)]
        return {"total": total, "records": [dict(r, content=(r.get("content") or "")[:600],
                                                   reasoning=(r.get("reasoning") or "")[:300]) for r in recs]}
    run = RUNS.get(run_id)
    if run:
        tgt = next((t for t in run.targets if t.key == target), None)
        recs = list(run.state[target]["records"]) if tgt else []
    else:
        snap = get_run(run_id)
        if not snap:
            return []
        if not any(t["target"] == target for t in snap["targets"]):
            return []
        path = RESULTS / run_id / ("%s.jsonl" % _slug(snap, target))
        recs = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    if only == "fail":
        recs = [r for r in recs if r["verdict"] in ("fail", "error")]
    trimmed = []
    for r in recs:
        r = dict(r)
        for k in ("content", "reasoning"):
            r[k] = (r.get(k) or "")[:4000]
        trimmed.append(r)
    return trimmed


def _slug(snap, target):
    t = next(t for t in snap["targets"] if t["target"] == target)
    return re.sub(r"[^A-Za-z0-9._-]+", "_", "%s__%s" % (t["provider"], t["model"]))


def stop(run_id):
    run = RUNS.get(run_id)
    if run:
        run.stop = True
        return True
    return False


def replay_spec(run_id):
    """Spec and frozen tasks of a past run, to send the exact same requests again."""
    snap = get_run(run_id)
    doc = json.loads((RESULTS / run_id / "tasks.json").read_text())
    return dict(snap["spec"], seed=doc["seed"]), [Task.from_dict(t) for t in doc["tasks"]]


# ------------------------------------------------------------------- traces

_TASK_CACHE = {}


def _tasks_of(run_id):
    path = RESULTS / run_id / "tasks.json"
    key = (run_id, path.stat().st_mtime if path.exists() else 0)
    if key not in _TASK_CACHE:
        _TASK_CACHE.clear()
        doc = json.loads(path.read_text()) if path.exists() else {"tasks": []}
        _TASK_CACHE[key] = {t["id"]: t for t in doc["tasks"]}
    return _TASK_CACHE[key]


def _all_records(run_id, target):
    run = RUNS.get(run_id)
    if run and target in run.state:
        return list(run.state[target]["records"])
    snap = get_run(run_id)
    if not snap or not any(t["target"] == target for t in snap["targets"]):
        return []
    key = (run_id, target)
    path = RESULTS / run_id / ("%s.jsonl" % _slug(snap, target))
    stamp = (path.stat().st_mtime, (RESULTS / run_id / "reviews.json").exists()) if path.exists() else None
    hit = _REC_CACHE.get(key)
    if hit and hit[0] == stamp:
        return hit[1]
    recs = _load_records(run_id, path)
    _REC_CACHE[key] = (stamp, recs)
    return recs


def trace(run_id, target, task_id):
    """One request in full: exactly what was sent (minus the API key) and everything that came back."""
    rec = next((r for r in _all_records(run_id, target) if r["task_id"] == task_id), None)
    task = _tasks_of(run_id).get(task_id)
    if rec is None and task is None:
        return None
    snap = get_run(run_id) or {}
    out = {"run": run_id, "target": target, "record": rec}
    if task:
        tg = next((t for t in snap.get("targets", []) if t["target"] == target), {})
        sent = (_closed_messages(run_id, target, task, _tasks_of(run_id))
                if (task.get("meta") or {}).get("loop") == "closed" and task.get("turn") else with_nonce(task["messages"], run_id))
        body = {"model": tg.get("model_id"), "messages": sent,
                "max_tokens": task["max_tokens"], "stream": True}
        if task.get("tools"):
            body["tools"] = tools_with_nonce(task["tools"], run_id) if (snap.get("spec") or {}).get("tool_tag") else task["tools"]
        body.update(task.get("extra_body") or {})
        try:
            prov = config.load_providers().get(tg.get("provider"))
            body = config.deep_merge(body, prov.body_for(tg.get("model"), (snap.get("spec") or {}).get("thinking", "default")))
            out["url"] = prov.url(tg.get("model_id") or "")
        except Exception:  # noqa: BLE001 - provider file may have changed since
            pass
        out["request"] = body
        out["expect"] = task.get("expect")
    return out


def export_zip(run_id):
    """run.json, tasks.json and every target's records, as one zip (bytes)."""
    import io
    import zipfile
    d = RESULTS / run_id
    if not d.exists():
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(d.iterdir()):
            if f.suffix in (".json", ".jsonl"):
                z.write(f, "%s/%s" % (run_id, f.name))
        z.writestr("%s/README.txt" % run_id, (
            "providerbench run %s\n\n"
            "run.json      spec, seed, task hash, per-target summary\n"
            "tasks.json    the frozen sample: every request's messages, tools and grading data\n"
            "*.jsonl       one line per request and target: timings, usage, cost, verdict, the reply\n"
            "The run tag 'Session <run id>.' was prepended to each system prompt when sent.\n"
            "No API keys or headers are stored.\n") % run_id)
    return buf.getvalue()


# --------------------------------------------------------------- human review of flags

LABELS = ("degenerate", "false_positive", "missed")   # missed = a false negative a human found


def _reviews_path(run_id):
    return RESULTS / run_id / "reviews.json"


def reviews(run_id):
    try:
        return json.loads(_reviews_path(run_id).read_text())
    except (OSError, ValueError):
        return {}


def set_review(run_id, target, task_id, label, note="", spans=None):
    """label: degenerate | false_positive | missed | None (clear). Stored per run, locally.
    spans: optional human-marked ranges [{"channel": "content"|"reasoning", "start": int, "end": int}]."""
    if label not in LABELS + (None,):
        raise ValueError("label must be degenerate, false_positive, missed or null")
    clean = []
    for sp in spans or []:
        if sp.get("channel") in ("content", "reasoning") and int(sp.get("end", 0)) > int(sp.get("start", 0)) >= 0:
            clean.append({"channel": sp["channel"], "start": int(sp["start"]), "end": int(sp["end"])})
    if not (RESULTS / run_id).exists():
        raise ValueError("no such run")
    with LOCK:
        doc = reviews(run_id)
        key = "%s|%s" % (target, task_id)
        if label is None:
            doc.pop(key, None)
        else:
            doc[key] = {"label": label, "note": (note or "")[:500], "at": time.time(), "spans": clean[:20]}
        _reviews_path(run_id).write_text(json.dumps(doc, indent=1))
    _SNAP_CACHE.pop(run_id, None)
    return doc.get(key)


def review_counts(run_id, target, recs):
    doc = reviews(run_id)
    flagged = [r for r in recs if (r.get("degen") or {}).get("flags")]
    lab = [doc.get("%s|%s" % (target, r["task_id"]), {}).get("label") for r in flagged]
    missed = sum(1 for k, v in doc.items() if k.startswith(target + "|") and v.get("label") == "missed")
    low = sum(1 for r in flagged if getattr(SUITES.get(r["suite"]), "degen_signal", "high") == "low")
    return {"flagged": len(flagged), "confirmed": lab.count("degenerate"),
            "false_positive": lab.count("false_positive"), "pending": lab.count(None), "missed": missed,
            "low_signal": low, "real_tasks": len(flagged) - low}


def _context(text, phrase, width=500):
    """The flagged phrase with some text around it, for review."""
    if not text:
        return {"before": "", "match": "", "after": ""}
    i = text.find(phrase[:80]) if phrase else -1
    if i < 0:
        tail = text[-2 * width:]
        return {"before": tail, "match": "", "after": ""}
    j = i + len(phrase)
    return {"before": ("…" if i > width else "") + text[max(0, i - width):i], "match": text[i:j],
            "after": text[j:j + width] + ("…" if j + width < len(text) else "")}


def flags(run_id=None, status=None):
    """Flagged replies with their evidence and review label, newest run first.
    status: pending | degenerate | false_positive | None (all)."""
    out = []
    ids = [run_id] if run_id else [r["id"] for r in list_runs()]
    for rid in ids:
        snap = get_run(rid)
        if not snap:
            continue
        doc = reviews(rid)
        for tg in snap["targets"]:
            for r in _all_records(rid, tg["target"]):
                if not getattr(SUITES.get(r.get("suite")), "degen_check", False):
                    continue
                d = r.get("degen") or {}
                rv = doc.get("%s|%s" % (tg["target"], r["task_id"]))
                if not d.get("flags") and not (rv and rv.get("label") == "missed"):
                    continue
                label = rv["label"] if rv else None
                if status and (label or "pending") != status:
                    continue
                ev = []
                for e in d.get("evidence") or [{"flag": f, "channel": f.split(":")[1], "phrase": "", "info": ""} for f in d.get("flags") or []]:
                    text = r.get("content") if e["channel"] == "content" else r.get("reasoning")
                    sp = e.get("span")
                    ctx = ({"before": ("…" if sp[0] > 500 else "") + (text or "")[max(0, sp[0] - 500):sp[0]],
                            "match": (text or "")[sp[0]:sp[1]][:3000] + ("…" if sp[1] - sp[0] > 3000 else ""),
                            "after": (text or "")[sp[1]:sp[1] + 500] + ("…" if sp[1] + 500 < len(text or "") else "")}
                           if sp else _context(text or "", e.get("phrase") or ""))
                    ev.append(dict(e, context=ctx))
                for sp in (rv or {}).get("spans") or []:      # human-marked ranges (false negatives)
                    text = (r.get("content") if sp["channel"] == "content" else r.get("reasoning")) or ""
                    ev.append({"flag": "human:" + sp["channel"], "detector": "human", "channel": sp["channel"],
                               "info": "marked by a reviewer", "phrase": text[sp["start"]:sp["end"]][:300], "span": [sp["start"], sp["end"]],
                               "context": {"before": text[max(0, sp["start"] - 500):sp["start"]], "match": text[sp["start"]:sp["end"]][:3000],
                                           "after": text[sp["end"]:sp["end"] + 500]}})
                suite = SUITES.get(r["suite"])
                task = _tasks_of(rid).get(r["task_id"]) or {}
                msgs = task.get("messages") or []
                last_user = next((m.get("content") for m in reversed(msgs) if m.get("role") == "user"), "") or ""
                last_user = last_user if isinstance(last_user, str) else json.dumps(last_user, ensure_ascii=False)
                system = next((m.get("content") for m in msgs if m.get("role") == "system"), "") or ""
                prompt = {"scenario": getattr(suite, "scenario", ""), "suite_title": getattr(suite, "title", r["suite"]),
                          "signal": getattr(suite, "degen_signal", "high"), "note": getattr(suite, "degen_note", ""),
                          "system": (system if isinstance(system, str) else "")[:800],
                          "last_user": last_user[-3000:], "last_user_chars": len(last_user),
                          "messages": len(msgs), "label": (task.get("meta") or {}).get("label")}
                out.append({"run": rid, "created": snap["created"], "target": tg["target"], "prompt": prompt,
                            "signal": prompt["signal"],
                            "provider_name": tg.get("provider_name"), "model": tg.get("model"),
                            "task_id": r["task_id"], "suite": r["suite"], "flags": d.get("flags") or [], "evidence": ev,
                            "label": label, "note": (rv or {}).get("note", ""), "spans": (rv or {}).get("spans") or [],
                            "completion_tokens": r.get("completion_tokens"), "finish_reason": r.get("finish_reason")})
    out.sort(key=lambda f: (f["signal"] != "high", -f["created"]))
    return out


def export_labels():
    """Every human-labelled flag as JSONL lines, to tune the detectors. Stays local."""
    rows = []
    for f in flags():
        if f["label"]:
            for e in f["evidence"]:
                rows.append({"label": f["label"], "flag": e["flag"], "info": e.get("info"), "phrase": e.get("phrase"),
                             "context": e["context"]["before"][-300:] + e["context"]["match"] + e["context"]["after"][:300],
                             "model": f["model"], "provider": f["target"].split(":")[0], "run": f["run"],
                             "task_id": f["task_id"], "note": f["note"]})
    return rows


# --------------------------------------------------------------- compare and readable exports

def _closed_messages(run_id, target, task, tasks):
    """A closed-loop turn's real request: turn 0's messages + what each later turn added (per provider)."""
    group = [t for t in tasks.values() if t.get("group") == task.get("group")]
    first = next((t for t in group if t.get("turn") == 0), None)
    if not first:
        return []
    msgs = with_nonce(first["messages"], run_id)
    recs = {r["task_id"]: r for r in _all_records(run_id, target)} if target else {}
    for t in sorted(group, key=lambda x: x.get("turn", 0)):
        if 0 < t.get("turn", 0) <= task.get("turn", 0):
            msgs = msgs + ((recs.get(t["id"]) or {}).get("closed_loop_added") or [])
    return msgs


def _new_messages(task, tasks):
    """Messages added since the previous turn of the same session (all of them for turn 0)."""
    if (task.get("meta") or {}).get("loop") == "closed" and task.get("turn"):
        return [{"role": "user", "content": (task.get("expect") or {}).get("request", "")}], -1
    msgs = task["messages"]
    if task.get("turn") and task.get("group"):
        prev = [t for t in tasks.values() if t.get("group") == task["group"] and t.get("turn") == task["turn"] - 1]
        if prev and msgs[:len(prev[0]["messages"])] == prev[0]["messages"]:
            return msgs[len(prev[0]["messages"]):], len(prev[0]["messages"])
    return msgs, 0


def task_index(run_id):
    """Every task of a run in order, with each target's verdict, for navigating the compare view."""
    snap = get_run(run_id)
    if not snap:
        return None
    tasks = _tasks_of(run_id)
    by_target = {tg["target"]: {r["task_id"]: r for r in _all_records(run_id, tg["target"])} for tg in snap["targets"]}
    out = []
    for tid, t in tasks.items():
        out.append({"task_id": tid, "suite": t["suite"], "turn": t.get("turn", 0), "group": t.get("group"),
                    "label": (t.get("meta") or {}).get("label"),
                    "targets": {k: {"verdict": (v.get(tid) or {}).get("verdict"), "codes": (v.get(tid) or {}).get("codes") or [],
                                    "ttft_ms": (v.get(tid) or {}).get("ttft_ms"), "completion_tokens": (v.get(tid) or {}).get("completion_tokens")}
                                for k, v in by_target.items()}})
    return out


def compare(run_id, task_id):
    """One request and every target's reply to it, for side-by-side reading."""
    snap = get_run(run_id)
    tasks = _tasks_of(run_id)
    task = tasks.get(task_id)
    if not snap or not task:
        return None
    new, skipped = _new_messages(task, tasks)
    order = list(tasks)
    i = order.index(task_id)
    replies = []
    for tg in snap["targets"]:
        rec = next((r for r in _all_records(run_id, tg["target"]) if r["task_id"] == task_id), None)
        replies.append({"target": tg["target"], "provider_name": tg.get("provider_name"), "model": tg.get("model"),
                        "model_id": tg.get("model_id"), "record": rec})
    return {"run": run_id, "task": {k: task.get(k) for k in ("id", "suite", "turn", "group", "meta", "max_tokens", "tools", "expect")},
            "messages_before": skipped, "new_messages": new, "replies": replies,
            "prev": order[i - 1] if i > 0 else None, "next": order[i + 1] if i + 1 < len(order) else None,
            "position": i + 1, "total": len(order)}


def _fmt_ms(x):
    return "—" if x is None else ("%.0f ms" % x if x < 1000 else "%.2f s" % (x / 1000))


def _clip(text, limit):
    text = text or ""
    if limit and len(text) > limit:
        return text[:limit] + "\n\n… (%s more characters; the full text is in the .jsonl export)" % format(len(text) - limit, ",")
    return text


def _fence(text, lang=""):
    ticks = "```"
    while ticks in (text or ""):
        ticks += "`"
    return "%s%s\n%s\n%s" % (ticks, lang, text or "", ticks)


def export_markdown(run_id, limit=6000):
    """The whole run as one Markdown document: summary table, then every request with each
    provider's reply under it. Long texts are clipped at `limit` characters (0 = no limit)."""
    snap = get_run(run_id)
    if not snap:
        return None
    tasks = _tasks_of(run_id)
    tg = snap["targets"]
    names = [t.get("provider_name") or t["target"] for t in tg]
    recs = [{r["task_id"]: r for r in _all_records(run_id, t["target"])} for t in tg]
    L = ["# %s" % " vs ".join(names), "",
         "- **Run:** `%s` · %s · status %s" % (run_id, time.strftime("%d %b %Y %H:%M", time.localtime(snap["created"])), snap["status"]),
         "- **Model:** %s" % ", ".join(sorted({t.get("model") or "" for t in tg})),
         "- **Suites:** %s" % ", ".join("%s (%s)" % (k, ", ".join("%s=%s" % kv for kv in v.items()) if isinstance(v, dict) else v)
                                         for k, v in (snap["spec"].get("suites") or {}).items()),
         "- **Seed / sample:** `%s` · `%s`" % (snap.get("seed"), snap.get("task_hash")), "",
         "## Summary", "", "| | " + " | ".join(names) + " |", "|---|" + "---:|" * len(names)]
    def row(label, f):
        L.append("| %s | %s |" % (label, " | ".join(f(t.get("summary") or {}) for t in tg)))
    pct = lambda v: "—" if v is None else "%.1f%%" % (100 * v)  # noqa: E731
    row("Requests", lambda s: str(s.get("n", "—")))
    row("Correct (graded suites)", lambda s: pct(s.get("accuracy")))
    row("Cost total", lambda s: report.money(s.get("cost_usd")))
    row("Cost per request", lambda s: report.money(s.get("cost_per_task_usd")))
    row("TTFT p50", lambda s: _fmt_ms((s.get("ttft_ms") or {}).get("p50")))
    row("Output tok/s p50", lambda s: "—" if not (s.get("tps") or {}).get("p50") else "%.0f" % s["tps"]["p50"])
    row("Cache reuse", lambda s: pct(s.get("cache_reuse_pct")))
    row("Errors", lambda s: str(s.get("errored", "—")))
    row("Degenerate (flagged)", lambda s: str((s.get("review") or {}).get("flagged", "—")))
    row("Failures", lambda s: ", ".join("%s ×%d" % kv for kv in (s.get("failures") or {}).items()) or "none")
    L += ["", "---", ""]
    for n, (tid, task) in enumerate(tasks.items(), 1):
        new, skipped = _new_messages(task, tasks)
        meta = task.get("meta") or {}
        L += ["## %d. `%s`" % (n, tid), "",
              "*%s · turn %d%s*" % (task["suite"], task.get("turn", 0) + 1, (" · " + meta["label"]) if meta.get("label") else ""), "",
              "### Request" + (" (new since previous turn; %d earlier messages not repeated)" % skipped if skipped else ""), ""]
        for m in new:
            content = m.get("content")
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False, indent=1)
            L.append("**%s**%s" % (m.get("role"), (" · `%s`" % m["tool_call_id"]) if m.get("tool_call_id") else ""))
            L.append("")
            L.append(_fence(_clip(content, limit), "text") if m.get("role") in ("tool", "system") else
                     "\n".join("> " + x for x in _clip(content, limit).split("\n")))
            for c in m.get("tool_calls") or []:
                L.append(_fence("%s(%s)" % (c["function"]["name"], c["function"].get("arguments")), "json"))
            L.append("")
        if task.get("tools"):
            L += ["*Tools offered: %s*" % ", ".join(x["function"]["name"] for x in task["tools"]), ""]
        if (task.get("expect") or {}).get("recorded"):
            rc = task["expect"]["recorded"].get("tool_calls") or []
            L += ["*Recorded next step (reference, not scored): %s*" % (
                ", ".join("`%s`" % c["function"]["name"] for c in rc) or "text reply"), ""]
        for t, name, rs in zip(tg, names, recs):
            r = rs.get(tid)
            L += ["### %s" % name, ""]
            if not r:
                L += ["*not run*", ""]
                continue
            bits = ["**%s**" % r["verdict"]] + (["`%s`" % ", ".join(r["codes"])] if r.get("codes") else []) + [
                "TTFT %s" % _fmt_ms(r.get("ttft_ms")), "%s tok/s" % ("%.0f" % r["tps"] if r.get("tps") else "—"),
                "%s prompt · %s cached · %s output" % (r.get("prompt_tokens"), r.get("cached_tokens"), r.get("completion_tokens")),
                report.money(r.get("cost_usd"))]
            L += [" · ".join(bits), ""]
            if r.get("error"):
                L += [_fence(r["error"], "text"), ""]
            for e in ((r.get("degen") or {}).get("evidence") or []):
                L += ["> **Flagged %s** in %s: %s — `%s`" % (e["detector"], e["channel"], e["info"], (e.get("phrase") or "")[:120]), ""]
            if r.get("detail"):
                L += ["*%s*" % r["detail"], ""]
            if r.get("reasoning"):
                L += ["<details><summary>Reasoning (%s tokens)</summary>" % (r.get("reasoning_tokens") or "?"), "",
                      _fence(_clip(r["reasoning"], limit), "text"), "", "</details>", ""]
            if r.get("content"):
                L += [_clip(r["content"], limit), ""]
            for c in r.get("tool_calls") or []:
                args = c["function"].get("arguments") or ""
                try:
                    args = json.dumps(json.loads(args), indent=1, ensure_ascii=False)
                except ValueError:
                    pass
                L += ["**tool call** `%s`" % c["function"]["name"], "", _fence(_clip(args, limit), "json"), ""]
        L += ["---", ""]
    return "\n".join(L)


def export_html(run_id):
    """A self-contained HTML report: same data as the compare view, side by side, works offline."""
    snap = get_run(run_id)
    if not snap:
        return None
    tasks = _tasks_of(run_id)
    data = {"run": run_id, "snap": {k: snap.get(k) for k in ("created", "status", "seed", "task_hash", "spec", "spend_usd")},
            "targets": [{k: t.get(k) for k in ("target", "provider_name", "model", "model_id", "summary")} for t in snap["targets"]],
            "items": []}
    recs = [{r["task_id"]: r for r in _all_records(run_id, t["target"])} for t in snap["targets"]]
    keep = ("verdict", "codes", "detail", "ttft_ms", "e2e_ms", "tps", "prompt_tokens", "cached_tokens", "completion_tokens",
            "reasoning_tokens", "cost_usd", "error", "finish_reason", "content", "reasoning", "tool_calls", "degen", "match", "expected")
    for tid, task in tasks.items():
        new, skipped = _new_messages(task, tasks)
        data["items"].append({"id": tid, "suite": task["suite"], "turn": task.get("turn", 0), "label": (task.get("meta") or {}).get("label"),
                              "skipped": skipped, "new": new, "tools": [x["function"]["name"] for x in task.get("tools") or []],
                              "replies": [{k: (rs.get(tid) or {}).get(k) for k in keep} if rs.get(tid) else None for rs in recs]})
    ui = Path(__file__).resolve().parent / "ui"
    page = (ui / "report.html").read_text()
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return page.replace("/*__MD__*/", (ui / "md.js").read_text()).replace("/*__DATA__*/null", blob)
