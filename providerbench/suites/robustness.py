"""Robustness probes: small fixed checks for serving bugs that accuracy benchmarks miss.

Adapted from provider-benchmark's markup-safety and api-contract suites:

    think_echo     literal <think> / </think> in a sentence must come back verbatim in the answer
    think_file     the same tags inside a write_file tool call must survive byte for byte
    json_schema    response_format json_schema output must parse and match the schema
    parallel_tools three get_weather calls in one turn, one per city
    greeting       "hi" gets a normal, non-empty, Latin-script reply with no tags

The seeded rng only picks variants (sentence, file, person, cities, greeting), so prompts change
from run to run while the checks stay the same.
"""
import json
import re

from ..failures import check_args, check_output, check_tool_calls
from .base import Suite, Task, Verdict

OPEN, CLOSE = "<" + "think>", "</" + "think>"

SENTENCES = [
    "The markers are %s and %s." % (OPEN, CLOSE),
    "Wrap hidden notes in %s and end them with %s before replying." % (OPEN, CLOSE),
    "Our parser treats %s as the start and %s as the end of a block." % (OPEN, CLOSE),
    "Type %s, then your note, then %s, and press enter." % (OPEN, CLOSE),
]

FILE_TEMPLATE = ('pub const {a}: &str = "%s";\n'
                 'pub const {b}: &str = "%s";\n\n'
                 "#[test]\n"
                 "fn detects_tags() {{\n"
                 '    assert!("before %s after".contains({a}));\n'
                 '    assert!("closed %s too".contains({b}));\n'
                 "}}\n") % (OPEN, CLOSE, OPEN, CLOSE)
CONST_NAMES = [("OPEN_TAG", "CLOSE_TAG"), ("START", "END"), ("THINK_OPEN", "THINK_CLOSE"), ("BEGIN", "FINISH")]

WRITE_FILE = {"type": "function", "function": {
    "name": "write_file", "description": "Write a file to disk.",
    "parameters": {"type": "object", "properties": {
        "path": {"type": "string"}, "contents": {"type": "string"}}, "required": ["path", "contents"]}}}

PEOPLE = [
    ("Asha Rao", 34, ["Kannada", "English", "Hindi"]),
    ("Tomas Lindqvist", 51, ["Swedish", "German"]),
    ("Mei Chen", 27, ["Mandarin", "English"]),
    ("Kofi Mensah", 42, ["Twi", "English", "French"]),
]
PERSON_SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "age": {"type": "integer"},
                   "languages": {"type": "array", "items": {"type": "string"}}},
    "required": ["name", "age", "languages"],
    "additionalProperties": False,
}

GET_WEATHER = {"type": "function", "function": {
    "name": "get_weather", "description": "Get the current weather for one city.",
    "parameters": {"type": "object", "properties": {
        "city": {"type": "string", "description": "City name."},
        "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]}}, "required": ["city"]}}}
CITIES = ["Pune", "Oslo", "Lima", "Nairobi", "Hanoi", "Denver", "Porto", "Perth", "Quito", "Riga"]

GREETINGS = ["hi", "hello!", "hey there", "good morning"]

# Non-Latin scripts (Greek deliberately excluded: it is ordinary notation)
_FOREIGN = re.compile("[㐀-䶿一-鿿豈-﫿぀-ヿ가-힯"
                      "Ѐ-ԯ؀-ۿ֐-׿ऀ-෿฀-๿�]")

PROBES = ["think_echo", "think_file", "json_schema", "parallel_tools", "greeting"]


def _no_leak(codes):
    return [c for c in codes if c != "reasoning_leak"]


def _verdict(codes, **kw):
    codes = sorted(set(codes))
    if not codes:
        kw["detail"] = ""
    return Verdict("fail" if codes else "pass", codes, **kw)


class RobustnessSuite(Suite):
    name = "robustness"
    title = "Robustness"
    degen_check = True      # beta: real prompts, so a degenerate reply means something
    method = [
        'Serving-bug probes: literal <think> tags, JSON schema, parallel calls, greetings.',
        'Exact round-trip and schema checks.',
        'Degeneration detection (beta) on every reply.',
    ]
    blurb = "Serving-bug probes: literal <think> tags, structured output, parallel tool calls, greetings."
    source = ""
    default_n = 5
    est_prompt_tokens = 250
    est_output_tokens = 600

    def load(self, rng, n, **params):
        tasks = []
        for i in range(n):
            probe = PROBES[i % len(PROBES)]
            tasks.append(getattr(self, "_make_" + probe)(rng, "probe/%s/%d" % (probe, i)))
        return tasks

    # ---------------------------------------------------------------- builders

    def _task(self, tid, probe, messages, expect, tools=None, extra_body=None):
        return Task(id=tid, suite=self.name, messages=messages, tools=tools, max_tokens=4096,
                    extra_body=extra_body or {}, expect=dict(expect, probe=probe), meta={"probe": probe})

    def _make_think_echo(self, rng, tid):
        s = rng.choice(SENTENCES)
        return self._task(tid, "think_echo", [{"role": "user", "content":
                          "Reply with exactly this sentence and nothing else: " + s}], {"sentence": s})

    def _make_think_file(self, rng, tid):
        a, b = rng.choice(CONST_NAMES)
        body = FILE_TEMPLATE.format(a=a, b=b)
        path = rng.choice(["src/tags.rs", "src/markers.rs", "lib/think.rs"])
        ask = ("Call the write_file tool exactly once to create %s with EXACTLY this content, "
               "byte for byte:\n\n```rust\n%s```" % (path, body))
        return self._task(tid, "think_file", [{"role": "user", "content": ask}],
                          {"path": path, "contents": body}, tools=[WRITE_FILE])

    def _make_json_schema(self, rng, tid):
        name, age, langs = rng.choice(PEOPLE)
        text = "%s is %d years old and speaks %s." % (name, age, ", ".join(langs[:-1]) + " and " + langs[-1]
                                                      if len(langs) > 1 else langs[0])
        rf = {"type": "json_schema", "json_schema": {"name": "person", "strict": True, "schema": PERSON_SCHEMA}}
        return self._task(tid, "json_schema", [{"role": "user", "content":
                          "Extract the person described here as JSON: " + text}],
                          {"name": name, "age": age, "languages": langs}, extra_body={"response_format": rf})

    def _make_parallel_tools(self, rng, tid):
        cities = rng.sample(CITIES, 3)
        ask = ("What's the weather right now in %s, %s and %s? Call get_weather once for each city, "
               "all in this one turn." % tuple(cities))
        return self._task(tid, "parallel_tools", [{"role": "user", "content": ask}], {"cities": cities},
                          tools=[GET_WEATHER])

    def _make_greeting(self, rng, tid):
        g = rng.choice(GREETINGS)
        return self._task(tid, "greeting", [{"role": "user", "content": g}], {"greeting": g})

    # ---------------------------------------------------------------- graders

    def grade(self, task, reply):
        return getattr(self, "_grade_" + task.expect["probe"])(task, reply)

    def _grade_think_echo(self, task, reply):
        msg = reply.get("message") or {}
        content, reasoning = msg.get("content") or "", msg.get("reasoning") or ""
        s = task.expect["sentence"]
        codes, detail = _no_leak(check_output(reply)), ""
        if s not in content:
            codes.append("mismatch")
            detail = ("the sentence was split into the reasoning channel" if CLOSE in reasoning or OPEN in reasoning
                      else "reply differs: %r" % content[:160])
        return _verdict(codes, answer=content[:300], expected=s, detail=detail)

    def _grade_think_file(self, task, reply):
        codes = _no_leak(check_output(reply))
        tc, calls, detail = check_tool_calls(reply, task.tools)
        codes += tc
        want = task.expect["contents"]
        got = ""
        if not calls:
            codes.append("missing_tool_call")
            detail = "no tool call"
        else:
            got = (calls[0]["args"] or {}).get("contents") or ""
            if got.rstrip("\n") != want.rstrip("\n"):
                codes.append("mismatch")
                detail = detail or ("tags missing from the file" if OPEN not in got or CLOSE not in got
                                    else "file contents differ")
        return _verdict(codes, answer=got[:400], expected=want, detail=detail)

    def _grade_json_schema(self, task, reply):
        codes = check_output(reply)
        content = ((reply.get("message") or {}).get("content") or "").strip()
        exp = {k: task.expect[k] for k in ("name", "age", "languages")}
        detail = ""
        try:
            obj = json.loads(content)
        except ValueError:
            obj = None
            codes.append("bad_json")
            detail = "not JSON: %r" % content[:120]
        if obj is not None:
            why = check_args(obj, PERSON_SCHEMA)
            extra = isinstance(obj, dict) and set(obj) - set(PERSON_SCHEMA["properties"])
            if why or extra or not all(isinstance(x, str) for x in (obj.get("languages") or [])):
                codes.append("bad_json")
                detail = why or ("extra keys %s" % sorted(extra) if extra else "languages must be strings")
            elif (obj["name"].strip().lower() != exp["name"].lower() or obj["age"] != exp["age"]
                  or sorted(x.lower() for x in obj["languages"]) != sorted(x.lower() for x in exp["languages"])):
                codes.append("mismatch")
                detail = "extracted values differ"
        return _verdict(codes, answer=content[:300], expected=json.dumps(exp), detail=detail)

    def _grade_parallel_tools(self, task, reply):
        codes = check_output(reply)
        tc, calls, detail = check_tool_calls(reply, task.tools)
        want = sorted(c.lower() for c in task.expect["cities"])
        got = sorted(((c["args"] or {}).get("city") or "").strip().lower() for c in calls)
        if not calls:
            codes.append("missing_tool_call")
            detail = "no tool call"
        else:
            codes += tc
            if got != want:
                codes.append("wrong_args")
                detail = detail or "cities %s, wanted %s" % (got, want)
        return _verdict(codes, answer=", ".join(got), expected=", ".join(want), detail=detail)

    def _grade_greeting(self, task, reply):
        codes = check_output(reply)
        content = ((reply.get("message") or {}).get("content") or "").strip()
        detail = ""
        if not content:
            codes.append("no_answer")
            detail = "empty reply"
        elif len(_FOREIGN.findall(content)) >= 3:
            codes.append("degenerate")
            detail = "non-Latin script in an English greeting"
        return _verdict(codes, answer=content[:300], expected="a short greeting", detail=detail)
