# providerbench

Same tasks, two providers. Bring your API keys, pick the same open-weight model on two inference
providers, and see who answers more correctly, faster, and cheaper per correct answer, with every
failure named.

```bash
git clone <repo> && cd providerbench
./pb serve                 # http://127.0.0.1:8765
./pb serve --mock          # two local fake providers: try it with no keys and no spend
```

Or from the terminal:

```bash
./pb run fireworks:glm-5.3-flash deepinfra:glm-5.3-flash --suites "agentic:n=10,turns=3" --budget 2
```

Python 3.11+, standard library only. Nothing to install.

## Five ways to test

| Scenario | What you control | What it tells you |
|---|---|---|
| **Trace replay** | Built-in SWE-smith agent sessions; sessions, steps per session, min/max context | How fast and how cheaply each provider serves real agent traffic, warm-cache behaviour on later steps, and protocol or degeneration failures |
| **Long agent session** | One long real coding-agent session (start context, e.g. 64k), framed like a production agent (~20k prompt + MCP tools), then heavy requests per turn (root cause, full fix, tests, PR text; up to 64k output). Open loop (identical requests) or closed loop (model's own replies) | Decode speed, thinking share, cache reuse and cost for long outputs at long context |
| **Your data** | Your own sessions or prompts as JSONL (Data tab, or `--data`); sessions or requests, steps per session, context range | The same as trace replay, on your real traffic |
| **Synthetic workload** | **ISL, OSL, cache-hit target, single or multi-turn, number of turns**, new input per turn | Pure serving: TTFT, tok/s, throughput, cache hits and cost for a request shape you define |
| **Quality benchmarks** | BFCL tool calling, MATH-500 + AIME, GPQA-Diamond, serving-bug probes | Known-answer accuracy, so quantization or template problems show up |

`--preview` (or **Preview sample** in the UI) shows exactly which sessions or prompts were drawn,
with their sizes and the recorded next steps, before anything is sent.

## Method

Applies to every run:

1. **Same requests.** The sample is drawn with a seed, frozen to `~/.providerbench/runs/<run>/tasks.json` with a
   sha256, and sent byte-identical to every provider, at the same time, with the same concurrency.
   The same seed replays it exactly.
2. **No stale cache.** A per-run tag opens every system prompt, so nothing is served from an earlier
   run's cache. Turns inside one session share their prefix, so warm-cache behaviour is measured.
3. **Provider numbers.** Prompt, cached, reasoning and output tokens come from each provider's own
   `usage`. TTFT = request sent → first token of any kind (connect time excluded). Output tok/s =
   (output tokens − 1) ÷ (last token − first token).
4. **Cost** = uncached input × input price + cached input × cached price + output × output price,
   at the price snapshot shown with the result.
5. **Cache, three numbers.**
   - *Cache hit* = cached ÷ prompt tokens (the provider's `usage`).
   - *Cacheable* = the share of each prompt that this provider had already prefilled earlier in the
     run, i.e. the most it could have served from cache. It's measured as a prefix match on the
     exact request text, counting only requests whose first token had arrived before this one was
     sent.
   - *Cache reuse* = cached ÷ cacheable, capped at 100%. **Compare providers on reuse**: hit alone
     depends on the workload, and the first request of anything is always a miss.
   - Results also break cache down by turn.
6. **Errors are counted, not hidden.** Retries on 429/5xx are counted. Transport errors are
   reported separately and excluded from accuracy.
7. **No LLM judge.** Every check is deterministic code.
8. **Noise.** A quality difference is reported as a win only when it is larger than about two
   standard errors for the sample size; otherwise the verdict says it is level.

### Trace replay

- From each sampled session, a window of consecutive agent steps is chosen. Its context starts
  above `min_context` and its last step fits under `max_context`.
- **Open loop.** Each step sends the session up to that point. The session then continues with the
  **recorded** step and its real tool output, not the model's reply. Tools are never executed, so
  every provider gets identical requests and nothing has side effects.
- **Not graded for accuracy.** Replay measures serving (speed, cost, cache, errors) on realistic
  agent traffic. Doing something different from the one recorded agent isn't "wrong", so there is
  no accuracy score. A reply fails only on protocol problems:
  - tool-call arguments that aren't valid JSON (`tool_call_invalid_json`)
  - a tool that wasn't offered (`tool_call_unknown_tool`)
  - arguments that break the tool's schema (`tool_args_schema`)
  - an empty or truncated reply
  - a degenerate reply (see below)
- **Reference only:** each trace shows the recorded next step and how the reply compares (same
  call, same action, different tool, ...). This isn't scored or summarised.

### Synthetic workload

- Prompts come from a fixed English word list and the run's seed. First turn = **shared prefix**
  (cache-hit target × ISL, identical in every session) + unique text + "continue for ~OSL words",
  with `max_tokens = OSL`.
- **Multi-turn.** Each later turn appends a fixed assistant reply (~OSL words, identical for both
  providers) and *turn ISL* new tokens, so the history is re-sent and can be cached.
- The first session runs alone to warm the shared prefix. The rest run at your concurrency.
- Sizes are targets (~1.10 tokens per word). Results report the **measured** ISL, OSL and cache hit.
  Turn thinking off if OSL should be answer tokens only.
- Nothing is graded for correctness. A request fails only on errors, or if it stops at under half
  the requested OSL (`short_output`).

### Quality benchmarks

| Suite | Data | Checked by |
|---|---|---|
| `tools` | [BFCL v4](https://github.com/ShishirPatil/gorilla/tree/main/berkeley-function-call-leaderboard): simple, multiple, parallel, parallel-multiple, irrelevance | BFCL-style AST match: function names, number of calls, every argument within the allowed values; irrelevance = no call |
| `math` | [MATH-500](https://huggingface.co/datasets/HuggingFaceH4/MATH-500) and [AIME 2025](https://huggingface.co/datasets/MathArena/aime_2025) | Last `\boxed{}` against the reference, LaTeX-normalised and numeric |
| `gpqa` | [GPQA-Diamond](https://github.com/idavidrein/gpqa), choices shuffled per seed | Extracted letter |
| `robustness` | Serving-bug probes | Exact round-trip of literal `<think>` tags, json_schema validation, parallel-call count, clean greetings |

## Watching, pausing, traces

- While a run is going, each provider shows its state, the requests in flight with their age, and
  an ETA. You can **pause, resume or stop one provider** without touching the other. On pause,
  requests in flight finish and no new ones start. On stop, the rest are recorded as skipped.
- **Every request is viewable**: Requests table per run (filter by provider, suite, failed), and a
  trace panel with exactly what was sent (messages with the run tag, tools, max_tokens, body
  options) and everything that came back (reasoning, content, tool calls, usage, timings, verdict).
- **Compare replies side by side:** for any request, every provider's reply sits next to the others,
  rendered as Markdown (headings, code blocks, tables, lists). You get:
  - reasoning, collapsible
  - tool calls, pretty-printed
  - timing, tokens and cost per provider
  - degeneration callouts
  - previous / next request

  Turns of one session show only what is new since the previous turn.
- **Readable exports:**
  - **HTML report:** one self-contained file with the same side-by-side layout. It opens offline in
    any browser and is easy to share.
  - **Markdown:** the whole run as one `.md`. It renders on GitHub. Long texts are clipped at 6,000
    characters (`--limit 0` for everything).
  - **Raw:** the zip (`run.json`, `tasks.json`, one `.jsonl` per provider), one provider's `.jsonl`,
    or a single request as JSON.

  ```bash
  ./pb export <run-id> --format html      # or md, or zip
  ```
- API keys and headers are never stored.

## Bring your own data

**Let your agent do the conversion.**
- Click **Format · brief for your agent** next to the trace picker and copy the brief, or run
  `./pb data brief`. Give it to Claude Code, Codex or Cursor along with your logs.
- The brief covers OpenAI, Anthropic, LangSmith/OpenTelemetry and agent-transcript formats, and says
  to strip secrets.
- The agent checks its output with `./pb data check traces.jsonl`. The full brief is in
  [docs/data-brief.md](docs/data-brief.md).

Upload a JSONL on the **Data** tab (or in the **Your data** scenario), or pass `--data file.jsonl`.
The Data tab lists your uploads with their stats, lets you delete them, and has the agent brief. One object per line,
OpenAI chat format:

```json
{"id": "fix-typo", "tools": [...optional OpenAI tool definitions...],
 "messages": [{"role": "system", "content": "..."}, {"role": "user", "content": "..."},
              {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function",
                "function": {"name": "read_file", "arguments": "{\"path\": \"README.md\"}"}}]},
              {"role": "tool", "tool_call_id": "c1", "content": "..."},
              {"role": "assistant", "content": "Done."}]}
{"id": "one-shot", "messages": [{"role": "user", "content": "Summarise TCP vs UDP."}]}
```

- A line with assistant turns is a **session**. Its steps are replayed and checked as above.
- A line without assistant turns is a single **request**, sent as is (speed, cost and output checks
  only).
- When `tools` is missing, schemas are inferred from the calls in the session.
- See [examples/traces.example.jsonl](examples/traces.example.jsonl). Uploads stay in `data/uploads/`
  (git-ignored).

## Degenerate replies and review (beta)

**Beta, real prompts only.** Degeneration detection runs on trace replay (SWE-smith), your own data
and the quality benchmarks, where prompts are real tasks and a broken reply means something.
Synthetic workloads are not checked: their prompts are random word lists, so loops are expected.

Every checked reply's content **and** reasoning go through these detectors (`providerbench/degen/`):

| Detector | Flags |
|---|---|
| loop | the same phrase repeated many times in a row (e.g. "red a" × 442) |
| word salad | a long run of content words with almost no function words |
| gibberish | symbol soup, broken characters (U+FFFD), CJK injected into Latin text |
| language switch | a run of non-Latin script in a reply to a Latin-script prompt (skipped when the prompt itself isn't Latin) |
| leak | a raw special token such as `<\|im_end\|>` in content or reasoning |
| entropy | 400+ characters that compress more than 6× with zlib, a long low-entropy loop the n-gram check can miss |
| exhausted | hit max_tokens with an empty answer: the whole budget went to reasoning |

- A flagged reply gets the `degenerate` code. Its trace shows which detector fired, in which
  channel, the flagged phrase and the numbers (period, repeats, junk ratio).
- **Highlighted in place:** every flag records the exact character range it covers. Traces and the
  compare view highlight the degenerated part inside the full reply, with **Go to degenerated part**
  to jump between highlights.
- **Review** (nav) lists every flagged reply across your runs. You can:
  - mark a flagged reply **real degeneration** or **false positive**, optionally with a note;
  - mark a reply the detectors **missed** (a false negative) by selecting the bad text in it and
    clicking **Mark selection**.
- Labels stay in `~/.providerbench/runs/<run>/reviews.json`. Run summaries count flagged /
  confirmed / false positive / pending.
- **Export labels** gives a JSONL of your labelled examples for tuning the detectors.
- **The prompt is shown with every flag**, so the reviewer sees what the model was asked.
- Flags are pointers for a human, not verdicts.

### Failure codes

Every non-passing request carries one or more codes:

- **transport:** `http_429`, `http_4xx`, `http_5xx`, `timeout`, `connection`, `empty_200`, `stream_error`
- **output:** `degenerate` (beta; not on synthetic), `truncated`, `short_output`, `tool_call_invalid_json`, `tool_call_unknown_tool`, `tool_args_schema`, `missing_tool_call`, `unexpected_tool_call`, `reasoning_leak`, `bad_json`
- **correctness:** `no_answer`, `wrong_answer`, `wrong_tool`, `wrong_call_count`, `wrong_args`, `mismatch`

The UI lists them per provider. Each one opens to the raw reply, reasoning and tool calls.

## Adding a provider

Any OpenAI-compatible endpoint:

```bash
./pb keys set acme            # or add ACME_API_KEY=... to ~/.providerbench/.env
./pb provider add acme https://api.acme.ai/v1
```

This reads `GET /models`, matches the ids to `models.toml` names (`glm-5p3-flash` → `glm-5.3-flash`), sends
a few tiny probes (stream usage, reasoning field, cached tokens, how thinking turns off), and writes
`providers/acme.toml`. Check the matches, run `./pb provider check acme`, and open a PR.
[AGENTS.md](AGENTS.md) has the file format if you prefer to write it by hand.

## Models

`models.toml` holds the canonical names (GLM-5.3, GLM-5.3 Flash, Kimi K3.1 Pro, Kimi K3, DeepSeek V4.1
Flash, Gemma 4 31B, ...) and the spellings providers use. `./pb models` reads each provider's
`/models` and tells you which served models are not mapped yet, with the exact line to add.

## Prices

`./pb prices refresh` (or Refresh on the Prices page) tries each provider's `[[pricing]]` sources
in order:

1. `models_endpoint`: prices in the provider's own `GET /models`
2. `openrouter`: that provider's listing for the model on OpenRouter (also gives the quantization it serves)
3. `scrape`: a regex over the provider's public pricing page
4. `manual`: entered by you, never overwritten

Every price keeps its source, URL and fetch time. Currency is shown in USD or INR.

## Your data stays on your machine

Everything private lives in **`~/.providerbench/`** (owner-only), never inside the repository, so a
`git push` or a Vercel deploy cannot carry it. Set `PB_HOME` to use another folder.

| In `~/.providerbench/` | |
|---|---|
| `.env` | your API keys, mode 0600 |
| `runs/` | run history: every run's spec, frozen sample, and every request and reply. History in the UI reads from here |
| `uploads/` | JSONL you uploaded |
| `prices.json`, `calibration.json`, `cache/` | price snapshots, output-length history, downloaded datasets |

- **Keys:** paste them in the UI (Keys) or run `./pb keys set <provider>`. Remove one with the
  Remove button or `./pb keys rm <provider>`. A key is only sent to the host in that provider's
  `base_url` (checked on every request; redirects are not followed). The local API never returns it,
  and it is never written into results.
- **Before you push:** run `./pb doctor`. It checks every file git would publish for saved key values,
  secret-looking strings, run data, exports and font files, and confirms the data folder is private.
  `./pb doctor --install-hook` runs it as a git pre-commit hook.
- `.gitignore` and `.vercelignore` repeat the same rules as a second line of defence.
- The server binds to 127.0.0.1 and refuses cross-origin POSTs.
- An older checkout that kept `.env` or `results/` in the repo is moved into `~/.providerbench`
  automatically on first start.

## Layout

```
pb                     launcher (finds Python 3.11+)
providers/*.toml       one file per provider
models.toml            canonical model names, aliases, OpenRouter slugs
providerbench/
  client.py            one streaming request, timed; key only to its own host
  runner.py            sample → freeze → run every target in parallel → record
  suites/              agentic (trace replay), synthetic, tools, math, gpqa, robustness
  datasets.py          your JSONL: validate, store, load
  degen/               loop, word-salad, gibberish and language-switch detectors (vendored)
  failures.py          failure codes and shared checks
  pricing.py           price sources
  probe.py             `pb provider add / check`
  report.py            summaries and the verdict
  server.py, ui/       local web UI (API in docs/api.md); ui/charts.js charts, ui/md.js Markdown + math
  mock.py              fake providers for development
tests/                 grader tests (offline); PB_NET=1 also loads every suite
```

## License

Apache-2.0, see [LICENSE](LICENSE). Third-party notices are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
