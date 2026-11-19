"""GPQA-Diamond: 198 graduate-level multiple-choice science questions.

The GPQA authors ask that the questions not appear in plain text online. This suite downloads their
password-protected release at run time (password published in the GPQA README) and keeps the
questions on this machine only: tasks and results under results/ are local and git-ignored.
Never publish a run's tasks.json or raw replies for this suite.
"""
import csv
import io
import re
import zipfile

from .. import data
from ..failures import check_output
from .base import Suite, Task, Verdict

URL = "https://github.com/idavidrein/gpqa/raw/main/dataset.zip"
PASSWORD = b"deserted-untie-orchid"   # published in the GPQA README
MEMBER = "dataset/gpqa_diamond.csv"
LETTERS = "ABCD"

PROMPT = ("Answer the following multiple-choice question. Think step by step.\n\n{question}\n\n"
          "{choices}\n\nFinish with a line 'Answer: X' where X is A, B, C or D.")

# Order matters: the bare "(X)" pattern is last because it also matches the choice list echoed back.
_PATTERNS = [re.compile(p, re.I) for p in (
    r"Answer\s*:\s*\**\s*\(?([ABCD])\b\)?",
    r"answer is\s*:?\s*\**\(?([ABCD])\b\)?",
    r"correct (?:answer|option|choice) is\s*:?\s*\**\(?([ABCD])\b\)?",
    r"\(([ABCD])\)",
)]


def extract_letter(text):
    for rx in _PATTERNS:
        found = rx.findall(text or "")
        if found:
            return found[-1].upper()
    return None


def load_rows():
    raw = data.http_get(URL, binary=True, timeout=180)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        text = z.read(MEMBER, pwd=PASSWORD).decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(text)))
    rows.sort(key=lambda r: r["Record ID"])
    return rows


class GPQASuite(Suite):
    name = "gpqa"
    title = "GPQA-Diamond"
    degen_check = True      # beta: real prompts, so a degenerate reply means something
    method = [
        'GPQA-Diamond, choices shuffled per seed.',
        'Answer letter vs the key.',
        'Degeneration detection (beta) on every reply.',
    ]
    blurb = "Graduate-level science multiple choice (198 questions); choices shuffled per run."
    source = "https://github.com/idavidrein/gpqa"
    default_n = 20
    est_prompt_tokens = 400
    est_output_tokens = 6000
    default_max_tokens = 65536

    def params(self):
        return [{"key": "n", "label": "Tasks", "type": "int", "default": self.default_n, "min": 1, "max": 198},
                {"key": "max_tokens", "label": "Max output tokens", "type": "int", "default": self.default_max_tokens,
                 "min": 1024, "max": 131072,
                 "help": "Reasoning plus answer. Capped at what fits in the model's context."}]

    def load(self, rng, n, **params):
        max_tokens = int(params.get("max_tokens") or self.default_max_tokens)
        rows = load_rows()
        tasks = []
        for i in sorted(rng.sample(range(len(rows)), min(n, len(rows)))):
            r = rows[i]
            options = [r["Correct Answer"].strip()] + [r["Incorrect Answer %d" % k].strip() for k in (1, 2, 3)]
            order = list(range(4))
            rng.shuffle(order)
            correct = LETTERS[order.index(0)]
            choices = "\n".join("%s) %s" % (LETTERS[j], options[o]) for j, o in enumerate(order))
            tasks.append(Task(
                id="gpqa/" + r["Record ID"], suite=self.name,
                messages=[{"role": "user", "content": PROMPT.format(question=r["Question"].strip(),
                                                                    choices=choices)}],
                max_tokens=max_tokens, expect={"answer": correct},
                meta={"record_id": r["Record ID"], "domain": r.get("High-level domain", "")}))
        return tasks

    def grade(self, task, reply):
        codes = check_output(reply)
        content = (reply.get("message") or {}).get("content") or ""
        gold = task.expect["answer"]
        got = extract_letter(content)
        detail = ""
        if got is None:
            codes.append("no_answer")
            detail = "no 'Answer: X' line"
        elif got != gold:
            codes.append("wrong_answer")
            detail = "answered %s, correct is %s" % (got, gold)
        codes = sorted(set(codes))
        return Verdict("fail" if codes else "pass", codes, answer=got or "", expected=gold, detail=detail)
