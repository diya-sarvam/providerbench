"""Three cheap pre-filter checks that send cases to a human; they are not verdicts.

    leak       a raw special token such as <|im_end|> in content or reasoning
    entropy    400+ characters that compress more than 6x with zlib: a long low-entropy loop that the
               n-gram loop detector can miss
    exhausted  finish_reason == "length" with empty content: the whole budget went to reasoning
"""
import re
import zlib

SPECIAL = re.compile(r"<\|[a-z_]+\|>")
ENTROPY_MIN_CHARS = 400
ENTROPY_RATIO = 6.0


def loopiness(s):
    if len(s or "") < ENTROPY_MIN_CHARS:
        return 0.0
    b = s.encode()
    return len(b) / max(1, len(zlib.compress(b)))


def checks(content, reasoning, finish_reason, ratio=ENTROPY_RATIO, length_stop_expected=False):
    """-> list of {flag, detector, channel, info, phrase}. `ratio`: entropy threshold (a suite whose prompts
    use a small vocabulary sets it higher); `length_stop_expected`: skip "exhausted" where stopping at
    max_tokens is the point (synthetic workloads)."""
    out = []
    for channel, text in (("content", content or ""), ("reasoning", reasoning or "")):
        m = SPECIAL.search(text)
        if m:
            out.append({"flag": "leak:" + channel, "detector": "leak", "channel": channel,
                        "info": "raw special token %s in the %s" % (m.group(0), channel), "phrase": m.group(0)})
        r = loopiness(text)
        if r > ratio:
            tail = text[-200:]
            out.append({"flag": "entropy:" + channel, "detector": "entropy", "channel": channel,
                        "info": "%s compresses %.1fx (over %.0fx means long low-entropy repetition)" % (channel, r, ratio),
                        "phrase": tail})
    if finish_reason == "length" and not (content or "").strip() and not length_stop_expected:
        out.append({"flag": "exhausted:content", "detector": "exhausted", "channel": "content",
                    "info": "hit max_tokens with an empty answer: the whole budget went to reasoning", "phrase": ""})
    return out
