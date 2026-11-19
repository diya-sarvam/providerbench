# Brief: convert my data into providerbench JSONL

You are converting my LLM request logs into the input format of **providerbench**, a tool that replays
the same requests against two inference providers and compares speed, cost, cache behaviour and failures.
Produce one file, `traces.jsonl`, and check it with the command at the end.

## Output format

UTF-8, **one JSON object per line**, no trailing commas, no wrapping array.

```json
{"id": "checkout-bug-0142", "tools": [ ...OpenAI tool definitions, optional... ], "messages": [ ...OpenAI chat messages... ]}
```

- `id`: optional, a short stable string (session id, ticket id). Unique per line.
- `messages`: required, the conversation in **OpenAI Chat Completions format**, oldest first.
  - Roles: `system`, `user`, `assistant`, `tool` (`developer` is accepted and treated as `system`).
  - Assistant tool calls go in `tool_calls`:
    `{"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"a.py\"}"}}`.
    `arguments` is a **JSON string**, not an object.
  - Every tool call is followed by a `tool` message with the matching `tool_call_id` and the result as
    `content` (a string).
  - `content` is a string. Multi-part content (text plus images) may stay as the OpenAI list form.
- `tools`: optional. The tool definitions the agent had (`{"type": "function", "function": {"name",
  "description", "parameters": <JSON schema>}}`). If you leave it out, schemas are inferred from the calls.

### Two kinds of line

1. **Session**: the messages include assistant turns. Each assistant turn becomes a replayable step.
   providerbench sends the conversation up to that step and records what each provider replies. The
   conversation then continues with **your recorded** assistant turn and tool results, so both
   providers always get identical requests. Keep whole sessions in order. Don't drop the tool results.
2. **Single request**: no assistant turn. It is sent as is (good for plain prompt sets).

## Converting common sources

- **OpenAI-style logs** (request `messages` plus the response `choices[0].message`): append the response
  message to the request messages. Consecutive requests of one conversation should become one
  session line, not many.
- **Anthropic Messages API logs:** `system` (top-level) becomes the first `system` message. `user` and
  `assistant` text blocks become `content` strings. A `tool_use` block becomes an assistant `tool_calls`
  entry (`id` stays, `input` becomes `json.dumps(input)` as `arguments`). A `tool_result` block (inside
  a user turn) becomes a separate `tool` message with `tool_call_id` = `tool_use_id`. Drop `thinking`
  blocks.
- **LangChain / LangSmith / OpenTelemetry GenAI spans:** group spans by trace or session id, order
  them by start time, and map each LLM call's input and output messages as above. Tool spans become
  `tool` messages.
- **Agent frameworks with their own transcript format:** map each model turn to an `assistant`
  message, each tool invocation to `tool_calls`, and each observation to a `tool` message.

## Rules

- **Remove secrets and personal data before writing the file:** API keys, tokens, passwords, emails,
  phone numbers, customer names. Replace them with placeholders like `<EMAIL>`. The file stays on my
  machine (providerbench stores uploads in `~/.providerbench/uploads`), but treat it as sensitive anyway.
- Don't invent or summarise content. Copy the real text. Truncating a single huge tool output to
  about 20k characters is fine (mark it `…[truncated]`).
- Keep `system` prompts and tool definitions exactly as the agent saw them. They drive caching.
- Skip conversations that are empty, broken, or have no user message.
- Aim for 20 to 500 lines. More is fine, but providerbench samples from the file anyway.

## Check your output

Run this from the providerbench folder and fix every error it prints:

```bash
./pb data check traces.jsonl
```

It prints how many sessions, requests, steps and tools it found, plus the first errors with line
numbers. Then show me the summary line and 2 converted lines (shortened) so I can sanity-check them.

To preview what a run would send:

```bash
./pb run <provider>:<model> <provider>:<model> --data traces.jsonl --suites "agentic:n=10,turns=3,min_context=0" --preview
```
