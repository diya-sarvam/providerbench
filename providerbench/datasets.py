"""Your own data: JSONL uploads, validated and stored under data/uploads/ (git-ignored).

One JSON object per line:

    {"id": "optional", "messages": [...OpenAI chat messages...], "tools": [...optional...]}

- A line whose messages contain assistant turns is a *session*. Every assistant turn is a step that
  can be replayed: the messages before it are sent, the recorded turn is what the reply is compared
  with, and the session continues with the recorded turn (and its tool results), not the model's.
- A line with no assistant turn is a single *request*, sent as is.

Accepted message shapes are the OpenAI chat format: roles system/user/assistant/tool, assistant
`tool_calls` with string `arguments`, tool results with `tool_call_id`.
"""
import hashlib
import json
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
from .config import HOME

UPLOADS = HOME / "uploads"
MAX_BYTES = 200 * 1024 * 1024


def _norm_message(m, n):
    if not isinstance(m, dict) or m.get("role") not in ("system", "user", "assistant", "tool", "developer"):
        raise ValueError("message %d: role must be system/user/assistant/tool" % n)
    out = {"role": "system" if m["role"] == "developer" else m["role"]}
    if "content" in m:
        out["content"] = m["content"] if m["content"] is not None else ""
    if m["role"] == "assistant" and m.get("tool_calls"):
        calls = []
        for c in m["tool_calls"]:
            fn = c.get("function") or {}
            args = fn.get("arguments", "{}")
            calls.append({"id": c.get("id") or "call_%d_%d" % (n, len(calls)), "type": "function",
                          "function": {"name": fn.get("name", ""),
                                       "arguments": args if isinstance(args, str) else json.dumps(args)}})
        out["tool_calls"] = calls
        out.setdefault("content", "")
    if m["role"] == "tool":
        out["tool_call_id"] = m.get("tool_call_id", "")
        c = out.get("content", "")
        out["content"] = c if isinstance(c, str) else json.dumps(c)
    return out


def parse(text):
    """-> (items, errors). item = {"id", "messages", "tools", "kind": "session"|"request"}."""
    items, errors = [], []
    for i, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
            msgs = obj["messages"] if isinstance(obj, dict) else None
            if not isinstance(msgs, list) or not msgs:
                raise ValueError("needs a non-empty \"messages\" list")
            msgs = [_norm_message(m, k) for k, m in enumerate(msgs)]
            tools = obj.get("tools") or None
            if tools is not None and not isinstance(tools, list):
                raise ValueError("\"tools\" must be a list")
            kind = "session" if any(m["role"] == "assistant" for m in msgs) else "request"
            items.append({"id": str(obj.get("id") or "line-%d" % i), "messages": msgs, "tools": tools, "kind": kind})
        except (ValueError, KeyError, TypeError) as e:
            errors.append("line %d: %s" % (i, e))
    return items, errors


def brief():
    """The copy-paste brief for a user's coding agent, plus the example file."""
    return {"brief": (ROOT / "docs" / "data-brief.md").read_text(),
            "example": (ROOT / "examples" / "traces.example.jsonl").read_text()}


def stats(items):
    sessions = [x for x in items if x["kind"] == "session"]
    steps = sum(sum(m["role"] == "assistant" for m in x["messages"]) for x in sessions)
    tools = []
    for x in items:
        for t in x.get("tools") or []:
            n = (t.get("function") or {}).get("name")
            if n and n not in tools:
                tools.append(n)
        for m in x["messages"]:
            for c in m.get("tool_calls") or []:
                if c["function"]["name"] not in tools:
                    tools.append(c["function"]["name"])
    toks = [len(json.dumps(x["messages"], ensure_ascii=False)) / 3.6 for x in items]
    return {"sessions": len(sessions), "requests": len(items) - len(sessions), "steps": steps,
            "tools": tools[:20], "tokens_mean": int(sum(toks) / len(toks)) if toks else 0}


def save(name, content):
    if len(content.encode()) > MAX_BYTES:
        raise ValueError("file is larger than %d MB" % (MAX_BYTES // 2 ** 20))
    items, errors = parse(content)
    if not items:
        raise ValueError("no usable lines" + (": " + "; ".join(errors[:3]) if errors else ""))
    did = hashlib.sha256(content.encode()).hexdigest()[:8]
    UPLOADS.mkdir(parents=True, exist_ok=True)
    (UPLOADS / (did + ".jsonl")).write_text("\n".join(json.dumps(x) for x in items) + "\n")
    meta = dict(id=did, name=name, created=time.time(), errors=errors[:20], **stats(items))
    (UPLOADS / (did + ".json")).write_text(json.dumps(meta))
    return meta


def list_all():
    out = []
    for p in sorted(UPLOADS.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            out.append(json.loads(p.read_text()))
        except ValueError:
            pass
    return out


def delete(did):
    """Remove an uploaded dataset (its JSONL and its stats) from ~/.providerbench/uploads."""
    if not re.fullmatch(r"[0-9a-f]{8}", did or ""):
        raise ValueError("bad dataset id")
    found = False
    for ext in (".jsonl", ".json"):
        f = UPLOADS / (did + ext)
        if f.exists():
            f.unlink()
            found = True
    return found


def load(did):
    path = UPLOADS / (did + ".jsonl")
    if not path.exists():
        raise ValueError("no uploaded dataset %s" % did)
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def meta(did):
    try:
        return json.loads((UPLOADS / (did + ".json")).read_text())
    except (OSError, ValueError):
        return {"id": did, "name": did}
