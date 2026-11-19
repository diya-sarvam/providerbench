# Metrics and method

## The method (every run)

1. **Same requests.** The sample is drawn with a seed and frozen to `runs/<run>/tasks.json` with a sha256
   before anything is sent. Both providers get the same bytes, at the same time, with the same
   concurrency. The same seed replays the same sample.
2. **No stale cache.** A per-run tag opens the system prompt and the first tool's description, so nothing
   can be served from an earlier run's cache. Turns inside one session share their prefix on purpose.
3. **Provider numbers.** Token counts and cache hits come from each provider's own `usage`.
4. **Errors are counted, never hidden.** Retries on 429 / 5xx are counted. Transport errors are reported
   separately and excluded from accuracy.
5. **No LLM judge.** Every check is deterministic code.
6. **Noise.** Small samples are noisy: a quality gap within about two standard errors is reported as
   "level", and speed or cost gaps under 10% as "about even".

## The numbers

| Metric | Definition |
|---|---|
| Time to first token (TTFT) | Request sent → first streamed token of any kind (reasoning, text or a tool call). Connection setup excluded. |
| Output tok/s | (output tokens − 1) ÷ (last token − first token). Only when ≥ 32 tokens came back. |
| End-to-end | Request sent → stream finished. |
| Throughput | All output tokens ÷ wall time of the run, per provider. |
| Input / output tokens | The provider's `prompt_tokens` / `completion_tokens`. Reasoning tokens from `completion_tokens_details` when reported, else estimated from character share and marked. |
| Cost | Uncached input × input price + cached input × cached price + output × output price, at the price snapshot shown with the result. Per request, per correct task (quality suites), and total. |
| Accuracy | Passed ÷ graded tasks, quality suites only. |

Medians (p50) are shown by default; tables also have p90.

## Cache: three numbers

| | |
|---|---|
| **Cache hit** | Cached ÷ prompt tokens, from the provider's `usage`. |
| **Cacheable** | The most that *could* have been cached: the part of each prompt that this provider had already prefilled earlier in the run (a prefix match on the exact request, counting only requests whose first token had arrived before this one was sent), in the provider's own token count. |
| **Cache reuse** | Cached ÷ cacheable, capped at 100%. **Compare providers on this one.** |

Hit alone depends on the workload (the first request of anything is a miss); reuse says how much of
what it could reuse a provider actually reused. Results also break cache down by turn.

## Failure codes

Every request that doesn't pass carries one or more codes.

| Kind | Codes |
|---|---|
| Transport | `http_429`, `http_4xx`, `http_5xx`, `timeout`, `connection`, `empty_200` (HTTP 200 with no tokens), `stream_error`, `budget` (skipped) |
| Output | `truncated`, `short_output`, `tool_call_invalid_json`, `tool_call_unknown_tool`, `tool_args_schema`, `missing_tool_call`, `unexpected_tool_call`, `reasoning_leak`, `bad_json`, `degenerate` (beta) |
| Correctness | `no_answer`, `wrong_answer`, `wrong_tool`, `wrong_call_count`, `wrong_args`, `mismatch` |

Each failure in the UI opens the raw reply, reasoning and tool calls.
