# Contributing

Thanks for helping. A few things keep this project easy to trust and easy to run.

## Principles

- **Same requests, honest numbers.** Anything that changes what is sent or how it is measured needs a
  line in [docs/metrics.md](docs/metrics.md) or [docs/scenarios.md](docs/scenarios.md).
- **Local and private.** Keys and run data stay in `~/.providerbench`. Never add telemetry or uploads.
- **No dependencies.** Python 3.11+ standard library; the UI is plain HTML, CSS and JS with no build step.
- **Deterministic grading.** No LLM judges.

## Setup

```bash
./pb setup
./pb doctor --install-hook     # blocks commits that would publish keys or run data
```

## Tests

```bash
python3.11 -m unittest discover -s tests          # offline, a few seconds
PB_NET=1 python3.11 -m unittest discover -s tests # also downloads and samples every dataset
./pb serve --mock                                 # click through the UI with fake providers
```

## Common changes

| Change | Where | Docs |
|---|---|---|
| New provider | `providers/<id>.toml` (`./pb provider add`) | [providers](docs/providers.md) |
| New model | `models.toml` + provider `[models]` (`./pb models`) | [providers](docs/providers.md#models) |
| New suite | `providerbench/suites/<name>.py` + registry + tests | [architecture](docs/architecture.md#add-a-suite) |
| New metric | `report.py` (+ UI) | [metrics](docs/metrics.md) |
| UI | `providerbench/ui/` | [api](docs/api.md) |

For a provider PR: one provider per file, no keys, `base_url` on the provider's own domain, model ids
that point at the same weights as the canonical model, and the output of `./pb provider check <id>`.

## Before a PR

- Tests pass and `./pb doctor` is clean.
- Docs updated for anything a user would notice.
