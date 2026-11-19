"""Degeneration detectors for model replies:

    loopdetect.py   repetition loops, word salad, symbol-soup gibberish
    detectors.py    adds the language-switch detector and runs everything over content and reasoning
    extra.py        cheap pre-filters: leaked special tokens, zlib low-entropy loops, budget exhausted
                    with no answer

A flag is a pointer for a human to read, not a verdict on the model: the result keeps which detector
fired in which channel ("loop:reasoning", "gibberish:content", ...).
"""
from .detectors import analyze, script_summary  # noqa: F401
