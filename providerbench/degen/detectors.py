"""Degeneration detectors for long outputs:

    loop              verbatim / near-verbatim repeated word n-grams
    word salad        a sustained run of content tokens with no function words
    gibberish         symbol soup, digit spam, fragments, U+FFFD
    foreign language  a sustained run of non-Latin script in a reply asked for in English
                      (Greek is excluded: it is ordinary scientific notation)

A flag is a pointer for a human to read, not a verdict.
"""
import re
import unicodedata

from . import loopdetect

# --------------------------------------------------------------------------- #
# Script table.
#
# Greek (U+0370-U+03FF) is deliberately ABSENT -- see the module docstring.
# So are the Latin supplements: accented Latin is still Latin.
# --------------------------------------------------------------------------- #
SCRIPTS = {
    "han":        r"㐀-䶿一-鿿豈-﫿",
    "kana":       r"぀-ヿｦ-ﾟ",
    "hangul":     r"ᄀ-ᇿ㄰-㆏가-힯",
    "cyrillic":   r"Ѐ-ӿԀ-ԯ",
    "arabic":     r"؀-ۿݐ-ݿ",
    "hebrew":     r"֐-׿",
    "devanagari": r"ऀ-ॿ",
    "bengali":    r"ঀ-৿",
    "gurmukhi":   r"਀-੿",
    "gujarati":   r"઀-૿",
    "odia":       r"଀-୿",
    "tamil":      r"஀-௿",
    "telugu":     r"ఀ-౿",
    "kannada":    r"ಀ-೿",
    "malayalam":  r"ഀ-ൿ",
    "thai":       r"฀-๿",
}
_SCRIPT_RE = {name: re.compile("[" + rng + "]") for name, rng in SCRIPTS.items()}
_ANY_FOREIGN = re.compile("[" + "".join(SCRIPTS.values()) + "]")


def detect_foreign_language(text, min_chars=12, min_ratio=0.005, min_run=3):
    """A sustained run of non-Latin script in a reply asked for in English.

    Two independent triggers, because the failure has two shapes:

    ratio  -- `min_chars` or more characters of one script AND at least
              `min_ratio` of the text. The floor stops a single stray glyph
              (which is jitter, and which `loopdetect`'s gibberish detector
              already counts) from being called a language switch; the ratio
              stops a long, otherwise-English answer from being flagged for one
              quoted foreign term.
    run    -- `min_run` or more CONSECUTIVE whitespace tokens in one script.
              Catches a short but unambiguous switch mid-sentence that the ratio
              rule would dilute away in a long answer.

    Returns a dict shaped like loopdetect's detectors so `analyze()` can treat
    all four the same: foreign(bool), script, chars, ratio, run, sample.
    """
    t = text or ""
    empty = {"foreign": False, "script": "", "chars": 0, "ratio": 0.0,
             "run": 0, "sample": "", "trigger": ""}
    if not t or not _ANY_FOREIGN.search(t):
        return empty

    counts = {name: len(rx.findall(t)) for name, rx in _SCRIPT_RE.items()}
    script = max(counts, key=lambda k: counts[k])
    chars = counts[script]
    ratio = chars / max(len(t), 1)

    # Longest consecutive run of tokens in the dominant script.
    rx = _SCRIPT_RE[script]
    run = best_run = 0
    run_at = best_at = 0
    tokens = t.split()
    for i, tok in enumerate(tokens):
        if rx.search(tok):
            if run == 0:
                run_at = i
            run += 1
            if run > best_run:
                best_run, best_at = run, run_at
        else:
            run = 0

    by_ratio = chars >= min_chars and ratio >= min_ratio
    by_run = best_run >= min_run
    if not (by_ratio or by_run):
        return empty

    sample = " ".join(tokens[best_at:best_at + max(best_run, 6)])[:200]
    return {
        "foreign": True,
        "script": script,
        "chars": chars,
        "ratio": round(ratio, 4),
        "run": best_run,
        "sample": sample,
        "trigger": ",".join(x for x in (("ratio" if by_ratio else ""),
                                        ("run" if by_run else "")) if x),
    }


def script_summary(text):
    """Which scripts appear at all, for the review file's header line."""
    t = text or ""
    present = sorted(name for name, rx in _SCRIPT_RE.items() if rx.search(t))
    if _ANY_FOREIGN.search(t):
        return present or ["unknown"]
    return []


# --------------------------------------------------------------------------- #
# The pipeline
# --------------------------------------------------------------------------- #

def analyze(content, reasoning):
    """Run all four detectors over both channels.

    Both channels, always: on a reasoning model the answer often lives in
    `reasoning_content`, and a collapse there is a real defect even when the
    final `content` came out clean. The flag records which detector fired in
    which field -- `loop:reasoning`, `foreign:content`.
    """
    res = loopdetect.analyze_response(content or "", reasoning or "")

    cf = detect_foreign_language(content or "")
    rf = detect_foreign_language(reasoning or "")
    res["content_foreign"] = cf
    res["reasoning_foreign"] = rf
    res["foreign"] = cf["foreign"] or rf["foreign"]
    res["foreign_where"] = "+".join(
        x for x in (("content" if cf["foreign"] else ""),
                    ("reasoning" if rf["foreign"] else "")) if x)

    # loopdetect's own `degenerate` predates this detector, so widen it here
    # rather than editing the vendored file.
    res["degenerate"] = bool(res["looped"] or res["salad"]
                             or res["gibberish"] or res["foreign"])

    flags = []
    for name, where_key in (("loop", "loop_where"), ("salad", "salad_where"),
                            ("gibberish", "gibberish_where"),
                            ("foreign", "foreign_where")):
        fired = res["looped"] if name == "loop" else res[name]
        if fired:
            for field in (res[where_key] or "?").split("+"):
                if field:
                    flags.append("%s:%s" % (name, field))
    res["flags"] = flags
    res["scripts"] = sorted(set(script_summary(content) + script_summary(reasoning)))
    return res


if __name__ == "__main__":
    cases = {
        "clean english": ("The upper limit is 85.865 mm. ANSWER: 85.865", ""),
        "greek science": ("The angle alpha is 30 deg, so α = π/6 and "
                          "Ω = 2πf. ANSWER: 0.524", ""),
        "one quoted term": ("The supplier is based in Tokyo (東京) and "
                            "ships weekly, which is well inside the window we "
                            "were asked about, so the lead time stands as "
                            "recorded in the register. ANSWER: 21", ""),
        "script switch": ("The tolerance band is "
                          "यह संख्या "
                          "रजिस्टर "
                          "में दर्ज "
                          "है ANSWER: 0.02", ""),
        "cjk collapse": ("summary " + "苦瓜 工作 完成 " * 8, ""),
        "loop": ("Let me read the full split logic. " * 40, ""),
    }
    for name, (c, r) in cases.items():
        a = analyze(c, r)
        print("%-18s degenerate=%-5s flags=%-28s scripts=%s"
              % (name, a["degenerate"], ",".join(a["flags"]) or "-",
                 a["scripts"] or "-"))
