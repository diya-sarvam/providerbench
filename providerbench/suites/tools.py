"""Tool calling: BFCL v4 single-turn categories, graded like BFCL's AST checker.

Data: the Berkeley Function Calling Leaderboard v4 files in the gorilla repo
(berkeley-function-call-leaderboard/bfcl_eval/data), categories simple_python, multiple, parallel,
parallel_multiple and irrelevance. Functions are converted to OpenAI tools the way BFCL's
function-calling mode does it ("dict" -> "object", "float" -> "number", "." in names -> "_").
"""
import json
import re

from .. import data
from ..failures import check_output, check_tool_calls
from .base import Suite, Task, Verdict

BASE = ("https://raw.githubusercontent.com/ShishirPatil/gorilla/main/"
        "berkeley-function-call-leaderboard/bfcl_eval/data/")
CATEGORIES = ("simple_python", "multiple", "parallel", "parallel_multiple", "irrelevance")
TYPE_MAP = {"dict": "object", "float": "number", "tuple": "array", "any": None}


def load_category(cat):
    items = data.jsonl(data.http_get(BASE + "BFCL_v4_%s.json" % cat))
    if cat == "irrelevance":
        answers = {}
    else:
        answers = {a["id"]: a["ground_truth"]
                   for a in data.jsonl(data.http_get(BASE + "possible_answer/BFCL_v4_%s.json" % cat))}
    return items, answers


def convert_schema(s):
    if not isinstance(s, dict):
        return s
    s = dict(s)
    if "type" in s:
        t = TYPE_MAP.get(s["type"], s["type"])
        if t is None:
            s.pop("type")
        else:
            s["type"] = t
    if isinstance(s.get("properties"), dict):
        s["properties"] = {k: convert_schema(v) for k, v in s["properties"].items()}
    if "items" in s:
        s["items"] = convert_schema(s["items"])
    if isinstance(s.get("additionalProperties"), dict):
        s["additionalProperties"] = convert_schema(s["additionalProperties"])
    return s


def oai_name(name):
    return name.replace(".", "_")


def to_tools(functions):
    tools, names = [], {}
    for f in functions:
        n = oai_name(f["name"])
        names[n] = f["name"]
        params = convert_schema(f.get("parameters") or {"type": "object", "properties": {}})
        params.setdefault("type", "object")
        tools.append({"type": "function", "function": {
            "name": n, "description": f.get("description", ""), "parameters": params}})
    return tools, names


# --------------------------------------------------------------------------- AST-style matching

_STD = re.compile(r"[ ,./\-_*^]")


def std(s):
    return _STD.sub("", s).lower().replace("'", '"')


def value_equal(v, a):
    """Model value `v` against one allowed ground-truth value `a`."""
    if isinstance(a, bool) or isinstance(v, bool):
        return isinstance(a, bool) and isinstance(v, bool) and a == v
    if isinstance(a, (int, float)) and isinstance(v, (int, float)):
        return abs(float(v) - float(a)) <= 1e-9 * max(1.0, abs(float(a)))
    if isinstance(a, str) and isinstance(v, str):
        return std(v) == std(a)
    if isinstance(a, list) and isinstance(v, list):
        return len(a) == len(v) and all(value_equal(x, y) for x, y in zip(v, a))
    if isinstance(a, dict) and isinstance(v, dict):
        # nested ground truth: each key maps to a list of allowed values
        for k in v:
            if k not in a:
                return False
        for k, allowed in a.items():
            allowed = allowed if isinstance(allowed, list) else [allowed]
            if k not in v:
                if "" not in allowed:
                    return False
                continue
            if not any(value_equal(v[k], x) for x in allowed if x != ""):
                return False
        return True
    return v == a


def args_match(args, gt_params):
    """None if `args` satisfies the ground-truth params, else a reason."""
    for k in args:
        if k not in gt_params:
            return "unexpected argument %r" % k
    for k, allowed in gt_params.items():
        allowed = allowed if isinstance(allowed, list) else [allowed]
        if k not in args:
            if "" not in allowed:
                return "missing argument %r" % k
            continue
        if not any(value_equal(args[k], a) for a in allowed if a != "" or args[k] == ""):
            return "%s=%s, allowed %s" % (k, json.dumps(args[k])[:60], json.dumps(allowed)[:80])
    return None


def match_calls(calls, ground_truth, names):
    """calls: [{"name", "args"}] (OpenAI names); ground_truth: [{orig_name: {param: [allowed]}}].
    Returns (code or None, detail)."""
    if len(calls) != len(ground_truth):
        return "wrong_call_count", "expected %d call(s), got %d" % (len(ground_truth), len(calls))
    free = list(range(len(calls)))
    for gt in ground_truth:
        (gname, gparams), = gt.items()
        same_name = [i for i in free if names.get(calls[i]["name"], calls[i]["name"]) == gname]
        if not same_name:
            return "wrong_tool", "no call to %s" % gname
        why = None
        for i in same_name:
            why = args_match(calls[i]["args"] or {}, gparams)
            if why is None:
                free.remove(i)
                break
        else:
            return "wrong_args", "%s: %s" % (gname, why)
    return None, ""


def describe(calls):
    return "; ".join("%s(%s)" % (c["name"], json.dumps(c["args"], ensure_ascii=False)[:200]) for c in calls)


class ToolsSuite(Suite):
    name = "tools"
    title = "Tool calling"
    degen_check = True      # beta: real prompts, so a degenerate reply means something
    method = [
        'BFCL v4 prompts with their function schemas.',
        'Checks function names, call count and argument values.',
        'Degeneration detection (beta) on every reply.',
    ]
    blurb = "BFCL v4 single-turn: simple, multiple, parallel, parallel-multiple and irrelevance."
    source = "https://github.com/ShishirPatil/gorilla/tree/main/berkeley-function-call-leaderboard"
    default_n = 30
    est_prompt_tokens = 600
    est_output_tokens = 400

    def _allocate(self, rng, n, sizes):
        total = sum(sizes.values())
        if n < 5:
            pool = [(c, i) for c in CATEGORIES for i in range(sizes[c])]
            picks = rng.sample(pool, min(n, len(pool)))
            return {c: sorted(i for cc, i in picks if cc == c) for c in CATEGORIES}
        alloc = {c: max(1, round(n * sizes[c] / total)) for c in CATEGORIES}
        while sum(alloc.values()) > n:
            c = max(alloc, key=lambda k: alloc[k])
            alloc[c] -= 1
        while sum(alloc.values()) < n:
            c = max(CATEGORIES, key=lambda k: sizes[k] - alloc[k])
            alloc[c] += 1
        return {c: sorted(rng.sample(range(sizes[c]), min(alloc[c], sizes[c]))) for c in CATEGORIES}

    def load(self, rng, n, **params):
        cats = {c: load_category(c) for c in CATEGORIES}
        picks = self._allocate(rng, n, {c: len(cats[c][0]) for c in CATEGORIES})
        tasks = []
        for c in CATEGORIES:
            items, answers = cats[c]
            for i in picks[c]:
                it = items[i]
                tools, names = to_tools(it["function"])
                tasks.append(Task(
                    id="bfcl/" + it["id"], suite=self.name, messages=list(it["question"][0]),
                    tools=tools, max_tokens=4096,
                    expect={"category": c, "ground_truth": answers.get(it["id"]), "names": names},
                    meta={"category": c}))
        return tasks

    def grade(self, task, reply):
        codes = check_output(reply)
        tc_codes, calls, detail = check_tool_calls(reply, task.tools)
        gt = task.expect.get("ground_truth")
        expected = "no tool call" if gt is None else json.dumps(gt, ensure_ascii=False)[:400]
        if task.expect.get("category") == "irrelevance":
            if calls:
                codes.append("unexpected_tool_call")
                detail = detail or "called %s" % ", ".join(c["name"] for c in calls)
        elif not calls:
            codes.append("missing_tool_call")
            detail = "no tool call in the reply"
        else:
            codes += tc_codes
            if not any(c["args"] is None for c in calls):
                code, why = match_calls(calls, gt, task.expect.get("names") or {})
                if code:
                    codes.append(code)
                    detail = detail or why
        codes = sorted(set(codes))
        return Verdict("fail" if codes else "pass", codes, answer=describe(calls), expected=expected,
                       detail=detail if codes else "")
