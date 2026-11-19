# CLI

Everything the UI does is also a command. `./pb <command> --help` shows every option.

## Start

| Command | |
|---|---|
| `./pb setup` | First run: checks Python, creates `~/.providerbench`, shows keys, fetches prices |
| `./pb serve [--port 8765] [--mock]` | The web UI. `--mock` adds two fake local providers |

## Run

```bash
./pb run <provider:model> <provider:model> --suites <suites> [options]
```

| Option | |
|---|---|
| `--suites` | `tools=30,math=20`, or with parameters: `"agentic:n=5,turns=3;synthetic:isl=2000,osl=256"` |
| `--data file.jsonl` | Your own sessions or prompts (the *Your data* scenario) |
| `--preview` | Print the sample and its seed; send nothing |
| `--seed N` | Replay the sample of that seed |
| `--replay <run id>` | Re-send exactly the tasks of a past run |
| `--budget USD` | Hard stop for the whole run (default 2) |
| `--concurrency N` | Requests in flight per provider (default 4) |
| `--thinking default\|off` | Ask providers to turn reasoning off (per-provider switch from its file) |
| `--temperature T` | Send a temperature (default: each provider's own default) |
| `--json` | Print the result as JSON |
| `--mock` | Use the fake local providers |

Suites: `agentic` (trace replay), `session` (long agent session), `custom` (your data), `synthetic`,
`tools`, `math`, `gpqa`, `robustness`. `./pb suites` lists each with its parameters.

```bash
./pb run fireworks:glm-5.3-flash deepinfra:glm-5.3-flash --suites "session:n=1,turns=3,loop=open" --budget 3
./pb run fireworks:glm-5.3-flash deepinfra:glm-5.3-flash --suites "synthetic:n=50,isl=8000,osl=512,cache_hit=0.6,turns=4"
./pb run fireworks:glm-5.3-flash deepinfra:glm-5.3-flash --data traces.jsonl --suites "custom:n=20"
```

## Results

| Command | |
|---|---|
| `./pb show <run id> [--json]` | Print a past run |
| `./pb export <run id> [--format zip\|md\|html] [--out FILE] [--limit N]` | Raw zip, Markdown, or HTML report |

## Providers, models, prices, keys

| Command | |
|---|---|
| `./pb providers` | Providers and whether a key is saved |
| `./pb provider add <id> <base_url>` | Probe an endpoint and write `providers/<id>.toml` |
| `./pb provider check <id>` | Re-probe a provider |
| `./pb models` | Each provider's `/models` vs `models.toml`: what to add |
| `./pb prices [refresh]` | Show or refresh prices |
| `./pb prices set <provider> <model> <in> <out> [--cached X] [--currency USD]` | Set a price by hand |
| `./pb keys [list \| set <provider> \| rm <provider>]` | Manage saved keys |

## Your data

| Command | |
|---|---|
| `./pb data check file.jsonl` | Validate a JSONL; errors with line numbers |
| `./pb data brief` | Print the conversion brief for your coding agent |

## Safety

| Command | |
|---|---|
| `./pb doctor [--install-hook]` | Check that nothing private is in the repo (and add a pre-commit hook) |
