# Getting started

## 1. Install

```bash
git clone https://github.com/aryan1165/providerbench && cd providerbench
./pb setup
```

`./pb` is a small launcher that finds Python 3.11 or newer on your machine (it needs `tomllib`, which is
in the standard library from 3.11). There are no packages to install. `pb setup` checks the Python
version, creates your private data folder (`~/.providerbench`), shows which provider keys are saved,
and fetches current prices.

No Python 3.11+? `brew install python` (macOS) or `uv python install 3.12` both work.

## 2. Try it with no keys

```bash
./pb serve --mock
```

This opens the web UI at http://127.0.0.1:8765 with two fake local providers, **Mock Swift** (fast, reports
cache) and **Mock Steady** (slower, some rate limits and broken tool calls). Everything works end to end,
nothing is billed. Mock runs only show in History while `--mock` is on.

## 3. Add your keys

```bash
./pb keys set fireworks        # prompts for the key, input hidden
./pb keys list
./pb keys rm fireworks
```

Or paste keys on the **Keys** page. Keys are stored in `~/.providerbench/.env` (owner-only) and are only
ever sent to that provider's own host. See [privacy](privacy.md).

## 4. Your first real run

```bash
./pb serve
```

1. **Run** page: pick provider A and B and the model (both must serve it).
2. Pick a scenario. Start small: Trace replay with 3 sessions, or Quality benchmarks with ~10 tasks each.
3. **Preview sample** shows exactly what will be sent and an estimated cost. Set a **Budget**: the run
   stops when it is reached.
4. **Run**. While it runs, each provider can be paused or stopped on its own.
5. The result page has the scorecard, charts, every request, and exports.

From the terminal instead:

```bash
./pb run fireworks:glm-5.3-flash deepinfra:glm-5.3-flash --suites "tools=20,math=10" --budget 1 --preview
./pb run fireworks:glm-5.3-flash deepinfra:glm-5.3-flash --suites "tools=20,math=10" --budget 1 --seed <seed from preview>
./pb show <run-id>
```

## Where things live

| | |
|---|---|
| `providers/*.toml` | one file per provider (in the repo) |
| `models.toml` | canonical model names and the spellings providers use (in the repo) |
| `~/.providerbench/.env` | your keys |
| `~/.providerbench/runs/` | every run: spec, frozen sample, every request and reply |
| `~/.providerbench/uploads/` | your own datasets |
| `~/.providerbench/prices.json` | price snapshots |

Set `PB_HOME` to keep this somewhere else.

## Next

- [Scenarios](scenarios.md): which test answers which question
- [Your data](your-data.md): run your own traffic
- [Providers](providers.md): add one that isn't listed
