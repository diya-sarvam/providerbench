# Traces, controls and exports

## While a run is going

Each provider shows its state, a progress bar, the requests in flight with their age, spend, and an ETA.

- **Pause** a provider: its requests in flight finish, no new ones start. The other keeps going.
- **Resume** it, or **Stop** it: the rest of its requests are recorded as skipped.
- **Stop all** ends the run. The budget is a hard stop for the whole run.

Progress is written to disk every few seconds, so History has it even if the server stops.

## After a run

- **Scorecard:** cost, time to first token, output speed, cache reuse, errors (and accuracy for quality
  suites). The better value is green when the gap is real (≥ 10%, or ≥ 5 points); otherwise "level".
- **Charts:** median with p25–p90 per metric, output speed vs time to first token per request, and time to
  first token over the run.
- **Requests:** every request, both providers on one row, with the difference as **A − B** (negative TTFT
  means A was faster). Filter by suite, failed, or where the providers differ.
- **Compare:** any request side by side: the request once, then each provider's reply rendered as Markdown
  (code, tables, math), reasoning, tool calls, and timing, tokens and cost. ← / → move between requests.
  Turns of one session show only what is new since the previous turn.
- **Trace:** exactly what was sent (messages with the run tag, tools, max_tokens, body options) and
  everything that came back (reasoning, content, tool calls, usage, timings, verdict, failure codes).

## Copy as image

Every scorecard, table, chart and History card has a **Copy image** button (top right, on hover). It
renders that element as a PNG at 2× in your current theme, with a one-line caption (providers, model,
scenario, size) and a small footer, and puts it on your clipboard, ready to paste into a post or a
chat. If the browser blocks clipboard images, the PNG is downloaded instead.

## Exports

| Export | What | Command |
|---|---|---|
| HTML report | One self-contained file, side-by-side replies, opens offline | `./pb export <run> --format html` |
| Markdown | The whole run as one `.md` (long texts clipped at 6,000 chars; `--limit 0` for all) | `./pb export <run> --format md` |
| Raw zip | `run.json`, `tasks.json`, one `.jsonl` per provider | `./pb export <run>` |
| One provider | that provider's `.jsonl` | UI: run page → Export |
| One request | the full trace as JSON | UI: trace → Download JSON |

No API keys or request headers are stored, so none are exported.
