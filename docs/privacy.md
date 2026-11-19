# Privacy: keys and local data

providerbench is a local tool. Nothing is sent anywhere except to the providers you test, and to public
dataset and price pages.

## Where things are kept

Everything private lives in `~/.providerbench/` (owner-only), never inside the repository. Set `PB_HOME` to
use another folder.

| | |
|---|---|
| `.env` | your API keys, mode 0600 |
| `runs/` | every run: spec, frozen sample, every request and reply, your review labels |
| `uploads/` | datasets you uploaded |
| `prices.json`, `calibration.json`, `cache/` | price snapshots, output-length history, downloaded datasets |

## Keys

- Set with `./pb keys set <provider>` or the Keys page; remove with `./pb keys rm <provider>` or **Remove**.
  Environment variables work too and take precedence.
- A key is only ever sent to the host in that provider's `base_url`: checked on every request, and
  redirects are not followed.
- The local API never returns a key, and keys are never written into results or exports.
- The server binds to 127.0.0.1 only and refuses cross-origin requests.

## Before you push

```bash
./pb doctor                  # checks every file git would publish
./pb doctor --install-hook   # runs it as a git pre-commit hook
```

`pb doctor` looks for your saved key values, secret-looking strings, run data, exports and font files in
anything git would publish, and checks the data folder is private. `.gitignore` repeats the same rules.

## What leaves your machine

| To | What |
|---|---|
| The providers you test | the benchmark requests, with your key |
| Hugging Face / GitHub | dataset downloads (SWE-smith, MATH-500, AIME, BFCL, GPQA, agent prompts), cached locally |
| Provider pricing pages, OpenRouter, open.er-api.com | price and exchange-rate lookups |
| cdn.jsdelivr.net | KaTeX, only when a reply contains math (the UI works without it) |
