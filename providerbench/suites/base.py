"""The contract every suite follows.

A suite turns a public benchmark into Tasks (one chat request each) and grades a reply.
Sampling is driven by the run's `random.Random(seed)`, so the same seed gives the same tasks,
byte for byte, and every target in a run gets exactly the same Tasks.

    class MySuite(Suite):
        name = "mysuite"
        def load(self, rng, n): ...          # -> list[Task]
        def grade(self, task, reply): ...    # -> Verdict

Grading must be deterministic code: no LLM judge.
"""
import json
from dataclasses import dataclass, field, asdict


@dataclass
class Task:
    id: str                          # stable and unique within the suite, e.g. "math500/test-123"
    suite: str
    messages: list                   # OpenAI chat messages, sent as-is (after the run nonce is added)
    tools: list = None               # OpenAI tool definitions, or None
    max_tokens: int = 16384
    extra_body: dict = field(default_factory=dict)   # e.g. {"response_format": {...}}
    expect: dict = field(default_factory=dict)       # grading data; never sent to the provider
    meta: dict = field(default_factory=dict)         # shown in the UI: source, category, ...
    group: str = None                # tasks sharing a group run in `turn` order on one worker
    turn: int = 0                    # 0 = first request of its group (cold), 1+ = warm

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(**d)


@dataclass
class Verdict:
    verdict: str                     # "pass" | "fail"
    codes: list = field(default_factory=list)   # failure codes from providerbench.failures, empty on pass
    answer: str = ""                 # what was extracted from the reply, for display
    expected: str = ""               # what was wanted, for display
    detail: str = ""                 # one line on why
    score: float = None              # optional partial credit / agreement, 0..1
    match: str = None                # replay only: exact | same_action | same_tool | other_tool | no_call | text

    def to_dict(self):
        return asdict(self)


class Suite:
    name = ""                        # short id used on the CLI and in results
    title = ""                       # shown in the UI
    blurb = ""                       # one line: what it measures and where the data comes from
    source = ""                      # dataset URL
    scenario = "quality"             # replay | synthetic | quality
    graded = True                    # False: no right answer (load tests); excluded from accuracy
    entropy_ratio = 6.0              # degeneration pre-filter: zlib ratio above this flags a reply
    length_stop_expected = False     # True: stopping at max_tokens is the point, not a collapse
    degen_check = False              # run the degeneration detectors (beta: only where prompts are real tasks)
    degen_signal = "high"            # how much a degeneration flag means here: high (real tasks) | low
    degen_note = ""                  # shown next to flags from this suite
    method = []                      # short bullets: what is sent, how it is checked
    default_n = 10
    est_prompt_tokens = 500          # per task, for the pre-run cost estimate
    est_output_tokens = 2000         # per task, for the pre-run cost estimate

    def params(self):
        """Parameters the UI renders (see docs/api.md). `n` is always first."""
        return [{"key": "n", "label": "Tasks", "type": "int", "default": self.default_n, "min": 1, "max": 1000}]

    def defaults(self):
        return {p["key"]: p["default"] for p in self.params()}

    def load(self, rng, n, **params):
        raise NotImplementedError

    def preview(self, tasks):
        """Rows for the preview table, one per group. Suites may override for better labels."""
        groups = {}
        for t in tasks:
            groups.setdefault(t.group or t.id, []).append(t)
        rows = []
        for gid, ts in groups.items():
            ts.sort(key=lambda t: t.turn)
            rows.append({"id": gid, "label": ts[0].meta.get("label") or gid, "turns": len(ts),
                         "prompt_tokens": [int(len(json.dumps(t.messages, ensure_ascii=False)) / 3.6) for t in ts],
                         "expected": [t.meta.get("expected_label", "") for t in ts if t.meta.get("expected_label")],
                         "tools": [x["function"]["name"] for x in (ts[0].tools or [])],
                         "url": ts[0].meta.get("url")})
        return rows

    def grade(self, task, reply):
        """`reply` is the dict from providerbench.client.chat(): reply["message"] has content, reasoning,
        tool_calls ([{id, type, function: {name, arguments(str)}}]); reply["finish_reason"]."""
        raise NotImplementedError

    def info(self):
        return {"name": self.name, "title": self.title, "blurb": self.blurb, "source": self.source,
                "default_n": self.default_n, "scenario": self.scenario, "graded": self.graded,
                "method": self.method, "params": self.params(), "degen_check": self.degen_check}
