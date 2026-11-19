# Architecture

Python 3.11+ standard library only; the UI is a single HTML file with two small scripts. No build step.

```
pb                        launcher: finds Python 3.11+ and runs `python -m providerbench`
providers/*.toml          one file per provider (endpoint, model ids, body tweaks, price sources)
models.toml               canonical model names, aliases, context, OpenRouter slug
examples/                 an example JSONL for "Your data"
docs/                     these guides
tests/                    offline tests; PB_NET=1 also loads every dataset
providerbench/
  __main__.py             the CLI
  config.py               providers, models, keys, ~/.providerbench
  client.py               one streaming request, timed; the key only goes to its own host
  runner.py               a run: sample → freeze → send to every target in parallel → record;
                          pause/stop, closed-loop turns, cacheable tokens, traces, exports, review labels
  report.py               summaries and the verdict
  failures.py             failure codes and shared output checks
  pricing.py              price sources (models endpoint, OpenRouter, scrape, manual) and cost
  datasets.py             your JSONL: validate, store, load
  data.py                 dataset downloads, cached in ~/.providerbench/cache
  probe.py                `pb provider add / check`, `pb models`
  doctor.py               `pb doctor`
  degen/                  degeneration detectors (loop, word salad, gibberish, language switch, extras)
  suites/                 one module per suite (see below)
  server.py               the local HTTP server and API
  ui/index.html           the whole web UI
  ui/charts.js            the chart kit (SVG, no dependencies)
  ui/md.js                safe Markdown renderer + KaTeX loader for math
  ui/snap.js              "Copy image" buttons: element → PNG → clipboard
  ui/report.html          template of the self-contained HTML report
  mock.py, mock_providers/  two fake providers for `--mock`
```

## A run, step by step

1. **Sample.** `runner.build_tasks` asks each selected suite for tasks with an rng seeded from the run's
   seed (each suite gets its own, so changing one suite's count doesn't change another's sample).
2. **Freeze.** Tasks are written to `runs/<id>/tasks.json` with a sha256.
3. **Send.** Each target gets its own thread pool (`concurrency` workers). Tasks of one session (a
   *group*) run in turn order on one worker; later turns are warm. The run tag is added to the system
   prompt and the first tool. Each request streams through `client.chat`, which times it.
4. **Record.** Each reply is graded by its suite, checked for degeneration (where enabled), costed, and
   appended to `runs/<id>/<provider>__<model>.jsonl`.
5. **Summarise.** `report.summarize` turns records into the summary; stored runs are re-summarised on read,
   so improvements to grading or metrics apply to old runs too.

## Add a suite

Create `providerbench/suites/<name>.py`:

```python
from .base import Suite, Task, Verdict
from ..failures import check_output

class MySuite(Suite):
    name = "mysuite"
    title = "My suite"
    scenario = "quality"            # which Run-page card it appears under
    method = ["What is sent.", "How it is checked."]
    graded = True                   # counts toward accuracy
    degen_check = True              # run the degeneration detectors on replies

    def params(self):               # the form fields; n is always first
        return [{"key": "n", "label": "Tasks", "type": "int", "default": 20, "min": 1, "max": 500}]

    def load(self, rng, n, **params):    # sample only with rng; fetch data with providerbench.data
        return [Task(id="mysuite/%d" % i, suite=self.name, messages=[...], max_tokens=2048,
                     expect={...}) for i in range(n)]

    def grade(self, task, reply):        # deterministic; start from check_output
        codes = check_output(reply)
        ...
        return Verdict("fail" if codes else "pass", codes, answer=..., expected=...)
```

Register it in `suites/__init__.py` and add offline grader tests in `tests/`.
