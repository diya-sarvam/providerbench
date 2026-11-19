"""Math: MATH-500 and AIME 2025, graded by the last \\boxed{} answer.

Data: HuggingFaceH4/MATH-500 (test split) and MathArena/aime_2025 (train split), via the
Hugging Face datasets-server. About three quarters of each sample is MATH-500, one quarter AIME.
"""
import re
from fractions import Fraction

from .. import data
from ..failures import check_output
from .base import Suite, Task, Verdict

MATH500 = ("HuggingFaceH4/MATH-500", "test")
AIME25 = ("MathArena/aime_2025", "train")

PROMPT = ("Solve the following problem. Reason step by step, then put the final answer, and only "
          "the final answer, in \\boxed{{}}.\n\n{problem}")


# --------------------------------------------------------------------------- extraction

def last_boxed(text):
    """Contents of the last \\boxed{...} / \\fbox{...} in `text`, braces matched; None if absent."""
    text = text or ""
    idx = max(text.rfind("\\boxed"), text.rfind("\\fbox"))
    if idx < 0:
        return None
    rest = text[idx:]
    head = re.match(r"\\(?:boxed|fbox)\s*", rest)
    i = head.end()
    if i < len(rest) and rest[i] != "{":          # "\boxed 5"
        m = re.match(r"[^\s$]+", rest[i:])
        return m.group(0) if m else None
    depth = 0
    for j in range(i, len(rest)):
        if rest[j] == "{":
            depth += 1
        elif rest[j] == "}":
            depth -= 1
            if depth == 0:
                return rest[i + 1:j]
    return None


_FALLBACK = [re.compile(r"answer is[:\s]*\$*([^\n$]+?)\$*\s*(?:\.\s*)?$", re.I | re.M),
             re.compile(r"^\s*\**answer\**\s*:\s*\$*([^\n$]+?)\$*\s*$", re.I | re.M)]


def extract(text):
    box = last_boxed(text)
    if box is not None:
        return box
    for rx in _FALLBACK:
        found = rx.findall(text or "")
        if found:
            return found[-1].strip()
    return None


# --------------------------------------------------------------------------- normalisation

def _unwrap(s, cmd):
    """Replace \\cmd{X} with X (one level of nested braces allowed)."""
    return re.sub(r"\\" + cmd + r"\s*\{((?:[^{}]|\{[^{}]*\})*)\}", r"\1", s)


def norm(s):
    s = (s or "").strip()
    s = re.sub(r"(\d)\s*\\(?:text|mbox|mathrm)\{\s*[a-zA-Z ]+\}", r"\1", s)   # units after numbers
    for cmd in ("text", "textbf", "mbox", "mathrm", "mathbf", "boxed"):
        s = _unwrap(s, cmd)
    for tok in ("\\left", "\\right", "\\!", "\\,", "\\;", "\\:", "\\ ", "$", "\\displaystyle"):
        s = s.replace(tok, "")
    s = s.replace("\\dfrac", "\\frac").replace("\\tfrac", "\\frac")
    s = re.sub(r"\^\s*\{?\\circ\}?", "", s).replace("\\circ", "")
    s = s.replace("\\%", "").replace("%", "")
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"\\frac(\d)(\d)", r"\\frac{\1}{\2}", s)
    s = re.sub(r"\\frac\{([^{}]*)\}(\d)", r"\\frac{\1}{\2}", s)
    s = re.sub(r"\\sqrt(\d+)", r"\\sqrt{\1}", s)
    if re.fullmatch(r"-?\d{1,3}(,\d{3})+(\.\d+)?", s):
        s = s.replace(",", "")
    s = re.sub(r"(\d)\.0+(?!\d)", r"\1", s)
    s = s.rstrip(".")
    return s


def to_number(s):
    s = norm(s)
    m = re.fullmatch(r"(-?)\\frac\{(-?\d+(?:\.\d+)?)\}\{(-?\d+(?:\.\d+)?)\}", s)
    if m:
        try:
            v = float(Fraction(m.group(2)) / Fraction(m.group(3)))
        except (ZeroDivisionError, ValueError):
            return None
        return -v if m.group(1) else v
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)/(-?\d+(?:\.\d+)?)", s)
    if m:
        try:
            return float(Fraction(m.group(1)) / Fraction(m.group(2)))
        except (ZeroDivisionError, ValueError):
            return None
    try:
        return float(s)
    except ValueError:
        return None


def _split_top(s):
    """Split on commas that are not inside brackets/braces."""
    parts, depth, cur = [], 0, ""
    for ch in s:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return parts


def _scalar_equal(a, b):
    if a == b:
        return True
    fa, fb = to_number(a), to_number(b)
    if fa is not None and fb is not None:
        return abs(fa - fb) <= 1e-6 * max(1.0, abs(fb))
    return False


def equal(pred, gold):
    a, b = norm(pred), norm(gold)
    if not a:
        return False
    if _scalar_equal(a, b):
        return True
    # "x=5" against "5"
    if "=" not in b and re.match(r"^[a-zA-Z]\w*=", a):
        if _scalar_equal(a.split("=", 1)[1], b):
            return True
    # tuples / intervals: same brackets, elementwise
    if len(a) > 1 and len(b) > 1 and a[0] in "([" and b[0] in "([" and a[-1] in ")]" and b[-1] in ")]":
        if a[0] != b[0] or a[-1] != b[-1]:
            return False
        pa, pb = _split_top(a[1:-1]), _split_top(b[1:-1])
        return len(pa) == len(pb) and all(_scalar_equal(x, y) for x, y in zip(pa, pb))
    # bare lists "1,-2": compare as multisets
    pa, pb = _split_top(a), _split_top(b)
    if len(pb) > 1 and len(pa) == len(pb):
        rest = list(pa)
        for y in pb:
            hit = next((x for x in rest if _scalar_equal(x, y)), None)
            if hit is None:
                return False
            rest.remove(hit)
        return True
    return False


# --------------------------------------------------------------------------- suite

class MathSuite(Suite):
    name = "math"
    title = "Math"
    degen_check = True      # beta: real prompts, so a degenerate reply means something
    method = [
        'MATH-500 and AIME 2025 problems.',
        'Last \\boxed{} answer vs the reference.',
        'Degeneration detection (beta) on every reply.',
    ]
    blurb = "MATH-500 and AIME 2025 problems; the last \\boxed{} answer must match."
    source = "https://huggingface.co/datasets/HuggingFaceH4/MATH-500"
    default_n = 20
    est_prompt_tokens = 250
    est_output_tokens = 4000

    def load(self, rng, n, **params):
        n_aime = 0 if n < 4 else max(1, round(n / 4))
        n_math = n - n_aime
        tasks = []
        if n_math:
            rows = data.hf_all(*MATH500)
            for i in sorted(rng.sample(range(len(rows)), min(n_math, len(rows)))):
                r = rows[i]
                uid = r["unique_id"].replace("/", "-").removesuffix(".json")
                tasks.append(Task(
                    id="math500/" + uid, suite=self.name,
                    messages=[{"role": "user", "content": PROMPT.format(problem=r["problem"])}],
                    max_tokens=32768, expect={"answer": str(r["answer"])},
                    meta={"source": "MATH-500", "subject": r.get("subject"), "level": r.get("level")}))
        if n_aime:
            rows = data.hf_all(*AIME25)
            for i in sorted(rng.sample(range(len(rows)), min(n_aime, len(rows)))):
                r = rows[i]
                tasks.append(Task(
                    id="aime25/%s" % r["problem_idx"], suite=self.name,
                    messages=[{"role": "user", "content": PROMPT.format(problem=r["problem"])}],
                    max_tokens=32768, expect={"answer": str(r["answer"])},
                    meta={"source": "AIME 2025", "subject": ", ".join(r.get("problem_type") or [])}))
        return tasks

    def grade(self, task, reply):
        codes = check_output(reply)
        content = (reply.get("message") or {}).get("content") or ""
        gold = task.expect["answer"]
        ans = extract(content)
        if ans is None:
            codes.append("no_answer")
            detail = "no \\boxed{} answer in the reply"
        elif not equal(ans, gold):
            codes.append("wrong_answer")
            detail = "%s != %s" % (norm(ans), norm(gold))
        else:
            detail = ""
        codes = sorted(set(codes))
        return Verdict("fail" if codes else "pass", codes, answer=ans or "", expected=gold, detail=detail)
