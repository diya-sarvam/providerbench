# Local HTTP API (server.py, 127.0.0.1 only)

The UI is `providerbench/ui/index.html`, served at `/`. All bodies are JSON. Money is always in USD in the
API (`*_usd`); the UI converts with `fx` for display.

A *target* is `"<provider>:<model>"`, where model is a canonical name from `models.toml`
(e.g. `glm-5.3-flash`) or, if not canonical, the provider's raw model id.

## GET /api/state
```json
{
  "providers": [{"id": "fireworks", "name": "Fireworks", "base_url": "...", "key_env": "FIREWORKS_API_KEY",
                 "key_saved": true, "models": {"glm-5.3-flash": "accounts/fireworks/models/glm-5p3-flash"},
                 "docs": "https://..."}],
  "models": [{"id": "glm-5.3-flash", "label": "GLM-5.3 Flash", "context": 1048576}],
  "suites": [{"name": "math", "title": "Math", "blurb": "...", "source": "...", "default_n": 20}],
  "currency": "USD",
  "fx": {"USD": 1, "INR": 88.4}
}
```

## POST /api/keys
Body `{"fireworks": "fw-..."}`. Saves to `~/.providerbench/.env` (0600). Keys are never returned by any endpoint.
Response `{"saved": ["fireworks"]}`.

## POST /api/estimate
Body: a run spec (below). Response:
```json
{"targets": [{"target": "fireworks:glm-5.3-flash", "tasks": 40, "usd": 0.12, "priced": true}],
 "total_usd": 0.25}
```

## Run spec
```json
{"targets": ["deepinfra:glm-5.3-flash", "fireworks:glm-5.3-flash"],
 "suites": {"agentic": 5, "tools": 20, "math": 10},
 "seed": null,            // null = fresh sample this run; a number replays that sample
 "concurrency": 4,        // in-flight requests per target
 "budget_usd": 2.0,       // hard stop for the whole run
 "thinking": "default"}   // "default" | "off"
```

## POST /api/runs  → `{"id": "20261119-142233-ab12"}`
## POST /api/runs/<id>/stop → `{"ok": true}`

## GET /api/runs
List, newest first: `[{"id", "created", "status", "targets": [..], "suites": {..}, "headline": "..."}]`

## GET /api/runs/<id>
```json
{
  "id": "...", "created": 1763540000.0, "status": "running|done|stopped|error", "error": null,
  "spec": {...}, "seed": 12345, "task_hash": "sha256:...", "tasks_total": 40,
  "spend_usd": 0.08,
  "targets": [{
     "target": "deepinfra:glm-5.3-flash", "provider": "deepinfra", "provider_name": "DeepInfra",
     "model": "glm-5.3-flash", "model_id": "glm5.3-flash",
     "done": 12, "total": 40, "spend_usd": 0.04,
     "live": {"task_id": "math500/test-12", "suite": "math", "thinking": "last ~600 chars", "answer": "last ~600 chars"},
     "price": {"input": 0.15, "cached_input": 0.03, "output": 0.5, "currency": "USD", "source": "openrouter", "quant": "fp8"},
     "summary": SUMMARY
  }],
  "verdict": ["fireworks is 1.8x faster to first token", "deepinfra costs 23% less per correct task", "..."]
}
```

SUMMARY (null until the target has at least one result):
```json
{
  "n": 40, "passed": 31, "failed": 7, "errored": 2,
  "accuracy": 0.816,                       // passed / (passed + failed); transport errors excluded
  "by_suite": {"math": {"n": 10, "passed": 8, "failed": 2, "errored": 0, "accuracy": 0.8}},
  "failures": {"wrong_answer": 5, "tool_args_schema": 2, "http_429": 2},
  "ttft_ms": {"p50": 820, "p90": 1900}, "ttft_cold_ms": {"p50": 1400}, "ttft_warm_ms": {"p50": 500},
  "tps": {"p50": 182.0}, "e2e_ms": {"p50": 9200},
  "cache_pct": 0.91, "cache_reported": true,          // cached/prompt over warm (turn>=1) requests
  "tokens": {"prompt": 120000, "cached": 80000, "completion": 54000, "reasoning": 30000},
  "cost_usd": 0.041, "cost_per_task_usd": 0.001, "cost_per_correct_usd": 0.0013,
  "retries": 3
}
```

## GET /api/runs/<id>/results?target=<target>&only=fail
Per-request records, newest last:
```json
[{"task_id": "...", "suite": "tools", "turn": 0, "verdict": "fail", "codes": ["wrong_args"],
  "answer": "...", "expected": "...", "detail": "...", "meta": {...},
  "ttft_ms": 800, "e2e_ms": 3000, "tps": 150, "prompt_tokens": 900, "cached_tokens": 0,
  "completion_tokens": 300, "reasoning_tokens": 120, "cost_usd": 0.0002, "error": null,
  "content": "first 4000 chars", "reasoning": "first 4000 chars", "tool_calls": [...]}]
```

## GET /api/prices
`[{"provider": "fireworks", "model": "glm-5.3-flash", "input": 0.15, "cached_input": 0.03, "output": 0.5,
   "currency": "USD", "source": "openrouter|models_endpoint|scrape|manual", "url": "...", "fetched_at": 1763540000, "quant": "fp8"}]`
Prices are per 1M tokens in the provider's own currency.

## POST /api/prices/refresh → `{"ok": true, "log": ["..."]}`
## POST /api/prices/manual
Body `{"provider", "model", "input", "cached_input", "output", "currency"}` → `{"ok": true}`

## Failure codes
`GET /api/state` also carries `"failure_codes": {"wrong_answer": {"kind": "correctness", "label": "Wrong answer"}, ...}`
(kinds: transport, output, correctness).

---

# v2: scenarios, parameters, preview, your own data

The Run page offers three **scenarios**. A scenario is a set of suites; each suite now declares its
parameters and a short method so the UI can render the form and the explanation without hard-coding.

## GET /api/state — new fields on each suite
```json
{"name": "agentic", "title": "Trace replay", "scenario": "replay",
 "blurb": "one line", "graded": true,
 "method": ["short bullet: what is sent", "short bullet: how it is checked", "..."],
 "params": [
   {"key": "n", "label": "Sessions", "type": "int", "default": 5, "min": 1, "max": 200, "help": "..."},
   {"key": "source", "label": "Traces", "type": "source", "default": "swe-smith", "help": "..."},
   {"key": "turns", "label": "Turns per session", "type": "int", "default": 3, "min": 1, "max": 20},
   {"key": "cache_hit", "label": "Cache hit target", "type": "float", "default": 0.5, "min": 0, "max": 0.95, "step": 0.05},
   {"key": "x", "label": "...", "type": "choice", "choices": [["value", "Label"]], "default": "value"}
 ]}
```
`scenario` is one of `replay`, `synthetic`, `quality`. `type: "source"` is a dataset picker: the
value is `"swe-smith"` (built in) or `"upload:<id>"` (from GET /api/datasets). Scenario titles and
one-line descriptions are in `state.scenarios`:
```json
"scenarios": [{"id": "replay", "title": "Trace replay", "blurb": "..."},
              {"id": "synthetic", "title": "Synthetic workload", "blurb": "..."},
              {"id": "quality", "title": "Quality benchmarks", "blurb": "..."}],
"method": ["general bullets that apply to every run: same bytes, same time, nonce, ..."]
```

## Run spec — suites carry parameters
```json
{"targets": [...], "scenario": "synthetic",
 "suites": {"synthetic": {"n": 20, "isl": 2000, "osl": 256, "cache_hit": 0.5, "turns": 1, "turn_isl": 200}},
 "seed": 123, "concurrency": 4, "budget_usd": 2, "thinking": "default"}
```
A plain integer is still accepted (`"tools": 30` = `{"n": 30}`). One scenario per run.

## POST /api/preview
Body: a run spec. Builds the exact sample (no requests are sent). If `seed` is null a seed is drawn
and returned; send that seed with the run to get exactly the previewed tasks.
```json
{"seed": 123, "task_hash": "sha256:...", "requests": 15,
 "suites": {"agentic": {"total_groups": 5, "groups": [
    {"id": "swe/<traj>@41", "label": "pandas-dev__pandas.95280573.pr_48890", "turns": 3,
     "prompt_tokens": [9800, 10400, 11100],
     "expected": ["str_replace_editor(view /testbed/pandas/core/frame.py)", "bash(python reproduce.py)", "..."],
     "tools": ["bash", "str_replace_editor", "submit"], "url": "https://..."}]}}}
```
`prompt_tokens` are estimates (chars / 3.6); measured values appear in results.

## POST /api/datasets
Body `{"name": "my-traces.jsonl", "content": "<jsonl text>"}`. One JSON object per line:
```json
{"id": "optional", "messages": [ ...OpenAI chat messages, may include assistant tool_calls and tool results... ],
 "tools": [ ...optional OpenAI tool definitions... ]}
```
A line whose messages contain assistant turns is a **session**: each assistant turn can be replayed
and the recorded turn is what the reply is compared with. A line with no assistant turn is a single
**request**, sent as is. Response:
```json
{"id": "a1b2c3d4", "name": "my-traces.jsonl", "sessions": 40, "requests": 0, "steps": 812,
 "tools": ["bash", "read_file"], "tokens_mean": 14200, "errors": ["line 7: not JSON"]}
```
## GET /api/datasets → `[{same fields, "created": 1763540000}]`

## Summary — new fields
Top level: `"isl_mean"`, `"osl_mean"` (measured prompt / completion tokens per request),
`"throughput_tps"` (completion tokens / wall time across all requests of the target),
`"accuracy"` is null when no graded suite ran.
Per suite (`by_suite[name]`): `"graded"`, `"isl_mean"`, `"osl_mean"`, `"cache_pct"`, `"ttft_p50"`,
`"ttft_p90"`, `"tps_p50"`, and for replay
`"match": {"exact": 3, "same_action": 4, "same_tool": 2, "other_tool": 1, "no_call": 0, "text": 0}`.

## Record — new field
`"match"`: `exact | same_action | same_tool | other_tool | no_call | text | null` (replay only),
and `"expected"` for replay is the recorded call rendered as `name(args)`.

---

# v3: per-provider control, progress, traces, cache baseline

## GET /api/runs/<id> — new per-target fields
```json
{"state": "waiting|running|pausing|paused|stopping|stopped|done",
 "inflight": [{"task_id": "syn/15/t2", "elapsed_s": 31.4}],
 "elapsed_s": 912.3, "eta_s": 640}
```
`eta_s` is null when unknown or not running. `live` is the stream of the most recently started
in-flight request. A run whose targets are all `done`/`stopped` is `done`/`stopped`.

## POST /api/runs/<id>/control
Body `{"target": "jarvislabs:gemma-4-31b", "action": "pause|resume|stop"}`. Pause: requests in
flight finish, no new ones start. Stop: remaining requests of that target are recorded as skipped.
Only for runs started by this server process (404-style error otherwise).

## Traces
- `GET /api/runs/<id>/results?target=..&suite=..&only=fail&q=..&offset=0&limit=50` → when any of
  suite/q/offset/limit is given: `{"total": 80, "records": [...]}` (content trimmed to 600 chars,
  reasoning to 300). Without them: the old list (fail-only use).
- `GET /api/runs/<id>/trace?target=..&task=<task_id>` → one request in full:
  `{"run", "target", "url", "request": {model, messages (with the run tag), tools, max_tokens, ...},
    "expect": {...grading data...}, "record": {...full record incl. full content, reasoning, tool_calls...}}`.
  Add `&download=1` to get it as a .json file.
- `GET /api/runs/<id>/download` → zip of run.json, tasks.json and every target's .jsonl.
- `GET /api/runs/<id>/download?target=..` → that target's records as .jsonl.

## Summary — cache fields (replace the old warm-only cache_pct)
- `cache_pct`: measured, cached ÷ prompt tokens over all requests that report caching.
- `cache_possible_pct`: the most that could have been cached: per request, the share of the prompt
  already prefilled earlier in this run on that provider (prefix match, prefill finished before the
  request was sent) ÷ prompt tokens.
- `cache_reuse_pct`: cached ÷ cacheable, capped at 100%: how much of the reusable prefix the
  provider actually reused. This is the number to compare providers on.
- `cache_by_turn`: `{"0": {"n": 20, "hit": 0.806, "possible": 0.903}, "1": {...}}`.
- Per suite: `cache_pct`, `cache_possible_pct`, `cache_reuse_pct`.
- Record: `cache_possible_tokens`.

## Fonts
`GET /fonts/<file>.woff2` serves optional local fonts from `~/.providerbench/fonts/` when present (local
only, never in the repo; licensed). The UI must fall back to system fonts when they are missing.

## POST /api/keys/delete
Body `{"provider": "fireworks"}` → `{"removed": true}`. Removes the key from `~/.providerbench/.env`
and from the running server.

---

# v4: degeneration flags and human review

Every reply's content and reasoning go through the vendored detectors (`providerbench/degen/`): repetition
loop, word salad, symbol-soup gibberish, language switch (the last is skipped when the prompt itself is
not in a Latin script). A flagged reply gets the code `degenerate` and the record carries:
```json
"degen": {"flags": ["loop:reasoning"], "scripts": [],
          "evidence": [{"flag": "loop:reasoning", "detector": "loop", "channel": "reasoning",
                        "info": "a 2-word phrase repeated 442 times (884 words, 100% of the tail)",
                        "phrase": "red a"}]}
```
Older runs are analysed on read.

## GET /api/flags?run=<id>&status=pending|degenerate|false_positive
All flagged replies (one run, or every run newest first), each:
```json
{"run", "created", "target", "provider_name", "model", "task_id", "suite", "flags": [...],
 "label": null | "degenerate" | "false_positive", "note": "",
 "completion_tokens", "finish_reason",
 "evidence": [{"flag", "detector", "channel", "info", "phrase",
               "context": {"before": "…text before", "match": "the flagged phrase as found", "after": "text after…"}}]}
```
## POST /api/runs/<id>/reviews
Body `{"target", "task_id", "label": "degenerate" | "false_positive" | null, "note": ""}` → `{"ok": true}`.
Stored in `~/.providerbench/runs/<id>/reviews.json` (local only).
## GET /api/flags/export → JSONL of every labelled flag (label, flag, info, phrase, context, model, provider), for tuning the detectors.

Summary: `summary.review = {"flagged": 9, "confirmed": 2, "false_positive": 1, "pending": 6}` per target
(stored runs). Mock-only runs are hidden from /api/runs unless the server runs with --mock.

## v4.1: highlight spans, false negatives
- Every evidence item has `"span": [start, end]` = character range of the flagged region inside the
  record's `content` or `reasoning` (per `channel`); null for "exhausted" (nothing to point at).
- Review labels: `degenerate` (confirmed), `false_positive`, `missed` (a false negative: a human found
  degeneration the detectors did not flag). POST /api/runs/<id>/reviews accepts
  `"spans": [{"channel": "content"|"reasoning", "start": int, "end": int}]` — the text a human selected.
- /api/flags also returns human-marked `missed` replies; their evidence items have `detector: "human"`,
  `flag: "human:<channel>"`, and the review's `spans`. `?status=missed` filters them.
- `summary.review` adds `missed`.
- Each /api/flags item adds `"signal": "high" | "low"` and
  `"prompt": {"scenario", "suite_title", "signal", "note", "system" (≤800 chars), "last_user" (last 3000 chars),
  "last_user_chars", "messages", "label"}`. Synthetic workload flags are `low` (random word-list prompt: loops
  and salad are expected); trace replay and quality flags are `high` (real tasks). Items are sorted
  high-signal first, then newest. `summary.review` adds `real_tasks` and `low_signal`.

## v5: Your data scenario, Data tab, detection scope
- New scenario `custom` ("Your data") with suite `custom`: the replay engine fed only by uploads;
  its `source` param has `uploads_only: true` and no default (the UI must make the user pick or upload).
  `replay` is now SWE-smith only in the UI (the agentic suite still accepts upload sources via the CLI).
- `state.suites[].degen_check`: true for agentic, custom and all quality suites; false for synthetic.
- `POST /api/datasets/delete {"id"}` → `{"removed": true}` removes an upload from ~/.providerbench/uploads.
