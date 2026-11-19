"""Long agent session: one long, real coding-agent session per sample, then a series of heavy
requests on top of it. Stresses decode at long context: long reasoning, long written output,
cache reuse as the session grows.

Framing (`framing`):
    agent   ~20k tokens like a production coding agent: system prompts from open-source coding agents
            (tag-pinned GitHub files) + a sample of the GitHub MCP server's real tool schemas
    trace   the trajectories' own short system prompt and the SWE-agent tools only

The session: real SWE-smith bug-fix trajectories (real tool output), concatenated until the prompt
reaches `start_context`. Then `turns` requests, one per turn, each needing real reasoning and a
substantial written result (root cause, full fix, regression tests, risks, reviewer reply, PR text,
trace, ...), up to `max_tokens` and never past `max_context`.

Loop (`loop`):
    open    identical requests for every provider: turn t = the session cut a little further along
            the recorded trajectory (+`growth` tokens per turn) + request t. Earlier turns are a
            prefix of later ones, so cache reuse is measured. The model does not see its own replies.
    closed  the model's own reply (and a "file created" / "not available" result for its tool calls)
            is appended before the next request, so the session grows the way a live one does and
            requests differ per provider after turn 1.

Not graded for accuracy; a turn fails on errors, broken tool calls, an empty or degenerate reply.
"""
import io
import json
import tarfile

from .. import data
from ..failures import check_output, check_tool_calls
from .agentic import DATASET, SPLIT, DEFAULT_SYSTEM, KNOWN_TOOLS, convert_trajectory, est_tokens, text_of
from .base import Suite, Task, Verdict

SYSTEM_PROMPT_SOURCES = [
    "https://raw.githubusercontent.com/openai/codex/rust-v0.40.0/codex-rs/core/prompt.md",
    "https://raw.githubusercontent.com/openai/codex/rust-v0.40.0/codex-rs/core/gpt_5_codex_prompt.md",
    "https://raw.githubusercontent.com/sst/opencode/v0.3.0/packages/opencode/src/session/prompt/anthropic.txt",
    "https://raw.githubusercontent.com/sst/opencode/v0.3.0/packages/opencode/src/session/prompt/beast.txt",
    "https://raw.githubusercontent.com/All-Hands-AI/OpenHands/0.40.0/openhands/agenthub/codeact_agent/prompts/system_prompt.j2",
]
TOOLS_TARBALL = "https://codeload.github.com/github/github-mcp-server/tar.gz/refs/tags/v0.4.0"
SYSTEM_BUDGET = 6000          # tokens of system prompt
BASE_TARGET = 20000           # system prompt + tool schemas
CHARS_PER_TOKEN = 3.6

TASK_REQUESTS = [
    "Pause the investigation here. Using only what is already in this session, explain the root cause of "
    "the problem we are working on: which function or code path is wrong, why, and what input triggers it. "
    "Quote the relevant lines you have seen. If something is still unknown, say exactly what and how you "
    "would settle it.",
    "Now write the complete fix. Use str_replace_editor (command `create`) to write the full corrected "
    "version of every function involved to `/testbed/fix_proposal.py`, with a comment on each change "
    "saying why it is needed.",
    "Write a regression test file for that fix with str_replace_editor `create` at "
    "`/testbed/tests/test_regression.py`: pytest, at least eight cases, covering the failing input from "
    "the problem statement, the edge cases around it, and behaviour that must not change.",
    "Think about what else your fix could break. Name three other code paths in this repository that "
    "depend on the behaviour you changed, explain the risk for each, and then create "
    "`/testbed/verify_fix.py`, a script that checks all three.",
    "A reviewer objects that the fix changes behaviour for existing callers. Argue both sides, decide, "
    "and create `/testbed/fix_proposal_v2.py` with a revised version that addresses the objection.",
    "Write the pull request description for this change: summary, root cause, the fix, how it was "
    "tested, risks and rollout notes. Be concrete and refer to the actual functions and files.",
    "Create `/testbed/NOTES.md`: a step-by-step trace of the failing input through the code, first "
    "before the fix and then after it, showing the value of each relevant variable at each step.",
    "Create `/testbed/tests/test_properties.py`: property-based tests (hypothesis) for the fixed "
    "behaviour, with a short comment on each property explaining why it must hold.",
    "Rewrite `/testbed/fix_proposal.py` in full with complete type hints and docstrings, and handle "
    "the two most likely invalid inputs explicitly with clear error messages.",
    "Write a changelog entry and a short migration note for users of this library, then list the "
    "follow-up work you would file as separate issues, each with a one-paragraph description.",
]
TASK_NOTE = " Do not run commands or open more files for this; work from what is already in the session."


def answer_calls(message):
    """Closed loop: results for the model's own tool calls. Writing a file succeeds (the text is what
    is measured); anything that would need a real machine is declined."""
    out = []
    for c in (message or {}).get("tool_calls") or []:
        try:
            args = json.loads(c["function"].get("arguments") or "{}")
        except ValueError:
            args = {}
        if c["function"]["name"] == "str_replace_editor" and isinstance(args, dict) and args.get("command") == "create":
            text = "File created successfully at: %s" % args.get("path", "/testbed/file")
        else:
            text = "Not available right now: commands and file views are paused. Answer from the session so far."
        out.append({"role": "tool", "tool_call_id": c["id"], "content": text})
    return out


def closed_loop_messages(prev_sent, prev_message, request):
    """The next closed-loop request: what was sent last turn + the model's reply + tool results + request."""
    msgs = list(prev_sent)
    reply = {"role": "assistant", "content": (prev_message or {}).get("content") or ""}
    if (prev_message or {}).get("tool_calls"):
        reply["tool_calls"] = prev_message["tool_calls"]
    msgs.append(reply)
    msgs += answer_calls(prev_message)
    msgs.append({"role": "user", "content": request})
    return msgs


def _mcp_tool(snap):
    params = snap.get("inputSchema") or {}
    params.setdefault("type", "object")
    params.setdefault("properties", {})
    return {"type": "function", "function": {"name": snap["name"], "description": snap.get("description", ""),
                                             "parameters": params}}


def _core_tools():
    return [{"type": "function", "function": dict(name=n, **spec)} for n, spec in KNOWN_TOOLS.items()]


def build_framing(rng, framing):
    """(system prompt, tools) for the session."""
    tools = _core_tools()
    if framing != "agent":
        return DEFAULT_SYSTEM, tools
    urls = SYSTEM_PROMPT_SOURCES[:]
    rng.shuffle(urls)
    parts, total = [], 0
    for url in urls:
        if total >= SYSTEM_BUDGET:
            break
        try:
            text = data.http_get(url, timeout=30, retries=2).strip()
        except RuntimeError:
            continue
        text = text[: int((SYSTEM_BUDGET - total) * CHARS_PER_TOKEN)]
        parts.append(text)
        total += len(text) / CHARS_PER_TOKEN
    system = "\n\n---\n\n".join(parts) if parts else DEFAULT_SYSTEM
    try:
        raw = data.http_get(TOOLS_TARBALL, timeout=120, binary=True)
        snaps = []
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
            for m in tar:
                if m.isfile() and m.name.endswith(".snap") and "__toolsnaps__" in m.name:
                    try:
                        snaps.append(json.load(tar.extractfile(m)))
                    except ValueError:
                        pass
        snaps.sort(key=lambda s: s.get("name", ""))
        rng.shuffle(snaps)
        size = len(system) / CHARS_PER_TOKEN + est_tokens(tools)
        for snap in snaps:
            if size >= BASE_TARGET:
                break
            if "name" in snap:
                tools.append(_mcp_tool(snap))
                size += est_tokens(tools[-1])
    except RuntimeError:
        pass
    return system, tools


def session_trace(rng, target_tokens, attempts=40):
    """Real SWE-smith sessions concatenated until about `target_tokens`."""
    total_rows = data.hf_count(DATASET, SPLIT)
    msgs, ids, size = [], [], 0
    for _ in range(attempts):
        if size >= target_tokens:
            break
        rows = data.hf_rows(DATASET, SPLIT, rng.randrange(total_rows), 1)
        if not rows:
            continue
        try:
            conv = convert_trajectory(json.loads(rows[0]["messages"]))
        except (ValueError, KeyError, TypeError):
            continue
        if len(conv) < 8 or conv[0]["role"] != "user":
            continue
        if msgs and msgs[-1]["role"] == "user":
            msgs.append({"role": "assistant", "content": "Done. What should I look at next?"})
        msgs.extend(conv)
        ids.append(rows[0].get("instance_id") or rows[0].get("traj_id"))
        size = est_tokens(msgs)
    return msgs, ids


def cut_at(msgs, budget):
    """Largest prefix under `budget` tokens that ends just before a user message (a clean point to ask)."""
    best, size = 0, 0
    for i, m in enumerate(msgs):
        size += est_tokens(m)
        if size > budget:
            break
        if i + 1 < len(msgs) and msgs[i + 1]["role"] == "user" and m["role"] != "user":
            best = i + 1
        if m["role"] == "tool" and (i + 1 == len(msgs) or msgs[i + 1]["role"] == "assistant"):
            best = i + 1
    return best


class SessionSuite(Suite):
    name = "session"
    title = "Long agent session"
    scenario = "session"
    graded = False
    degen_check = True
    blurb = "One long real coding-agent session, then heavy requests on top: long reasoning and long output at long context."
    source = "https://huggingface.co/datasets/SWE-bench/SWE-smith-trajectories"
    method = [
        "One long real SWE-smith session (start context), framed like a production coding agent.",
        "Each turn asks for heavy work: root cause, full fix, tests, risks, PR text.",
        "Open loop: identical requests; each turn adds more of the recorded session.",
        "Closed loop: the model's own replies carry into the next turn.",
        "Not graded. Shows decode speed, thinking share and cost at long context.",
        "Degeneration detection (beta) on every reply.",
    ]
    default_n = 1
    est_output_tokens = 12000

    def params(self):
        return [
            {"key": "n", "label": "Sessions", "type": "int", "default": 1, "min": 1, "max": 10,
             "help": "Independent long sessions; each runs its turns in order."},
            {"key": "loop", "label": "Loop", "type": "choice", "default": "open",
             "choices": [["open", "Open: identical requests"], ["closed", "Closed: model's own replies"]],
             "help": "Open keeps both providers on byte-identical requests."},
            {"key": "start_context", "label": "Start context (tokens)", "type": "int", "default": 64000, "min": 4000, "max": 900000},
            {"key": "turns", "label": "Turns", "type": "int", "default": 7, "min": 1, "max": len(TASK_REQUESTS)},
            {"key": "max_tokens", "label": "Max output per turn", "type": "int", "default": 65536, "min": 1024, "max": 131072},
            {"key": "max_context", "label": "Context window (tokens)", "type": "int", "default": 128000, "min": 16000, "max": 1048576,
             "help": "Output is capped so prompt + output stays inside this."},
            {"key": "growth", "label": "Open loop: new session per turn", "type": "int", "default": 4000, "min": 0, "max": 64000,
             "help": "Tokens of further recorded session added before each later turn."},
            {"key": "framing", "label": "Framing", "type": "choice", "default": "agent",
             "choices": [["agent", "Coding agent (~20k prompt + MCP tools)"], ["trace", "Trace only"]]},
        ]

    def load(self, rng, n, **params):
        p = dict(self.defaults(), **params)
        turns, loop = int(p["turns"]), p["loop"]
        system, tools = build_framing(rng, p["framing"])
        base = est_tokens([{"role": "system", "content": system}]) + est_tokens(tools)
        start, growth, limit = int(p["start_context"]), int(p["growth"]), int(p["max_context"])
        need = start - base + (growth * turns if loop == "open" else 0) + 4000
        tasks = []
        for s in range(n):
            trace, ids = session_trace(rng, max(need, 8000))
            group = "session/%d" % s
            for t in range(turns):
                request = TASK_REQUESTS[t % len(TASK_REQUESTS)] + TASK_NOTE
                budget = start - base + (growth * t if loop == "open" else 0)
                k = cut_at(trace, budget)
                if loop == "closed" and t > 0:
                    msgs = []                     # built at send time from the previous turn's reply
                else:
                    msgs = [{"role": "system", "content": system}] + trace[:k] + [{"role": "user", "content": request}]
                prompt_est = base + est_tokens(trace[:k]) + 200
                tasks.append(Task(
                    id="%s/t%d" % (group, t), suite=self.name, messages=msgs, tools=tools,
                    max_tokens=max(1024, min(int(p["max_tokens"]), limit - prompt_est - 512)),
                    expect={"request": request, "context_limit": limit},
                    meta={"label": "Session %d · %s loop · ~%dk start" % (s + 1, loop, start // 1000),
                          "loop": loop, "trajectories": ids[:12], "turn_request": request[:120],
                          "est_output": min(int(p["max_tokens"]), 12000)},
                    group=group, turn=t))
        return tasks

    def grade(self, task, reply):
        codes = check_output(reply)
        tc_codes, _, detail = check_tool_calls(reply, task.tools)
        msg = reply.get("message") or {}
        if not (msg.get("content") or "").strip() and not msg.get("tool_calls"):
            codes.append("no_answer")
            detail = detail or "empty reply"
        codes = sorted(set(codes + tc_codes))
        out = reply.get("completion_tokens") or 0
        return Verdict("fail" if codes else "pass", codes, answer="%d output tokens" % out, detail=detail)
