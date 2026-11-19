"""Suite registry. Order is the order shown in the UI and CLI."""
from .agentic import AgenticSuite, CustomSuite
from .tools import ToolsSuite
from .math import MathSuite
from .gpqa import GPQASuite
from .robustness import RobustnessSuite
from .synthetic import SyntheticSuite

SUITES = {s.name: s for s in (AgenticSuite(), CustomSuite(), SyntheticSuite(), ToolsSuite(), MathSuite(), GPQASuite(), RobustnessSuite())}


def get(name):
    try:
        return SUITES[name]
    except KeyError:
        raise KeyError("unknown suite %r (have: %s)" % (name, ", ".join(SUITES))) from None


SCENARIOS = [
    {"id": "replay", "title": "Trace replay",
     "blurb": "Real agent sessions from SWE-smith, replayed step by step."},
    {"id": "custom", "title": "Your data",
     "blurb": "Your own sessions or prompts (JSONL), replayed against both providers."},
    {"id": "synthetic", "title": "Synthetic workload",
     "blurb": "Define the shape: input and output length, cache hit rate, single or multi-turn."},
    {"id": "quality", "title": "Quality benchmarks",
     "blurb": "Public benchmarks with known answers: tool calling, math, science, serving-bug probes."},
]

METHOD = [
    'Same requests to both providers, at the same time and concurrency.',
    'Fresh seeded sample, frozen with a hash. Same seed, same requests.',
    'A run tag opens every prompt, so no stale cache from earlier runs.',
    'TTFT: request sent to first token. Tok/s: after the first token.',
    "Tokens and cache hits are the provider's own usage numbers.",
    'Cache reuse = cached ÷ cacheable prefix.',
    "Cost uses each provider's published prices.",
    'Errors and retries are counted, never hidden.',
]
