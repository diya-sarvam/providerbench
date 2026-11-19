# Your data

Run your own traffic: real agent sessions or plain prompts, as JSONL.

## The quick way: let your agent convert it

Your logs are probably in some other shape (OpenAI request logs, Anthropic messages, LangSmith or
OpenTelemetry spans, an agent framework's transcripts). Hand your coding agent the brief:

```bash
./pb data brief            # prints it; also in the UI: Data → Format · brief for your agent
```

It explains the format, how to convert each common source, to strip secrets, and how to check the
result. The full text is [data-brief.md](data-brief.md).

## The format

One JSON object per line, OpenAI chat format:

```json
{"id": "fix-typo", "tools": [ ...optional OpenAI tool definitions... ],
 "messages": [{"role": "system", "content": "..."}, {"role": "user", "content": "..."},
              {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function",
                "function": {"name": "read_file", "arguments": "{\"path\": \"README.md\"}"}}]},
              {"role": "tool", "tool_call_id": "c1", "content": "..."},
              {"role": "assistant", "content": "Done."}]}
{"id": "one-shot", "messages": [{"role": "user", "content": "Summarise TCP vs UDP."}]}
```

- A line **with** assistant turns is a **session**: its steps are replayed like
  [trace replay](scenarios.md#trace-replay) (the recorded step continues the session; both providers get
  identical requests).
- A line **without** assistant turns is a single **request**, sent as is.
- `arguments` in tool calls is a JSON *string*. Every tool call is followed by its `tool` result.
- No `tools`? Schemas are inferred from the calls in the session.

Example: [examples/traces.example.jsonl](../examples/traces.example.jsonl).

## Check it

```bash
./pb data check traces.jsonl
```

Prints sessions, requests, replayable steps, tools found, and the first errors with line numbers.

## Run it

- **UI:** Data tab → upload (or drag the file in) → *Use in a run*, or pick it in the **Your data** scenario.
- **CLI:**

  ```bash
  ./pb run fireworks:glm-5.3-flash deepinfra:glm-5.3-flash --data traces.jsonl --suites "custom:n=20,turns=2" --preview
  ```

Uploads are stored in `~/.providerbench/uploads/`, never in the repository. Delete them on the Data tab.
