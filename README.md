# providerbench

**Same requests, two inference providers, one honest comparison.**

Pick a model that two providers both serve (say GLM-5.3 Flash on Fireworks and on DeepInfra). providerbench
sends both of them the *same frozen requests at the same time* and shows who is faster, cheaper, caches
better, and breaks less, with every request and reply kept so you can check for yourself.

It runs on your machine. Your API keys and results never leave it.

## Try it in a minute (no keys, no cost)

```bash
git clone https://github.com/aryan1165/providerbench && cd providerbench
./pb setup               # checks Python 3.11+, creates ~/.providerbench, shows what's next
./pb serve --mock        # opens http://127.0.0.1:8765 with two fake local providers
```

Pick **Mock Swift** vs **Mock Steady**, choose a scenario, press **Run**.

## Run it for real

```bash
./pb keys set fireworks         # paste your key (stored in ~/.providerbench/.env, never in the repo)
./pb keys set deepinfra
./pb serve                      # the web UI
```

or straight from the terminal:

```bash
./pb run fireworks:glm-5.3-flash deepinfra:glm-5.3-flash --suites "agentic:n=10,turns=3" --budget 2
```

Python 3.11+, standard library only. Nothing to install.

## What you can test

| Scenario | What it sends | What it tells you |
|---|---|---|
| **Trace replay** | Real SWE-smith agent sessions, replayed step by step | Speed, cost and cache reuse on realistic agent traffic |
| **Long agent session** | One long coding-agent session (64k+), then heavy requests (fix, tests, PR text) | Decode speed, thinking and cost for long outputs at long context |
| **Your data** | Your own sessions or prompts (JSONL) | The same, on your real traffic |
| **Synthetic workload** | Requests shaped by you: input/output length, cache-hit target, turns | Pure serving numbers for a request shape |
| **Quality benchmarks** | BFCL tool calling, MATH-500 + AIME, GPQA-Diamond, serving-bug probes | Known-answer accuracy: catches quantization and template problems |

Every run gives you a scorecard (cost, time to first token, tok/s, cache reuse, errors), charts, a
table of every request, side-by-side replies, and exports (HTML report, Markdown, raw JSONL).

## Docs

| | |
|---|---|
| [Getting started](docs/getting-started.md) | Install, keys, first runs, where your data lives |
| [Scenarios](docs/scenarios.md) | What each test sends and how it is checked |
| [Metrics and method](docs/metrics.md) | How every number is taken; failure codes |
| [Your data](docs/your-data.md) | The JSONL format, and a brief to hand your agent |
| [Providers, models, prices](docs/providers.md) | Add a provider in one command; how prices are found |
| [Traces and exports](docs/traces.md) | Watching a run, pausing a provider, comparing replies, reports |
| [Degeneration review (beta)](docs/degeneration.md) | Loop / gibberish detection and human review |
| [Privacy](docs/privacy.md) | Keys, local data, `pb doctor` |
| [CLI](docs/cli.md) | Every command |
| [Architecture](docs/architecture.md) | How the code fits together |
| [Local API](docs/api.md) | The HTTP API behind the UI |

**Using a coding agent?** Point it at [AGENTS.md](AGENTS.md).
**Contributing?** See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Apache-2.0, see [LICENSE](LICENSE). Third-party notices: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
