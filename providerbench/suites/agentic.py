"""Trace replay: real agent sessions, replayed step by step, to measure serving speed, cost and
cache behaviour on realistic agent traffic.

Sources: SWE-smith (real SWE-agent bug-fix sessions with real tool output, built in), AgentX (SemiAnalysis'
raw Claude Code sessions on SWE-bench tasks, with Claude Code's real system prompt and tools, built in), or
your own JSONL (see datasets.py). For each sampled session a window of `turns` consecutive assistant steps is
chosen so that the context at the first step is >= min_context and at the last <= max_context:

    turn 0: session up to step k        (cold prefix)
    turn 1: + recorded step k + result  (warm prefix)
    ...

Open loop: tools are never executed, and the session always continues with the recorded step and
its real result, so every provider sees byte-identical requests whatever it replied.

Not graded for accuracy. A reply fails only on protocol problems: tool-call JSON that does not
parse, a tool that was not offered, arguments that break the schema, an empty or truncated reply.
The reply is still compared with the recorded step (exact / same_action / same_tool / other_tool /
no_call / text) and the result is stored in the record as reference, never scored or summarised.

convert_trajectory / text_of / literal / clip are copied from provider-bench's build_prompts.py.
"""
import ast
import json
import re

from .. import data, datasets
from ..failures import check_output, check_tool_calls
from .base import Suite, Task, Verdict

DATASET, SPLIT = "SWE-bench/SWE-smith-trajectories", "tool"
AGENTX = "semianalysisai/swebench-cc-traces-raw"          # SemiAnalysis AgentX: raw Claude Code sessions, full text
AGENTX_URL = "https://huggingface.co/datasets/%s/resolve/main/data/train.jsonl" % AGENTX
MAX_TOOL_OUTPUT_CHARS = 6_000
CHARS_PER_TOKEN = 3.6
MAX_PROMPT_TOKENS = 60_000
MIN_STEPS = 8
DEFAULT_SYSTEM = ("You are a helpful assistant that can interact with a computer to solve tasks. "
                  "Use the tools to investigate and fix the issue in the repository.")

KNOWN_TOOLS = {
    "bash": {
        "description": "Run a bash command in the sandbox and return its combined stdout/stderr.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string", "description": "The bash command to execute."}},
            "required": ["command"]}},
    "str_replace_editor": {
        "description": "Custom editing tool for viewing, creating and editing files. State is persistent "
                       "across command calls. `view` shows a file with line numbers or lists a directory; "
                       "`create` writes a new file; `str_replace` replaces one exact occurrence of old_str; "
                       "`insert` inserts new_str after insert_line; `undo_edit` reverts the last edit.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string", "enum": ["view", "create", "str_replace", "insert", "undo_edit"],
                        "description": "The command to run."},
            "path": {"type": "string", "description": "Absolute path to file or directory."},
            "file_text": {"type": "string", "description": "Content for `create`."},
            "old_str": {"type": "string", "description": "String to replace for `str_replace`."},
            "new_str": {"type": "string", "description": "Replacement / inserted string."},
            "insert_line": {"type": "integer", "description": "Line after which to insert."},
            "view_range": {"type": "array", "items": {"type": "integer"},
                           "description": "[start, end] line range for `view`."}},
            "required": ["command", "path"]}},
    "submit": {
        "description": "Submit the current changes as the final answer.",
        "parameters": {"type": "object", "properties": {}}},
}


# ------------------------------------------------------------- copied from build_prompts.py

def text_of(content):
    """SWE-smith stores content either as a string or the repr of [{'type':'text','text':..}]."""
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    if isinstance(content, str) and content.startswith("[{") and content.rstrip().endswith("}]"):
        try:
            return text_of(ast.literal_eval(content))
        except (ValueError, SyntaxError):
            return content
    return content or ""


def literal(value, default):
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str) and value:
        try:
            return ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return default
    return default


def clip(text):
    if len(text) <= MAX_TOOL_OUTPUT_CHARS:
        return text
    half = MAX_TOOL_OUTPUT_CHARS // 2
    return text[:half] + "\n<response clipped: %d chars omitted>\n" % (len(text) - 2 * half) + text[-half:]


XML_CALL = re.compile(r"<function=([\w.-]+)>(.*?)</function>", re.S)
XML_PARAM = re.compile(r"<parameter=([\w.-]+)>(.*?)</parameter>", re.S)


def convert_trajectory(raw_messages):
    """SWE-smith row -> clean OpenAI chat messages (assistant tool_calls + tool results)."""
    out, open_ids = [], []
    for m in raw_messages:
        role = m.get("role")
        text = text_of(m.get("content"))
        if role == "system":
            continue
        if role == "user":
            if open_ids and text.lstrip()[:12].lower() == "observation:":
                out.append({"role": "tool", "tool_call_id": open_ids.pop(0),
                            "content": clip(text.lstrip()[12:].lstrip())})
                continue
            out.append({"role": "user", "content": text})
            open_ids = []
        elif role == "assistant":
            msg = {"role": "assistant", "content": text}
            calls = []
            if not m.get("tool_calls"):
                fm = XML_CALL.search(text)
                if fm:
                    args = {}
                    for k, v in XML_PARAM.findall(fm.group(2)):
                        if k in ("view_range", "insert_line"):
                            try:
                                v = json.loads(v)
                            except ValueError:
                                pass
                        args[k] = v
                    calls.append({"id": "call_%d" % len(out), "type": "function",
                                  "function": {"name": fm.group(1), "arguments": json.dumps(args)}})
                    msg["content"] = text[:fm.start()].strip()
            for c in literal(m.get("tool_calls"), []) or []:
                fn = c.get("function") or {}
                args = fn.get("arguments", "{}")
                if not isinstance(args, str):
                    args = json.dumps(args)
                if c.get("id") and fn.get("name"):
                    calls.append({"id": c["id"], "type": "function",
                                  "function": {"name": fn["name"], "arguments": args}})
            if calls:
                msg["tool_calls"] = calls
            open_ids = [c["id"] for c in calls]
            out.append(msg)
        elif role == "tool":
            ids = literal(m.get("tool_call_ids"), []) or []
            tid = ids[0] if ids else None
            if tid in open_ids:
                open_ids.remove(tid)
                out.append({"role": "tool", "tool_call_id": tid, "content": clip(text)})
            else:
                out.append({"role": "user", "content": clip(text)})
    fixed = []
    for i, msg in enumerate(out):
        fixed.append(msg)
        if msg["role"] == "assistant" and msg.get("tool_calls"):
            answered = set()
            for nxt in out[i + 1:]:
                if nxt["role"] != "tool":
                    break
                answered.add(nxt["tool_call_id"])
            msg["tool_calls"] = [c for c in msg["tool_calls"] if c["id"] in answered]
            if not msg["tool_calls"]:
                del msg["tool_calls"]
    return [m for m in fixed if m["role"] != "assistant" or m.get("content") or m.get("tool_calls")]


# --------------------------------------------------------------------------- helpers

def est_tokens(obj):
    return int(len(json.dumps(obj, ensure_ascii=False)) / CHARS_PER_TOKEN)


def tools_for(conv, given=None):
    """The session's own tool definitions when it has them, else schemas for the tools it used."""
    if given:
        return given
    used = []
    for m in conv:
        for c in m.get("tool_calls") or []:
            n = c["function"]["name"]
            if n not in used:
                used.append(n)
    tools = []
    for n in used:
        spec = KNOWN_TOOLS.get(n) or {"description": "Tool %s." % n,
                                      "parameters": {"type": "object", "properties": {}}}
        tools.append({"type": "function", "function": dict(name=n, **spec)})
    return tools


def _args(call):
    try:
        a = json.loads(call["function"].get("arguments") or "{}")
        return a if isinstance(a, dict) else {}
    except ValueError:
        return {}


def _norm(v):
    if isinstance(v, str):
        return " ".join(v.split()).rstrip("/")
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, list):
        return [_norm(x) for x in v]
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in v.items()}
    return v


def key_args(name, args, schema):
    if name == "bash":
        keys = ["command"]
    elif name == "str_replace_editor":
        keys = ["command", "path"] + (["old_str"] if args.get("command") == "str_replace" else [])
    else:
        keys = list((schema or {}).get("required") or []) or sorted(args)
    return {k: _norm(args.get(k)) for k in keys}


def render_call(name, args, width=70):
    if not args:
        return "%s()" % name
    if name == "bash":
        s = args.get("command", "")
    elif name == "str_replace_editor":
        s = "%s %s" % (args.get("command", ""), args.get("path", ""))
    else:
        s = ", ".join("%s=%s" % (k, json.dumps(v, ensure_ascii=False)) for k, v in args.items())
    s = " ".join(str(s).split())
    return "%s(%s)" % (name, s if len(s) <= width else s[:width - 1] + "…")


def compare(rec_call, got_call, schema):
    """-> exact | same_action | same_tool | other_tool"""
    rn, gn = rec_call["function"]["name"], got_call["function"]["name"]
    if rn != gn:
        return "other_tool"
    ra, ga = _args(rec_call), _args(got_call)
    if _norm(ra) == _norm(ga):
        return "exact"
    if key_args(rn, ra, schema) == key_args(rn, ga, schema):
        return "same_action"
    return "same_tool"


def plan_window(rng, conv, base_tokens, turns, min_ctx, max_ctx):
    """Start index into the assistant steps for `turns` consecutive steps, or None."""
    steps = [i for i, m in enumerate(conv) if m["role"] == "assistant"]
    if len(steps) < turns:
        return None
    ok = []
    for j in range(0, len(steps) - turns + 1):
        first, last = steps[j], steps[j + turns - 1]
        if base_tokens + est_tokens(conv[:first]) < min_ctx:
            continue
        if base_tokens + est_tokens(conv[:last]) > max_ctx:
            break
        ok.append(j)
    return (steps, rng.choice(ok)) if ok else None


def _cc_text(content):
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content or [] if isinstance(b, dict) and "text" in b)


def convert_claude_code(req):
    """One Anthropic Messages request (system, tools, messages) -> (OpenAI messages, OpenAI tools).
    Thinking blocks are dropped; tool_use -> tool_calls; tool_result -> role "tool"."""
    msgs = []
    system = "\n".join(b.get("text", "") for b in req.get("system") or []
                       if not b.get("text", "").startswith("x-anthropic-billing-header"))
    if system:
        msgs.append({"role": "system", "content": system})
    for m in req.get("messages") or []:
        c = m.get("content")
        blocks = [{"type": "text", "text": c}] if isinstance(c, str) else (c or [])
        if m.get("role") == "assistant":
            text = "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            calls = [{"id": b["id"], "type": "function",
                      "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {})}}
                     for b in blocks if b.get("type") == "tool_use"]
            if text or calls:
                msgs.append(dict({"role": "assistant", "content": text}, **({"tool_calls": calls} if calls else {})))
            continue
        texts = []
        for b in blocks:
            if b.get("type") == "tool_result":
                msgs.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": _cc_text(b.get("content"))})
            elif b.get("type") == "text":
                texts.append(b.get("text", ""))
        if texts:
            msgs.append({"role": "user", "content": "\n".join(texts)})
    tools = [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
              "parameters": t.get("input_schema") or {"type": "object", "properties": {}}}}
             for t in req.get("tools") or []]
    return msgs, tools


_AGENTX = None


def agentx_sessions():
    """[(session id, OpenAI messages, OpenAI tools)] from the AgentX raw traces (downloaded once, cached)."""
    global _AGENTX
    if _AGENTX is None:
        out = []
        for line in data.http_get(AGENTX_URL, timeout=300).splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            mains = [q for q in r["requests"] if q.get("type") == "s"] or r["requests"]
            last = max(mains, key=lambda q: len(q.get("messages") or []))
            conv, tools = convert_claude_code(last)
            out.append((r["id"], conv, tools))
        _AGENTX = out
    return _AGENTX


class AgenticSuite(Suite):
    name = "agentic"
    title = "Trace replay"
    scenario = "replay"
    graded = False          # no accuracy: a reply that differs from the recorded agent is not "wrong"
    degen_check = True      # beta: degeneration detection runs only here, where prompts are real tasks
    degen_note = ("Real task: a recorded agent session (SWE-smith or your own traces). A degenerate reply "
                  "here is worth checking.")
    blurb = "Real agent sessions replayed step by step; each reply is compared with what the agent actually did."
    source = "https://huggingface.co/datasets/SWE-bench/SWE-smith-trajectories"
    method = [
        'Real agent sessions: SWE-smith or your JSONL.',
        'Each step sends the session so far; the recorded step continues it.',
        'Tools never run, so both providers get identical requests.',
        'No accuracy score. Fails only on bad tool calls, empty or degenerate replies.',
        'Later steps share the prefix: warm TTFT and cache reuse.',
        'Degeneration detection (beta): loops, gibberish and language switches are flagged for review.',
    ]
    default_n = 5
    est_prompt_tokens = 25000
    est_output_tokens = 1500

    def params(self):
        return [
            {"key": "n", "label": "Sessions", "type": "int", "default": 5, "min": 1, "max": 200,
             "help": "How many different sessions to sample."},
            {"key": "source", "label": "Traces", "type": "source", "default": "swe-smith",
             "help": "Built-in SWE-smith or AgentX (Claude Code) sessions, or a JSONL you upload."},
            {"key": "turns", "label": "Steps per session", "type": "int", "default": 3, "min": 1, "max": 20,
             "help": "Consecutive agent steps replayed from each session."},
            {"key": "min_context", "label": "Min context (tokens)", "type": "int", "default": 8000, "min": 0, "max": 900000,
             "help": "The window starts at a step whose context is at least this long."},
            {"key": "max_context", "label": "Max context (tokens)", "type": "int", "default": 64000, "min": 2000, "max": 1000000,
             "help": "The last step of the window must fit under this."},
            {"key": "max_tokens", "label": "Max output per step", "type": "int", "default": 8192, "min": 256, "max": 131072},
        ]

    # -- sampling
    def load(self, rng, n, **params):
        p = dict(self.defaults(), **params)
        src = p["source"] or "swe-smith"
        if src.startswith("upload:"):
            return self._load_upload(rng, n, src.split(":", 1)[1], p)
        if src == "agentx":
            return self._load_agentx(rng, n, p)
        return self._load_swe(rng, n, p)

    def _window_tasks(self, conv, tools, steps, j, p, group, meta):
        tasks = []
        for t in range(p["turns"]):
            k = steps[j + t]
            rec = conv[k]
            calls = rec.get("tool_calls") or []
            exp = render_call(calls[0]["function"]["name"], _args(calls[0])) if calls else "text reply"
            tasks.append(Task(
                id="%s/t%d" % (group, t), suite=self.name, messages=conv[:k], tools=tools or None,
                max_tokens=int(p["max_tokens"]), expect={"recorded": rec},
                meta=dict(meta, step=k, expected_label=exp), group=group, turn=t))
        return tasks

    def _load_swe(self, rng, n, p):
        total = data.hf_count(DATASET, SPLIT)
        tasks, seen, attempts = [], set(), 0
        while len(seen) < n and attempts < max(40, n * 12):
            attempts += 1
            offset = rng.randrange(total)
            rows = data.hf_rows(DATASET, SPLIT, offset, 1)
            if not rows:
                continue
            row = rows[0]
            tid = row.get("traj_id") or row.get("instance_id") or str(offset)
            if tid in seen:
                continue
            try:
                raw = json.loads(row["messages"])
                conv = convert_trajectory(raw)
            except (ValueError, KeyError, TypeError):
                continue
            if not conv or conv[0]["role"] != "user":
                continue
            sys_raw = next((m for m in raw if m.get("role") == "system"), None)
            system = text_of(sys_raw.get("content")) if sys_raw else ""
            conv = [{"role": "system", "content": system.strip() or DEFAULT_SYSTEM}] + conv
            tools = tools_for(conv)
            win = plan_window(rng, conv, est_tokens(tools), int(p["turns"]), int(p["min_context"]), int(p["max_context"]))
            if win is None:
                continue
            steps, j = win
            seen.add(tid)
            group = "swe/%s@%d" % (tid, steps[j])
            meta = {"label": row.get("instance_id") or tid, "traj_id": tid, "source": "swe-smith",
                    "url": "https://huggingface.co/datasets/%s/viewer/default/%s?row=%d" % (DATASET, SPLIT, offset)}
            tasks += self._window_tasks(conv, tools, steps, j, p, group, meta)
        return tasks

    def _load_agentx(self, rng, n, p):
        """AgentX raw Claude Code traces. Each session is its main agent's last request (Anthropic format),
        converted to OpenAI chat format. There are only 10 sessions, so when n > 10 a session is reused
        with a different window (no (session, step) pair twice)."""
        sessions = agentx_sessions()
        order = list(range(len(sessions)))
        rng.shuffle(order)
        tasks, used, attempts = [], set(), 0
        while len(used) < n and attempts < n * 20:
            idx = order[attempts % len(order)]
            attempts += 1
            sid, conv, tools = sessions[idx]
            win = plan_window(rng, conv, est_tokens(tools), int(p["turns"]), int(p["min_context"]), int(p["max_context"]))
            if win is None:
                continue
            steps, j = win
            if (sid, steps[j]) in used:
                continue
            used.add((sid, steps[j]))
            group = "agentx/%s@%d" % (sid, steps[j])
            meta = {"label": sid, "source": "agentx", "url": "https://huggingface.co/datasets/%s" % AGENTX}
            tasks += self._window_tasks(conv, tools, steps, j, p, group, meta)
        return tasks

    def _load_upload(self, rng, n, did, p):
        items = datasets.load(did)
        order = list(range(len(items)))
        rng.shuffle(order)
        tasks, used = [], 0
        for idx in order:
            if used >= n:
                break
            it = items[idx]
            conv, tools = it["messages"], tools_for(it["messages"], it.get("tools"))
            group = "up/%s/%s" % (did, it["id"])
            meta = {"label": it["id"], "source": "upload:%s" % did}
            if it["kind"] == "request":
                tasks.append(Task(id=group, suite=self.name, messages=conv, tools=tools or None,
                                  max_tokens=int(p["max_tokens"]), expect={"recorded": None},
                                  meta=meta, group=group, turn=0))
                used += 1
                continue
            win = plan_window(rng, conv, est_tokens(tools), int(p["turns"]), int(p["min_context"]), int(p["max_context"]))
            if win is None:
                continue
            steps, j = win
            tasks += self._window_tasks(conv, tools, steps, j, p, "%s@%d" % (group, steps[j]), meta)
            used += 1
        return tasks

    # -- grading
    def grade(self, task, reply):
        codes = check_output(reply)
        rec = task.expect.get("recorded")
        calls = (reply.get("message") or {}).get("tool_calls") or []
        got = render_call(calls[0]["function"]["name"], _args(calls[0])) if calls else \
            ("text: " + " ".join(((reply.get("message") or {}).get("content") or "").split())[:80])
        if rec is None:                      # a plain request from your data: nothing to compare with
            tc_codes, _, detail = check_tool_calls(reply, task.tools)
            codes = sorted(set(codes + tc_codes))
            return Verdict("fail" if codes else "pass", codes, answer=got, detail=detail)
        rec_calls = rec.get("tool_calls") or []
        expected = task.meta.get("expected_label", "")
        if not rec_calls:                    # the agent answered in text at this step
            tc_codes, _, detail = check_tool_calls(reply, task.tools)
            codes = sorted(set(codes + tc_codes))
            return Verdict("fail" if codes else "pass", codes, answer=got, expected=expected, detail=detail,
                           match="other_tool" if calls else "text")
        if not calls:                        # a text reply is a valid reply; only an empty one fails
            content = ((reply.get("message") or {}).get("content") or "").strip()
            if not content:
                codes.append("no_answer")
            return Verdict("fail" if codes else "pass", sorted(set(codes)), answer=got, expected=expected,
                           detail="empty reply" if not content else "", match="no_call")
        tc_codes, _, detail = check_tool_calls(reply, task.tools)
        schema = next((t["function"].get("parameters") for t in task.tools or []
                       if t["function"]["name"] == rec_calls[0]["function"]["name"]), None)
        match = compare(rec_calls[0], calls[0], schema)   # kept as reference in the trace, not scored
        codes = sorted(set(codes + tc_codes))
        return Verdict("fail" if codes else "pass", codes, answer=got, expected=expected, detail=detail, match=match)


class CustomSuite(AgenticSuite):
    """Your data: the same replay engine, fed only by JSONL you upload (sessions and/or single requests)."""
    name = "custom"
    title = "Your data"
    scenario = "custom"
    blurb = "Your own sessions or prompts (JSONL), replayed against both providers."
    source = ""
    method = [
        "Your JSONL: sessions are replayed step by step, single requests sent as is.",
        "Sessions continue with your recorded step, so both providers get identical requests.",
        "Tools never run. No accuracy score.",
        "Fails on bad tool calls, empty or degenerate replies.",
        "Degeneration detection (beta) on every reply.",
    ]
    default_n = 20
    degen_note = "Your data: real prompts, so a degenerate reply here is worth checking."

    def params(self):
        ps = [dict(p) for p in super().params()]
        for p in ps:
            if p["key"] == "source":
                p.update(default="", label="Dataset", help="A JSONL you uploaded (Data tab or Upload).", uploads_only=True)
            elif p["key"] == "n":
                p.update(default=20, label="Sessions or requests")
            elif p["key"] == "min_context":
                p.update(default=0)
            elif p["key"] == "turns":
                p.update(default=2)
        return ps

    def load(self, rng, n, **params):
        p = dict(self.defaults(), **params)
        src = p.get("source") or ""
        if not src.startswith("upload:"):
            raise ValueError("pick an uploaded dataset for Your data (Data tab, or Upload JSONL)")
        tasks = self._load_upload(rng, n, src.split(":", 1)[1], p)
        for task in tasks:
            task.suite = self.name
        return tasks
