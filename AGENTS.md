# For coding agents

This file is for an agent (Claude Code, Codex, Cursor, ...) working in this repo for a user.

## Run a comparison for someone

1. Ask which two providers and which model. Keys go in `~/.providerbench/.env` as
   `<KEY_ENV>=<value>` (the provider file names `key_env`), or the user runs `./pb keys set <provider>`.
   Never print, log or commit a key, and never write one inside the repository.
2. Pick the scenario that answers their question (README "Three ways to test"), then preview:
   `./pb run <a> <b> --suites "agentic:n=10,turns=3" --preview` (or `synthetic:isl=..,osl=..,cache_hit=..,turns=..`,
   or `tools=30,math=20`; their own traces: `--data file.jsonl`). Then run the same command with
   `--seed <seed from the preview> --budget 2 --json > run.json`.
   Targets are `provider:model`, model from `models.toml` (or the provider's raw id).
3. Read `verdict`, then `targets[].summary`. Lead with accuracy and cost per correct task; quote
   TTFT/tok/s as medians; list failure codes with counts. Say it was one sample of N tasks.
4. Spend: `--budget` is a hard stop in USD. Run `./pb run ... ` with small counts first if unsure;
   the CLI prints an estimate before it starts.

## Add a provider

Preferred: `./pb provider add <id> <base_url>` (needs the key: `./pb keys set <id>` first), then fix anything it
could not decide, then `./pb provider check <id>`, then `./pb prices refresh`.

By hand, `providers/<id>.toml`:

```toml
name = "Acme"                              # display name
base_url = "https://api.acme.ai/v1"        # OpenAI-compatible root; /chat/completions is appended
key_env = "ACME_API_KEY"                   # env var holding the user's key ("" = no key)
docs = "https://acme.ai/pricing"

[headers]                                  # optional; {session} = one id per agentic session
x-session-affinity = "{session}"

[models]                                   # canonical name (models.toml) -> this provider's id
"glm-5.3-flash" = "acme/glm-5.3-flash"

[thinking_off]                             # body merged when a run asks for thinking off; "*" = all models
"glm-5.3-flash" = { reasoning_effort = "none" }

[extra_body]                               # body merged on every request for that model
"gemma-4-31b" = { chat_template_kwargs = { enable_thinking = true } }

[quirks]                                   # informational; stream_usage = false stops sending stream_options
stream_usage = true

[[pricing]]                                # tried in order; first source with a price wins
source = "models_endpoint"                 # price in GET /models (add public = true if no key needed)

[[pricing]]
source = "openrouter"                      # the provider's listing on OpenRouter
provider = "Acme"                          # provider_name as OpenRouter shows it
tag = "acme"                               # optional: pick one endpoint variant

[[pricing]]
source = "scrape"                          # regex over a public page; per 1M tokens
url = "https://acme.ai/pricing"
currency = "USD"
pattern = '''(?m)^\| (?P<model>[\w./-]+) \| \$(?P<input>[\d.]+) \| \$(?P<output>[\d.]+) \|'''

[[pricing]]
source = "manual"
```

Rules for a provider PR:
- One provider per file. No keys, ever. Keep `base_url` on the provider's own domain.
- Every `[models]` entry must point at the same weights as the canonical model (same checkpoint;
  note the quantization in a comment if the provider documents it).
- Run `./pb provider check <id>` and paste its output in the PR.

## Add a model

Add a block to `models.toml` with `label`, `context`, `openrouter` (slug, if listed) and the
`aliases` providers use; then run `./pb models`: for every provider whose `/models` it can read, it
prints the exact `"<canonical>" = "<provider id>"` lines to add to that provider's `[models]`, ids
that are mapped but no longer listed, and ids that match nothing yet. Only map ids a provider
actually lists; for providers you have no key for, say the mapping is unverified.

## Add a suite

`providerbench/suites/<name>.py`: subclass `Suite` (`suites/base.py`), set `scenario`, `method` and
`params()`, implement `load(rng, n, **params)` (sample
only with `rng`, fetch data with `providerbench/data.py`) and `grade(task, reply)` (deterministic
code; start from `failures.check_output(reply)`; return a `Verdict`). Register it in
`suites/__init__.py`. Add offline grader tests in `tests/`.

## Checks before you finish

Run `./pb doctor` before any commit or deploy: it must say "nothing private in the repository".


```bash
python3 -m unittest discover -s tests          # with Python 3.11+
./pb run mock-swift:glm-5.3-flash mock-steady:glm-5.3-flash --mock --suites tools=5,robustness=5
```
