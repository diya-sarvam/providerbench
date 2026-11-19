# For coding agents

You are helping someone use or extend **providerbench**, a local tool that sends the same frozen requests
to two LLM inference providers and compares speed, cost, cache behaviour and failures. Read this file,
then the doc linked from each recipe.

## Ground rules

- **Never print, log, commit or paste an API key.** Keys go in `~/.providerbench/.env` via
  `./pb keys set <provider>` (the user types it), never inside the repository.
- **Real runs cost money.** Always `--preview` first, say the request count and the estimate, keep
  `--budget` small, and get the user's go-ahead before the real run. Use `--mock` to try things for free.
- Run `./pb doctor` before any commit; it must end with "nothing private in the repository".
- Python 3.11+ standard library only. Don't add dependencies.

## Setup

```bash
./pb setup                 # checks Python, creates ~/.providerbench, shows keys, fetches prices
./pb serve --mock          # optional: the UI with two fake providers
```

## Recipes

**Compare two providers** ([scenarios](docs/scenarios.md), [CLI](docs/cli.md))

1. Ask which providers and which model; check both serve it: `./pb providers` (and `./pb models`).
2. Pick the scenario that answers their question:
   - agent traffic: `agentic`
   - long reasoning at long context: `session`
   - their own logs: `custom` with `--data`
   - raw serving shape: `synthetic`
   - correctness: `tools,math,gpqa,robustness`
3. `./pb run A B --suites "<suite:params>" --preview`, then show the sample size and the estimate.
4. After the go-ahead: the same command with `--seed <seed from preview> --budget <usd> --json > run.json`.
5. Report from `targets[].summary`:
   - lead with cost per request (or per correct task) and median TTFT / tok/s;
   - compare cache on `cache_reuse_pct`;
   - list failure codes with counts;
   - say how many requests the sample had.

**Convert the user's logs** ([your data](docs/your-data.md))
Follow [docs/data-brief.md](docs/data-brief.md) exactly, write `traces.jsonl`, strip secrets and personal
data, then `./pb data check traces.jsonl` until it reports no errors.

**Add a provider** ([providers](docs/providers.md))
`./pb provider add <id> <base_url>` (the user saves the key first), review the `[models]` matches, then
`./pb provider check <id>` and `./pb prices refresh`. Only map model ids the provider actually lists.

**Add a model**
Add a block to `models.toml` (label, context, OpenRouter slug, aliases), then `./pb models` prints the
exact line to add to each provider file.

**Add a suite** ([architecture](docs/architecture.md#add-a-suite))
One module in `providerbench/suites/`, registered in `suites/__init__.py`, with offline grader tests.

## Checks before you finish

```bash
/path/to/python3.11+ -m unittest discover -s tests
./pb run mock-swift:glm-5.3-flash mock-steady:glm-5.3-flash --mock --suites tools=5,robustness=5
./pb doctor
```
