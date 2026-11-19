# Scenarios

Every scenario follows the same [method](metrics.md): a seeded sample, frozen before anything is sent,
sent byte-identical to both providers at the same time. **Preview sample** (or `--preview`) shows the
exact sample first.

| Scenario | Suite | Graded | Degeneration check |
|---|---|---|---|
| Trace replay | `agentic` | no | yes (beta) |
| Long agent session | `session` | no | yes (beta) |
| Your data | `custom` | no | yes (beta) |
| Synthetic workload | `synthetic` | no | no |
| Quality benchmarks | `tools`, `math`, `gpqa`, `robustness` | yes | yes (beta) |

## Trace replay

Real agent sessions with their real tool outputs, from either built-in source (`source=`):

- `swe-smith` (default): SWE-agent bug-fix sessions from [SWE-smith](https://huggingface.co/datasets/SWE-bench/SWE-smith-trajectories).
- `agentx`: SemiAnalysis' raw Claude Code sessions on SWE-bench tasks,
  [semianalysisai/swebench-cc-traces-raw](https://huggingface.co/datasets/semianalysisai/swebench-cc-traces-raw):
  Claude Code's real system prompt and 40 tool definitions (~50k tokens before the first message). Only 10
  sessions, so larger samples reuse a session at different steps (never the same step twice). Claude's
  thinking blocks are dropped in the conversion to OpenAI format.

- From each sampled session, a window of consecutive agent steps is chosen: context starts above
  `min_context` and the last step fits under `max_context`.
- **Open loop.** Each step sends the session up to that point; the session then continues with the
  *recorded* step and its real tool result, not the model's reply. Tools are never executed, so both
  providers get identical requests and nothing has side effects.
- Later steps share the earlier prefix, so you see warm time to first token and cache reuse.
- **No accuracy score.** Doing something different from the recorded agent isn't wrong. A step fails
  only on protocol problems (tool-call JSON that doesn't parse, a tool that wasn't offered, arguments
  that break the schema), an empty or truncated reply, or a degenerate reply. The recorded next step is
  shown in each trace for reference.

Parameters: sessions, steps per session, min / max context, max output per step.

## Long agent session

One long, real coding-agent session per sample, then a series of heavy requests on top of it. This is
the test for **decode at long context**: long reasoning, long written output, a growing session.

- **The session:** SWE-smith sessions concatenated until the prompt reaches `start_context` (default 64k).
- **Framing:** like a production coding agent: system prompts from open-source coding agents plus a
  sample of the GitHub MCP server's real tool schemas (~20k tokens), or "trace only".
- **Each turn** asks for real work over the session: root cause, the full fix, regression tests, risks,
  a reviewer's objection, the PR description, a trace of the bug. Up to 64k output, capped so the prompt
  plus output stays inside `max_context`.
- **Open loop (default):** identical requests for both providers. Each turn cuts the recorded session a
  little further (+`growth` tokens), so earlier turns are a prefix of later ones.
- **Closed loop:** the model's own reply (and a "file created" or "not available" result for each of its
  tool calls) is carried into the next turn, like a live session. Requests then differ per provider
  after the first turn; if a turn errors, the rest of that session is skipped.

Parameters: sessions, loop, start context, turns, max output per turn, context window, growth, framing.

## Your data

Your own sessions or single prompts, uploaded as JSONL. Sessions are replayed exactly like trace
replay; single requests are sent as is. See [your data](your-data.md) for the format.

## Synthetic workload

Requests built to a shape you choose, for pure serving numbers.

- Prompts are generated from a fixed English word list with the run's seed: identical for both providers.
- **Input:** a prefix shared by all sessions (cache-hit target × ISL) + unique text + "continue for about
  OSL words", with `max_tokens = OSL`.
- **Multi-turn:** each later turn adds a fixed assistant reply (~OSL words) and *turn ISL* new tokens, so
  the history is re-sent and can be cached.
- The first session runs alone to warm the shared prefix; the rest run at your concurrency.
- Sizes are targets (~1.10 tokens per word); results report the **measured** input, output and cache.
- Nothing is graded. A request fails only on errors, or if it stops at under half the requested output.
- No degeneration check: the prompts are random words, so loops are expected.

Parameters: sessions, ISL, OSL, cache-hit target, turns, new input per later turn.

## Quality benchmarks

| Suite | Data | Checked by |
|---|---|---|
| `tools` | [BFCL v4](https://github.com/ShishirPatil/gorilla/tree/main/berkeley-function-call-leaderboard): simple, multiple, parallel, parallel-multiple, irrelevance | Function names, number of calls, every argument within the allowed values; irrelevance = no call |
| `math` | [MATH-500](https://huggingface.co/datasets/HuggingFaceH4/MATH-500) and [AIME 2025](https://huggingface.co/datasets/MathArena/aime_2025) | The last `\boxed{}` against the reference, LaTeX-normalised and compared numerically |
| `gpqa` | [GPQA-Diamond](https://github.com/idavidrein/gpqa), choices shuffled per seed; max output tokens is a setting (default 64K, e.g. `gpqa:n=198;max_tokens=88000`) | The answer letter |
| `robustness` | Serving-bug probes | Literal `<think>` tags round-trip, `json_schema` output validates, parallel tool calls, clean greetings |

Grading is deterministic code; there is no LLM judge. A quality gap is only called a win when it is
larger than about two standard errors for the sample size.

GPQA asks that its questions are not posted online: they stay in your local run data.
