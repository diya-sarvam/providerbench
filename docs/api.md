# Local HTTP API

`./pb serve` runs a small server on `127.0.0.1` (default port 8765) that serves the UI and this JSON API.
It refuses cross-origin POSTs. Money in the API is always USD (`*_usd`); prices are per 1M tokens in
the provider's own currency.

A **target** is `"<provider>:<model>"`: a canonical model from `models.toml` or the provider's raw id.

## State

`GET /api/state` → providers (with `key_saved`, never the key), models, suites (title, scenario, method,
`params` for the form, `graded`, `degen_check`), scenarios, the general method bullets, failure codes,
and exchange rates.

## Runs

**Run spec** (the body for estimate, preview and run):

```json
{"targets": ["fireworks:glm-5.3-flash", "deepinfra:glm-5.3-flash"],
 "suites": {"agentic": {"n": 5, "turns": 3}, "tools": 20},
 "seed": null, "concurrency": 4, "budget_usd": 2.0, "thinking": "default"}
```

`seed: null` draws a fresh sample; a number replays one. A suite value is a count or an object of its
parameters (see `params` in `/api/state`).

| Endpoint | |
|---|---|
| `POST /api/estimate` | Estimated cost per target |
| `POST /api/preview` | The exact sample (nothing sent): seed, task hash, groups with sizes and labels |
| `POST /api/runs` | Start a run → `{"id"}`. `{"replay": "<run id>"}` re-sends a past run's frozen tasks |
| `GET /api/runs` | Run list, newest first |
| `GET /api/runs/<id>` | Status, spec, seed, task hash, per-target progress (`state`, `inflight`, `eta_s`, `live`), price, `summary`, verdict |
| `POST /api/runs/<id>/control` | `{"target", "action": "pause" \| "resume" \| "stop"}` for one provider |
| `POST /api/runs/<id>/stop` | Stop the whole run |

**Summary** (per target): `n, passed, failed, errored, skipped, accuracy, by_suite, failures,
ttft_ms {p50, p90}, ttft_cold_ms, ttft_warm_ms, tps, e2e_ms, isl_mean, osl_mean, throughput_tps,
cache_pct, cache_possible_pct, cache_reuse_pct, cache_by_turn, tokens, cost_usd, cost_per_task_usd,
cost_per_correct_usd, retries, review`.

## Requests and traces

| Endpoint | |
|---|---|
| `GET /api/runs/<id>/results?target=&suite=&only=fail&q=&offset=&limit=` | Records for one target (`{total, records}` when paging) |
| `GET /api/runs/<id>/tasks` | Every task in order with each target's verdict |
| `GET /api/runs/<id>/compare?task=<task id>` | One request and every target's reply, with prev / next |
| `GET /api/runs/<id>/trace?target=&task=` | One request in full: what was sent and what came back (`&download=1` for a file) |
| `GET /api/runs/<id>/download` | Raw zip; `?target=` for one provider's `.jsonl` |
| `GET /api/runs/<id>/export?format=html\|md` | Readable report (`&view=1` to open instead of download) |

A **record** carries timings (`ttft_ms, e2e_ms, tps`), usage (`prompt_tokens, cached_tokens,
cache_possible_tokens, completion_tokens, reasoning_tokens`), `cost_usd`, `verdict`, `codes`, `detail`,
`content`, `reasoning`, `tool_calls`, `degen` (flags with evidence and character spans), and for trace
replay the recorded next step (`expected`, `match`) as reference.

## Degeneration review

| Endpoint | |
|---|---|
| `GET /api/flags?run=&status=pending\|degenerate\|false_positive\|missed` | Flagged replies with evidence, the prompt, and the label |
| `POST /api/runs/<id>/reviews` | `{"target", "task_id", "label": "degenerate" \| "false_positive" \| "missed" \| null, "note", "spans"}` |
| `GET /api/flags/export` | Labelled examples as JSONL |

## Data, keys, prices

| Endpoint | |
|---|---|
| `GET /api/datasets` · `POST /api/datasets {name, content}` · `POST /api/datasets/delete {id}` | Your uploads |
| `GET /api/datasets/brief` | The agent brief and the example file |
| `POST /api/keys {"<provider>": "<key>"}` · `POST /api/keys/delete {provider}` | Save or remove a key (never returned) |
| `GET /api/prices` · `POST /api/prices/refresh` · `POST /api/prices/manual` | Price rows, refresh, manual entry |
