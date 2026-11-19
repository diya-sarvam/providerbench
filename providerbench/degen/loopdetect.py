"""N-gram based degenerate-loop detector for LLM outputs (stdlib only).

Three independent failure detectors:
  * detect_loop       -- verbatim / near-verbatim repeated word n-grams.
  * detect_word_salad -- a sustained run of "content" tokens with essentially
                         no function words (the incoherent, script-mixed
                         collapse, which is a *different* failure mode than a
                         clean repetition loop).
  * detect_gibberish  -- symbol-soup / fragment collapse: sustained windows
                         dominated by pure-punctuation tokens, digit spam,
                         single-char fragments and U+FFFD replacement chars
                         (the "` # {{ if thus re :" mode, which
                         is FULL of function words so word-salad misses it, is
                         non-periodic so loop misses it, and stops on its own
                         so run-to-length misses it).
analyze_response() bundles all three so callers can tell the modes apart.
"""
import string
from typing import Optional


# Common English function words. A long stretch of text with none of these is a
# strong signal of incoherent "word salad" rather than real prose.
_FUNCTION_WORDS = frozenset("""
a an the this that these those my your his her its our their whose
i you he she it we they me him us them
is am are was were be been being do does did done have has had having
will would shall should can could may might must ought need
and or but nor so yet for because although though while whereas if unless until since
of to in on at by from with without into onto upon over under above below between among
as than then thus hence therefore however moreover instead about around through during before after
not no nor never none very just only also too more most less least much many few
what which who whom where when why how here there
""".split())


def _norm(tok: str) -> str:
    return tok.strip(string.punctuation + "“”‘’\"'`").lower()


import re as _re

# --------------------------------------------------------------------------- #
# Science-notation guards (added to stop false positives on math/chem/bio).    #
#                                                                              #
# GPQA / scientific reasoning is dense in LaTeX, equations, bare number runs,  #
# chemistry formulae and biological sequences (single-letter amino-acid codes  #
# "R N T F ...", DNA codons "GCT GGT ..."). The junk/salad heuristics were     #
# written for coding-agent output and treat all of that as "junk", firing on   #
# perfectly coherent science. These helpers let the detectors treat science    #
# notation as NEUTRAL (neither junk nor content) so only genuine degeneration  #
# -- symbol-soup, U+FFFD corruption, CJK injected mid-English -- is flagged.   #
# --------------------------------------------------------------------------- #
_CJK_RE = _re.compile(
    r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uff66-\uff9f]")
# pure numeric-ish token: digits joined by math punctuation (10, 3.3, 1,3, 10^-9,
# 1.4:301, -5, 6%, (2)).
_NUM_RE = _re.compile(r"^[(\[]?[+\-]?\d+([.,:;/^*_)\]\-]+\d+)*[%°)\]]?$")
# Mathematical operator / relation / grouping symbols. A whitespace token made
# up ENTIRELY of these is a lone maths operator ("/", "*", "(", ")", "≈", "^")
# written out in numerical work -- NEUTRAL, not junk. Deliberately EXCLUDES the
# genuine-degeneration soup characters (backtick, "#", "{", "}", '"') so those
# still count as junk.
_MATH_SYMS = frozenset(
    "+-*/=<>()[]^_.,:;|~%!?"
    "≈≤≥≠≡→←↔⇒⇔·×÷±∓√∛∫∮∑∏∂∇∞∝∈∉⊂⊃∪∩∀∃°′″…"
    "αβγδεζηθικλμνξοπρστυφχψωΓΔΘΛΞΠΣΦΨΩ")


def _is_math_token(tok: str) -> bool:
    """True for a token that is math / LaTeX / chemistry notation (not junk).

    Covers bare numbers, LaTeX ($C_3$, \\frac, D_{3h}), sub/superscript
    notation (C_3, 10^-9, x^2) and chemistry formulae (sp3, C(CH3)2, C8, 3a).
    Single-letter tokens (protein codes, math variables) are handled separately
    in _token_kind (they are neutralised, not counted as junk).
    """
    if not tok:
        return False
    if _NUM_RE.match(tok):
        return True
    if "$" in tok or "\\" in tok:
        return True
    if _re.search(r"[A-Za-z0-9][_^][\w{()+\-]", tok):
        return True
    # chemistry/formula: contains a digit and only formula-safe characters
    if any(c.isdigit() for c in tok) and \
            _re.fullmatch(r"[A-Za-z0-9()\[\]=+\-.,'/]+", tok):
        return True
    return False


def _token_kind(tok: str) -> str:
    """Classify a whitespace token as 'neutral' | 'junk' | 'content'.

    junk    = genuine degeneration signal: pure punctuation/symbol tokens
              ("#", "`", "{{", "--", ":", '"'), anything with a U+FFFD
              replacement char, or a mostly-symbol token with a stray letter.
    neutral = science notation that must NOT count as junk: structural markdown/
              code-listing tokens, math/LaTeX/chemistry, and single-character
              tokens (single-letter amino-acid codes, math variables, "a"/"I").
    content = an ordinary multi-character word (prose, identifiers, DNA codons).
    """
    if "\ufffd" in tok:
        return "junk"
    if _STRUCTURAL_RE.match(tok):
        return "neutral"
    if _is_math_token(tok):
        return "neutral"
    alpha = sum(1 for ch in tok if ch.isalpha())
    if alpha == 0:
        if all(ch in _MATH_SYMS for ch in tok):
            return "neutral"                # "/", "*", "(", ")", "≈", "^", "--"
        return "junk"                       # "#", "`", "{{", ":`", '"'
    if len(tok) == 1:
        return "neutral"                    # protein codes, math vars, "a"/"I"
    if alpha / len(tok) < 0.5 and len(tok) >= 3:
        return "junk"                       # ":-.--x", stray-letter symbol soup
    return "content"


def detect_word_salad(text: str, min_run: int = 120,
                      max_fn_ratio: float = 0.03):
    """Detect a sustained run of consecutive non-function-word tokens.

    Real prose sprinkles function words ("the", "to", "is", ...) every few
    tokens. A long consecutive run with none of them -- often mixing scripts or
    invented tokens -- is the incoherent "salad" collapse. We report the longest
    such run and flag salad when it reaches `min_run` words.

    Science-notation guard: function words AND neutral science tokens (numbers,
    LaTeX, chemistry, single-letter codes) BREAK the run, so a long formula /
    protein / codon stretch (which has no function words but is coherent) does
    not masquerade as salad. Genuine salad -- multi-char invented / script-mixed
    words with no maths between them -- still builds an unbroken run.

    Returns dict: salad(bool), run(words), start(word idx), phrase(sample),
    fn_ratio (function-word fraction over the flagged region).
    """
    words = text.split()
    n = len(words)
    if n < min_run:
        return {"salad": False, "run": 0, "start": 0, "phrase": "",
                "fn_ratio": 0.0}
    best_run = 0
    best_start = 0
    run = 0
    start = 0
    for i, w in enumerate(words):
        if _norm(w) in _FUNCTION_WORDS or _token_kind(w) == "neutral":
            run = 0
            start = i + 1
        else:
            if run == 0:
                start = i
            run += 1
            if run > best_run:
                best_run = run
                best_start = start
    if best_run < min_run:
        return {"salad": False, "run": best_run, "start": best_start,
                "phrase": "", "fn_ratio": 0.0}
    region = words[best_start:best_start + best_run]
    fn = sum(1 for w in region if _norm(w) in _FUNCTION_WORDS)
    fn_ratio = fn / max(1, len(region))
    phrase = " ".join(region[:40])[:300]
    return {"salad": fn_ratio <= max_fn_ratio, "run": best_run,
            "start": best_start, "phrase": phrase, "fn_ratio": round(fn_ratio, 4)}


# Structural tokens that are dense in legitimate coding-agent output and must
# not count toward junk: markdown table pipes/rules ("|", "|---|---|", ":-:"),
# horizontal rules ("---", "==="), and line-number prefixes from quoted code
# listings ("16:", "142|"). These are *excluded from the window entirely*
# (neither junk nor content) so a long table or code quote scores like the
# words inside it.
_STRUCTURAL_RE = _re.compile(r"^[|+:=\-]+$|^\d+[:|]$")


def _is_junk_token(tok: str) -> bool:
    """Back-compat shim: a token is junk iff _token_kind() calls it junk."""
    return _token_kind(tok) == "junk"


def detect_gibberish(text: str, window: int = 120, step: int = 30,
                     junk_ratio: float = 0.4, min_windows: int = 2):
    """Detect the symbol-soup / fragment gibberish collapse.

    Two independent signals, either of which flags gibberish:

    1. HARD-CORRUPTION triggers -- signatures legitimate science text never
       produces and which the junk-density window cannot see:
         * U+FFFD replacement chars (>=2): tokenizer/decoding corruption.
         * CJK injected into otherwise-Latin (English) reasoning: script
           injection (CJK is `.isalpha()` so it never raised the junk ratio).
       These are fingerprints of a quantization / decoding collapse.

    2. SYMBOL-SOUP density: slide a `window`-token window and measure the
       fraction of *junk* tokens (pure punctuation/backtick/brace/# soup and
       U+FFFD fragments -- see _token_kind). Science NOTATION (numbers, LaTeX,
       chemistry formulae, single-letter amino-acid / math tokens) is NEUTRAL
       and excluded from the window, so dense math / protein sequences / DNA
       codons no longer masquerade as junk. Real prose/code stays well under
       25% junk; the '` # {{ if thus re : - "' collapse runs 40-80%. Flag when
       >= `min_windows` windows cross `junk_ratio`.

    Returns dict: gibberish(bool), worst_ratio, bad_windows, total_windows,
    coverage, phrase, replacement_chars (U+FFFD count), cjk_chars, trigger
    ("replacement_char" | "cjk_injection" | "symbol_soup" | "").
    """
    fffd = text.count("\ufffd")
    cjk = len(_CJK_RE.findall(text))
    latin = sum(1 for ch in text if ("a" <= ch <= "z") or ("A" <= ch <= "Z"))
    out = {"gibberish": False, "worst_ratio": 0.0, "bad_windows": 0,
           "total_windows": 0, "coverage": 0.0, "phrase": "",
           "replacement_chars": fffd, "cjk_chars": cjk, "trigger": ""}

    # 1. Hard-corruption triggers (decisive on their own).
    if fffd >= 2 or (cjk >= 5 and latin > 3 * cjk):
        out["gibberish"] = True
        out["trigger"] = "replacement_char" if fffd >= 2 else "cjk_injection"
        idx = text.find("\ufffd")
        if idx < 0:
            m = _CJK_RE.search(text)
            idx = m.start() if m else 0
        out["phrase"] = text[max(0, idx - 60): idx + 60].replace("\n", " ")[:300]

    # 2. Symbol-soup junk density (science notation neutralised).
    words = [w for w in text.split() if _token_kind(w) != "neutral"]
    n = len(words)
    if n < window:
        return out
    junk = [_token_kind(w) == "junk" for w in words]
    pref = [0]
    for j in junk:
        pref.append(pref[-1] + (1 if j else 0))
    bad = 0
    total = 0
    worst = 0.0
    worst_start = 0
    for start in range(0, n - window + 1, step):
        total += 1
        ratio = (pref[start + window] - pref[start]) / window
        if ratio > worst:
            worst, worst_start = ratio, start
        if ratio >= junk_ratio:
            bad += 1
    out.update(worst_ratio=round(worst, 3), bad_windows=bad,
               total_windows=total,
               coverage=round(bad / max(1, total), 3))
    if bad >= min_windows:
        out["gibberish"] = True
        if not out["trigger"]:
            out["trigger"] = "symbol_soup"
        if not out["phrase"]:
            out["phrase"] = " ".join(words[worst_start:worst_start + 40])[:300]
    return out


def detect_loop(text: str, min_period: int = 1, max_period: int = 40):
    """Detect consecutive repeated word n-grams.

    Returns dict: looped(bool), period(words), repeats, span_words,
    phrase(sample), tail_ratio (fraction of text covered by the repeated region).
    A period-p n-gram repeated k times consecutively means words[i] == words[i-p]
    for a run of (k-1)*p positions.
    """
    words = text.split()
    n = len(words)
    best = None  # (span_words, period, repeats, start)
    if n < 20:
        return {"looped": False, "period": 0, "repeats": 0, "span_words": 0,
                "phrase": "", "tail_ratio": 0.0}
    for p in range(min_period, min(max_period, n // 2) + 1):
        run = 0
        for i in range(p, n):
            if words[i] == words[i - p]:
                run += 1
            else:
                run = 0
            repeats = run // p + 1
            # thresholds: short periods need many repeats, longer ones fewer
            min_rep = 30 if p <= 2 else (10 if p <= 8 else 6)
            span = run + p
            if repeats >= min_rep and span >= 60:
                if best is None or span > best[0]:
                    best = (span, p, repeats, i - run - p + 1)
    if best is None:
        return {"looped": False, "period": 0, "repeats": 0, "span_words": 0,
                "phrase": "", "tail_ratio": 0.0}
    span, p, repeats, start = best
    phrase = " ".join(words[start:start + p])[:300]
    end = start + span
    tail_ratio = span / max(1, n - start) if end >= n - 5 else span / n
    return {"looped": True, "period": p, "repeats": repeats, "span_words": span,
            "phrase": phrase, "tail_ratio": round(tail_ratio, 3)}


def analyze_response(content: Optional[str], reasoning: Optional[str]):
    out = {}
    c = detect_loop(content or "")
    r = detect_loop(reasoning or "")
    cs = detect_word_salad(content or "")
    rs = detect_word_salad(reasoning or "")
    cg = detect_gibberish(content or "")
    rg = detect_gibberish(reasoning or "")
    out["content_loop"] = c
    out["reasoning_loop"] = r
    out["content_salad"] = cs
    out["reasoning_salad"] = rs
    out["content_gibberish"] = cg
    out["reasoning_gibberish"] = rg
    out["looped"] = c["looped"] or r["looped"]
    out["loop_where"] = ("content" if c["looped"] else "") + \
                        ("+reasoning" if r["looped"] and c["looped"] else
                         "reasoning" if r["looped"] else "")
    out["salad"] = cs["salad"] or rs["salad"]
    out["salad_where"] = ("content" if cs["salad"] else "") + \
                         ("+reasoning" if rs["salad"] and cs["salad"] else
                          "reasoning" if rs["salad"] else "")
    out["gibberish"] = cg["gibberish"] or rg["gibberish"]
    out["gibberish_where"] = ("content" if cg["gibberish"] else "") + \
                             ("+reasoning" if rg["gibberish"] and cg["gibberish"]
                              else "reasoning" if rg["gibberish"] else "")
    out["degenerate"] = out["looped"] or out["salad"] or out["gibberish"]
    return out


if __name__ == "__main__":
    loop = ("Let me read the full split logic. " * 40 + "Then I will decide.")
    salad = ("summary follows " + " ".join(
        ["execution", "answer", "uploading", "workbook", "苦瓜", "config",
         "todone", "doneIRM", "meCode", "withonoc"] * 20))
    # the symbol-soup mode (function-word rich, non-periodic)
    gib = (' " about or 0 # ` if {{ - the use : ` # " thus re 0 -- ` if # '
           '" ` type} {type=" ` # : - " use # ` on 0 " if ` # {{ " thus ' * 12)
    clean = ("The prepare step already computes the per-client aggregates, "
             "so the campaign agents should reuse those columns instead of "
             "recomputing them from the raw events. The label windows still "
             "need to be derived per task because they depend on the target "
             "definition, and the temporal boundary must stay chronological "
             "so the heldout window is never touched during tuning. " +
             " ".join(f"Additional consideration number {i} covers a distinct "
                      f"aspect of the migration plan." for i in range(30)))
    code = ("def split_dataset(df, cfg):\n    validate_config(cfg)\n"
            "    strat = cfg['strategy']\n"
            "    rng = np.random.default_rng(cfg.get('seed', 0))\n"
            "    if strat == 'random':\n"
            "        mask = rng.random(len(df)) < cfg.get('test_size', 0.2)\n"
            "        return df[~mask], df[mask]\n"
            "    if strat == 'temporal':\n"
            "        col = cfg['temporal_column']\n"
            "        ordered = df.sort_values(col)\n"
            "        n_test = int(len(df) * cfg.get('test_size', 0.2))\n"
            "        return ordered.iloc[:-n_test], ordered.iloc[-n_test:]\n"
            "    raise ValueError(f'unknown strategy {strat!r}')\n"
            "# The temporal branch sorts by the configured column so the test\n"
            "# window is always the most recent slice of the data, which is\n"
            "# what the template promises when it documents temporal_column.\n"
            ) * 4
    print("loop     :", analyze_response(loop, "")["looped"])
    print("salad    :", analyze_response(salad, "")["salad"])
    print("gibberish:", detect_gibberish(gib))
    print("clean    :", analyze_response(clean, "")["degenerate"])
    print("code ok  :", analyze_response(code, "")["degenerate"])
