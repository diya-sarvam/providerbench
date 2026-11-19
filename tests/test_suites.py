"""Grader tests with hand-built replies (offline), plus an opt-in network test (PB_NET=1)."""
import json
import os
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from providerbench.suites import SUITES  # noqa: E402
from providerbench.suites import math as m  # noqa: E402
from providerbench.suites import robustness as rb  # noqa: E402
from providerbench.suites.base import Task  # noqa: E402
from providerbench.suites.gpqa import extract_letter  # noqa: E402
from providerbench.suites.tools import to_tools  # noqa: E402


def reply(content="", reasoning="", calls=None, finish="stop"):
    msg = {"role": "assistant", "content": content, "reasoning": reasoning}
    if calls:
        msg["tool_calls"] = [{"id": "c%d" % i, "type": "function",
                              "function": {"name": n, "arguments": a if isinstance(a, str) else json.dumps(a)}}
                             for i, (n, a) in enumerate(calls)]
    return {"message": msg, "finish_reason": finish}


class MathTests(unittest.TestCase):
    S = SUITES["math"]

    def t(self, ans):
        return Task(id="x", suite="math", messages=[], expect={"answer": ans})

    def test_boxed_nested(self):
        self.assertEqual(m.last_boxed(r"so \boxed{1} no wait \boxed{\frac{3}{4}}."), r"\frac{3}{4}")

    def test_equal_forms(self):
        for pred, gold in [(r"\dfrac{3}{4}", r"\frac34"), ("0.75", r"\frac{3}{4}"), ("10.0", "10"),
                           (r"90^\circ", "90"), (r"\left( 3, \frac{\pi}{2} \right)", r"(3,\frac{\pi}{2})"),
                           ("x = 5", "5"), ("1,000", "1000"), (r"5\text{ cm}", "5"), ("-2, 1", "1,-2"),
                           (r"\text{(C)}", "(C)"), ("3/4", "0.75")]:
            self.assertTrue(m.equal(pred, gold), (pred, gold))
        for pred, gold in [("4", "5"), ("(1,2)", "[1,2]"), ("", "0"), (r"\frac{1}{3}", "0.33")]:
            self.assertFalse(m.equal(pred, gold), (pred, gold))

    def test_grade(self):
        self.assertEqual(self.S.grade(self.t("70"), reply(r"... thus \boxed{70}")).verdict, "pass")
        v = self.S.grade(self.t("70"), reply(r"\boxed{71}"))
        self.assertEqual((v.verdict, v.codes), ("fail", ["wrong_answer"]))
        self.assertEqual(self.S.grade(self.t("70"), reply("I think 70")).codes, ["no_answer"])
        self.assertIn("truncated", self.S.grade(self.t("70"), reply("", finish="length")).codes)
        self.assertEqual(self.S.grade(self.t("70"), reply("The answer is 70.")).verdict, "pass")


class GPQATests(unittest.TestCase):
    def test_extract(self):
        self.assertEqual(extract_letter("Option (A) is wrong... Answer: C"), "C")
        self.assertEqual(extract_letter("the answer is (b)"), "B")
        self.assertEqual(extract_letter("between (A) and (D), (D)"), "D")
        self.assertIsNone(extract_letter("no idea"))

    def test_grade(self):
        t = Task(id="g", suite="gpqa", messages=[], expect={"answer": "B"})
        s = SUITES["gpqa"]
        self.assertEqual(s.grade(t, reply("reasoning...\nAnswer: B")).verdict, "pass")
        self.assertEqual(s.grade(t, reply("Answer: A")).codes, ["wrong_answer"])
        self.assertEqual(s.grade(t, reply("<think>x</think>Answer: B")).codes, ["reasoning_leak"])


class ToolsTests(unittest.TestCase):
    S = SUITES["tools"]
    FUNCS = [{"name": "math.hypot", "description": "", "parameters": {"type": "dict", "properties": {
        "x": {"type": "integer"}, "y": {"type": "integer"}, "z": {"type": "float"}}, "required": ["x", "y"]}},
        {"name": "get_city", "description": "", "parameters": {"type": "dict", "properties": {
            "city": {"type": "string"}, "opts": {"type": "dict", "properties": {"unit": {"type": "string"}}}},
            "required": ["city"]}}]

    def task(self, gt, cat="simple_python"):
        tools, names = to_tools(self.FUNCS)
        return Task(id="b", suite="tools", messages=[], tools=tools,
                    expect={"category": cat, "ground_truth": gt, "names": names})

    def test_convert(self):
        tools, names = to_tools(self.FUNCS)
        self.assertEqual(tools[0]["function"]["name"], "math_hypot")
        self.assertEqual(names["math_hypot"], "math.hypot")
        self.assertEqual(tools[0]["function"]["parameters"]["type"], "object")
        self.assertEqual(tools[0]["function"]["parameters"]["properties"]["z"]["type"], "number")

    def test_simple(self):
        gt = [{"math.hypot": {"x": [4], "y": [5], "z": ["", 0]}}]
        self.assertEqual(self.S.grade(self.task(gt), reply(calls=[("math_hypot", {"x": 4, "y": 5})])).verdict, "pass")
        self.assertEqual(self.S.grade(self.task(gt), reply(calls=[("math_hypot", {"x": 4, "y": 5, "z": 0.0})])).verdict, "pass")
        self.assertEqual(self.S.grade(self.task(gt), reply(calls=[("math_hypot", {"x": 4, "y": 6})])).codes, ["wrong_args"])
        self.assertEqual(self.S.grade(self.task(gt), reply(calls=[("get_city", {"city": "x"})])).codes, ["wrong_tool"])
        self.assertEqual(self.S.grade(self.task(gt), reply("no")).codes, ["missing_tool_call"])
        self.assertEqual(self.S.grade(self.task(gt), reply(calls=[("math_hypot", "{bad")])).codes,
                         ["tool_call_invalid_json"])
        self.assertIn("tool_args_schema", self.S.grade(self.task(gt), reply(calls=[("math_hypot", {"x": 4})])).codes)

    def test_parallel_and_nested(self):
        gt = [{"get_city": {"city": ["New York", "NYC"]}}, {"get_city": {"city": ["Paris"],
              "opts": [{"unit": ["c", "celsius"]}, ""]}}]
        ok = reply(calls=[("get_city", {"city": "paris", "opts": {"unit": "Celsius"}}), ("get_city", {"city": "new york"})])
        self.assertEqual(self.S.grade(self.task(gt, "parallel"), ok).verdict, "pass")
        one = reply(calls=[("get_city", {"city": "paris"})])
        self.assertEqual(self.S.grade(self.task(gt, "parallel"), one).codes, ["wrong_call_count"])

    def test_irrelevance(self):
        t = self.task(None, "irrelevance")
        self.assertEqual(self.S.grade(t, reply("I can't help with that.")).verdict, "pass")
        self.assertEqual(self.S.grade(t, reply(calls=[("get_city", {"city": "x"})])).codes, ["unexpected_tool_call"])


class AgenticTests(unittest.TestCase):
    S = SUITES["agentic"]

    def task(self, name="str_replace_editor", args=None):
        from providerbench.suites.agentic import tools_for
        args = {"command": "view", "path": "/testbed/a.py"} if args is None else args
        conv = [{"role": "assistant", "content": "", "tool_calls": [
            {"id": "1", "type": "function", "function": {"name": "str_replace_editor", "arguments": "{}"}},
            {"id": "2", "type": "function", "function": {"name": "bash", "arguments": "{}"}}]}]
        rec = {"role": "assistant", "content": "", "tool_calls": [
            {"id": "r", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}
        return Task(id="a", suite="agentic", messages=[], tools=tools_for(conv), expect={"recorded": rec})

    def test_match_levels(self):
        # replay is not graded for accuracy: the match is recorded as reference, only protocol errors fail
        g = lambda t, *calls: self.S.grade(t, reply(calls=list(calls)))  # noqa: E731
        v = g(self.task(), ("str_replace_editor", {"command": "view", "path": "/testbed/a.py"}))
        self.assertEqual((v.verdict, v.match), ("pass", "exact"))
        v = g(self.task(), ("str_replace_editor", {"command": "view", "path": "/testbed/a.py/", "view_range": [1, 40]}))
        self.assertEqual((v.verdict, v.match), ("pass", "same_action"))
        v = g(self.task(), ("str_replace_editor", {"command": "view", "path": "/testbed/b.py"}))
        self.assertEqual((v.verdict, v.match, v.codes), ("pass", "same_tool", []))
        v = g(self.task(), ("bash", {"command": "ls"}))
        self.assertEqual((v.verdict, v.match, v.codes), ("pass", "other_tool", []))
        b = self.task("bash", {"command": "cd /testbed &&  python  t.py"})
        self.assertEqual(g(b, ("bash", {"command": "cd /testbed && python t.py"})).match, "exact")
        v = self.S.grade(self.task(), reply("I will look"))
        self.assertEqual((v.verdict, v.match), ("pass", "no_call"))
        self.assertEqual(self.S.grade(self.task(), reply("")).codes, ["no_answer"])
        self.assertIn("tool_call_unknown_tool", g(self.task(), ("python", {})).codes)
        self.assertIn("tool_args_schema", g(self.task("bash", {"command": "ls"}), ("bash", {})).codes)
        self.assertFalse(self.S.graded)

    def test_text_step_and_request(self):
        t = Task(id="a", suite="agentic", messages=[], expect={"recorded": {"role": "assistant", "content": "done"}})
        self.assertEqual(self.S.grade(t, reply("All fixed.")).match, "text")
        self.assertEqual(self.S.grade(t, reply("All fixed.")).verdict, "pass")
        r = Task(id="b", suite="agentic", messages=[], expect={"recorded": None})
        self.assertEqual(self.S.grade(r, reply("hello")).verdict, "pass")

    def test_upload_windows(self):
        import random
        from providerbench import datasets
        items, errors = datasets.parse((Path(__file__).parent.parent / "examples" / "traces.example.jsonl").read_text())
        self.assertEqual(errors, [])
        self.assertEqual([i["kind"] for i in items], ["session", "request"])
        from providerbench.suites.agentic import plan_window, est_tokens
        steps, j = plan_window(random.Random(1), items[0]["messages"], 0, 2, 0, 10 ** 6)
        self.assertEqual(len(steps), 3)
        self.assertIn(j, (0, 1))


class SyntheticTests(unittest.TestCase):
    S = SUITES["synthetic"]

    def test_shape_and_determinism(self):
        import random
        a = self.S.load(random.Random(5), 3, isl=1000, osl=100, cache_hit=0.5, turns=3, turn_isl=50)
        b = self.S.load(random.Random(5), 3, isl=1000, osl=100, cache_hit=0.5, turns=3, turn_isl=50)
        self.assertEqual([t.to_dict() for t in a], [t.to_dict() for t in b])
        self.assertEqual(len(a), 9)
        first = [t.messages[1]["content"] for t in a if t.turn == 0]
        shared = first[0][:1500]
        self.assertTrue(all(f.startswith(shared) for f in first))       # shared prefix across sessions
        self.assertNotEqual(first[0], first[1])                         # unique tail
        s0 = [t for t in a if t.group == "syn/0"]
        self.assertEqual(s0[1].messages[:2], s0[0].messages[:2])         # history re-sent
        self.assertEqual(len(s0[2].messages), 6)

    def test_grade(self):
        t = self.S.load(__import__("random").Random(1), 1, osl=100)[0]
        self.assertEqual(self.S.grade(t, dict(reply("x"), completion_tokens=100, finish_reason="length")).verdict, "pass")
        self.assertEqual(self.S.grade(t, dict(reply("x"), completion_tokens=20, finish_reason="stop")).codes, ["short_output"])

    def test_convert(self):
        from providerbench.suites.agentic import convert_trajectory
        raw = [{"role": "system", "content": "sys"}, {"role": "user", "content": "fix it"},
               {"role": "assistant", "content": "look <function=bash><parameter=command>ls</parameter></function>"},
               {"role": "user", "content": "OBSERVATION: a.py"}]
        conv = convert_trajectory(raw)
        self.assertEqual([x["role"] for x in conv], ["user", "assistant", "tool"])
        self.assertEqual(json.loads(conv[1]["tool_calls"][0]["function"]["arguments"]), {"command": "ls"})


class RobustnessTests(unittest.TestCase):
    S = SUITES["robustness"]

    def tasks(self):
        return {t.expect["probe"]: t for t in self.S.load(random.Random(3), 5)}

    def test_load_offline(self):
        t1 = [t.to_dict() for t in self.S.load(random.Random(7), 7)]
        t2 = [t.to_dict() for t in self.S.load(random.Random(7), 7)]
        self.assertEqual(t1, t2)
        self.assertEqual(len({t["id"] for t in t1}), 7)

    def test_think_echo(self):
        t = self.tasks()["think_echo"]
        s = t.expect["sentence"]
        self.assertEqual(self.S.grade(t, reply(s)).verdict, "pass")
        v = self.S.grade(t, reply("The markers are and .", reasoning=rb.OPEN))
        self.assertEqual(v.codes, ["mismatch"])
        self.assertIn("reasoning channel", v.detail)

    def test_think_file(self):
        t = self.tasks()["think_file"]
        good = reply(calls=[("write_file", {"path": t.expect["path"], "contents": t.expect["contents"]})])
        self.assertEqual(self.S.grade(t, good).verdict, "pass")
        bad = reply(calls=[("write_file", {"path": "x", "contents": t.expect["contents"].replace(rb.OPEN, "")})])
        self.assertEqual(self.S.grade(t, bad).codes, ["mismatch"])

    def test_json_schema(self):
        t = self.tasks()["json_schema"]
        e = t.expect
        ok = json.dumps({"name": e["name"], "age": e["age"], "languages": list(reversed(e["languages"]))})
        self.assertEqual(self.S.grade(t, reply(ok)).verdict, "pass")
        self.assertEqual(self.S.grade(t, reply("```json\n{}\n```")).codes, ["bad_json"])
        self.assertEqual(self.S.grade(t, reply(json.dumps({"name": e["name"], "age": "x", "languages": []}))).codes,
                         ["bad_json"])
        self.assertEqual(self.S.grade(t, reply(json.dumps({"name": "Bob", "age": e["age"],
                                                          "languages": e["languages"]}))).codes, ["mismatch"])

    def test_parallel(self):
        t = self.tasks()["parallel_tools"]
        calls = [("get_weather", {"city": c}) for c in t.expect["cities"]]
        self.assertEqual(self.S.grade(t, reply(calls=calls)).verdict, "pass")
        self.assertEqual(self.S.grade(t, reply(calls=calls[:2])).codes, ["wrong_args"])

    def test_greeting(self):
        t = self.tasks()["greeting"]
        self.assertEqual(self.S.grade(t, reply("Hello! How can I help?")).verdict, "pass")
        self.assertEqual(self.S.grade(t, reply("你好！我能帮你什么")).codes, ["degenerate"])
        self.assertEqual(self.S.grade(t, reply("")).codes, ["no_answer"])
        self.assertEqual(self.S.grade(t, reply("<think>hm</think>Hi")).codes, ["reasoning_leak"])


@unittest.skipUnless(os.environ.get("PB_NET") == "1", "network test: set PB_NET=1")
class NetworkLoadTests(unittest.TestCase):
    def test_load_all(self):
        for name, suite in SUITES.items():
            with self.subTest(suite=name):
                a = [t.to_dict() for t in suite.load(random.Random(1), 3)]
                b = [t.to_dict() for t in suite.load(random.Random(1), 3)]
                self.assertEqual(a, b, "not deterministic")
                self.assertTrue(a, "no tasks")
                ids = [t["id"] for t in a]
                self.assertEqual(len(ids), len(set(ids)))
                for t in a:
                    self.assertEqual(t["suite"], name)
                    self.assertTrue(t["messages"])
                    json.dumps(t)
                print("\n  %s: %d tasks, e.g. %s" % (name, len(a), ids[:3]), end="")


if __name__ == "__main__":
    unittest.main()
