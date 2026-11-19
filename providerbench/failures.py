"""Failure codes. Every failed request or reply carries one or more of these.

Transport codes are set by the runner from the HTTP exchange; output and correctness codes are set
by the shared checks below and by each suite's grader.
"""
import json
import re

CODES = {
    # transport: the provider did not give a usable reply
    "http_429":        ("transport", "Rate limited (HTTP 429)"),
    "http_4xx":        ("transport", "Request refused (HTTP 4xx)"),
    "http_5xx":        ("transport", "Server error (HTTP 5xx)"),
    "timeout":         ("transport", "Timed out"),
    "connection":      ("transport", "Connection failed"),
    "empty_200":       ("transport", "HTTP 200 with no tokens"),
    "stream_error":    ("transport", "Error inside the stream"),
    "budget":          ("transport", "Skipped: budget reached"),
    # output: a reply came back but it is malformed
    "truncated":       ("output", "Stopped at max_tokens"),
    "tool_call_invalid_json": ("output", "Tool-call arguments are not valid JSON"),
    "tool_call_unknown_tool": ("output", "Called a tool that was not offered"),
    "tool_args_schema": ("output", "Tool-call arguments do not match the schema"),
    "missing_tool_call": ("output", "Expected a tool call, got none"),
    "unexpected_tool_call": ("output", "Called a tool when none was wanted"),
    "reasoning_leak":  ("output", "Reasoning markup leaked into the answer"),
    "bad_json":        ("output", "Structured output is not valid JSON / schema"),
    "degenerate":      ("output", "Degenerate output: loop, word salad, gibberish or language switch"),
    "short_output":    ("output", "Stopped well short of the requested output length"),
    # correctness
    "no_answer":       ("correctness", "No answer could be extracted"),
    "wrong_answer":    ("correctness", "Wrong answer"),
    "wrong_tool":      ("correctness", "Called the wrong tool"),
    "wrong_call_count": ("correctness", "Wrong number of tool calls"),
    "wrong_args":      ("correctness", "Right tool, wrong arguments"),
    "mismatch":        ("correctness", "Output differs from what was asked"),
}


def kind(code):
    return CODES.get(code, ("other", code))[0]


def label(code):
    return CODES.get(code, ("other", code))[1]


THINK_TAG = re.compile(r"</?think>|<\|?(?:begin|end)_of_thought\|?>", re.I)

_JSON_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool,
               "array": list, "object": dict, "null": type(None)}


def _type_ok(value, schema):
    t = schema.get("type") if isinstance(schema, dict) else None
    if t is None:
        return True
    types = t if isinstance(t, list) else [t]
    for tt in types:
        py = _JSON_TYPES.get(tt)
        if py is None:
            return True
        if tt in ("integer", "number") and isinstance(value, bool):
            continue
        if isinstance(value, py):
            return True
    return False


def check_args(args, schema):
    """Light JSON-schema check: required keys present, top-level types right, enums respected."""
    if not isinstance(args, dict):
        return "arguments are not an object"
    props = (schema or {}).get("properties") or {}
    for k in (schema or {}).get("required") or []:
        if k not in args:
            return "missing required argument %r" % k
    for k, v in args.items():
        s = props.get(k)
        if s is None:
            continue
        if not _type_ok(v, s):
            return "argument %r should be %s" % (k, s.get("type"))
        if "enum" in s and v not in s["enum"]:
            return "argument %r=%r not in enum" % (k, v)
    return None


def check_tool_calls(reply, tools):
    """Shared checks on the tool calls in a reply. Returns (codes, parsed_calls, detail).

    parsed_calls: [{"name": str, "args": dict|None}] in reply order.
    """
    calls = (reply.get("message") or {}).get("tool_calls") or []
    offered = {t["function"]["name"]: t["function"].get("parameters") or {} for t in tools or []}
    codes, parsed, detail = [], [], ""
    for c in calls:
        fn = c.get("function") or {}
        name, raw = fn.get("name") or "", fn.get("arguments")
        try:
            args = raw if isinstance(raw, dict) else json.loads(raw or "{}")
        except ValueError:
            codes.append("tool_call_invalid_json")
            detail = detail or "%s(...) arguments: %s" % (name, (raw or "")[:120])
            parsed.append({"name": name, "args": None})
            continue
        parsed.append({"name": name, "args": args})
        if name not in offered:
            codes.append("tool_call_unknown_tool")
            detail = detail or "called %r" % name
            continue
        why = check_args(args, offered[name])
        if why:
            codes.append("tool_args_schema")
            detail = detail or "%s: %s" % (name, why)
    return sorted(set(codes)), parsed, detail


def check_output(reply):
    """Checks every reply gets, whatever the suite: truncation and reasoning-tag leaks."""
    codes = []
    if reply.get("finish_reason") == "length":
        codes.append("truncated")
    content = (reply.get("message") or {}).get("content") or ""
    if THINK_TAG.search(content):
        codes.append("reasoning_leak")
    return codes
