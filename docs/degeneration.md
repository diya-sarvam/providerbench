# Degeneration review (beta)

Some serving bugs don't show up as errors: the reply loops, turns into symbol soup, switches language,
or leaks template tokens. providerbench flags these for a human to look at.

**Scope (beta):** trace replay, long agent session, your data and the quality benchmarks, where prompts
are real tasks. Not the synthetic workload: its prompts are random words, so loops are expected there.

## Detectors

Content **and** reasoning are checked (`providerbench/degen/`).

| Detector | Flags |
|---|---|
| loop | the same phrase repeated many times in a row |
| word salad | a long run of content words with almost no function words |
| gibberish | symbol soup, broken characters (U+FFFD), CJK injected into Latin text |
| language switch | a run of non-Latin script in a reply to a Latin-script prompt (skipped when the prompt isn't Latin) |
| leak | a raw special token such as `<\|im_end\|>` |
| entropy | 400+ characters that compress more than 6× with zlib: a long low-entropy loop |
| exhausted | hit max_tokens with an empty answer: the whole budget went to reasoning |

A flagged reply gets the `degenerate` failure code. Its evidence records which detector fired, in which
channel, the flagged phrase, the detector's numbers, and the exact character range.

## Reviewing

- **Review** (top nav) lists every flagged reply across your runs, with the prompt it answered and the
  flagged part highlighted in context. Traces and the compare view highlight it too, with
  **Go to degenerated part** to jump between highlights.
- Mark each one **Real degeneration** or **False positive** (optionally with a note), or select text in
  any reply and **Mark selection** to record one the detectors **missed**.
- Keyboard on the Review page: `j` / `k` next / previous, `d` real, `f` false positive.
- Labels are stored per run in `~/.providerbench/runs/<run>/reviews.json`. Run summaries count flagged,
  confirmed, false positive, missed and pending.
- **Export labels** downloads your labelled examples as JSONL, to tune the detectors.

Flags are pointers for a human, not verdicts on a model.
