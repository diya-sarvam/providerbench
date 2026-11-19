# Providers, models and prices

## Providers

Each provider is one file in `providers/`. Any OpenAI-compatible chat-completions endpoint works.

Included: Baseten, Blaze, DeepInfra, Fireworks, JarvisLabs, OpenRouter, Sarvam, Together.

### Add one in one command

```bash
./pb keys set acme
./pb provider add acme https://api.acme.ai/v1
./pb provider check acme
```

`provider add` reads `GET /models`, matches the ids to `models.toml` names (`glm-5p3-flash` →
`glm-5.3-flash`), sends a few tiny probes (stream usage, reasoning field, cached tokens, how thinking
turns off), and writes `providers/acme.toml`. Review the matches, then open a PR.

### The file

```toml
name = "Acme"                              # display name
base_url = "https://api.acme.ai/v1"        # OpenAI-compatible root; /chat/completions is appended
key_env = "ACME_API_KEY"                   # where the key lives ("" = no key)
docs = "https://acme.ai/pricing"

[headers]                                  # optional; {session} = one id per session (cache affinity)
x-session-affinity = "{session}"

[models]                                   # canonical name (models.toml) -> this provider's id
"glm-5.3-flash" = "acme/glm-5.3-flash"

[thinking_off]                             # body merged when a run asks for thinking off; "*" = all models
"glm-5.3-flash" = { reasoning_effort = "none" }

[extra_body]                               # body merged on every request for that model
"gemma-4-31b" = { chat_template_kwargs = { enable_thinking = true } }

[[pricing]]                                # tried in order; the first that finds a price wins
source = "models_endpoint"                 # price in GET /models (add public = true if no key is needed)

[[pricing]]
source = "openrouter"                      # this provider's listing on OpenRouter
provider = "Acme"                          # provider name as OpenRouter shows it
tag = "acme"                               # optional: one endpoint variant

[[pricing]]
source = "scrape"                          # a regex over a public page; prices per 1M tokens
url = "https://acme.ai/pricing"
currency = "USD"
pattern = '''(?m)^\| (?P<model>[\w./-]+) \| \$(?P<input>[\d.]+) \| \$(?P<output>[\d.]+) \|'''

[[pricing]]
source = "manual"                          # entered by you; never overwritten
```

A `{ENV_VAR}` or `{model}` placeholder in `base_url` is filled in at request time.

## Models

`models.toml` lists canonical names (GLM-5.3, GLM-5.3 Flash, Kimi K3.1 Pro, Kimi K3, DeepSeek V4.1 Flash,
Gemma 4 31B, ...) with their context window, OpenRouter slug, and the spellings providers use.

```bash
./pb models
```

reads every provider's `/models` (where your key or a public endpoint allows) and prints the exact
`[models]` lines to add, ids a provider stopped listing, and ids nothing matches yet. A model that isn't
in `models.toml` can still be run as `provider:<raw model id>`.

## Prices

```bash
./pb prices refresh
./pb prices
./pb prices set acme glm-5.3-flash 0.15 0.50 --cached 0.03 --currency USD
```

Prices are per 1M tokens in the provider's own currency, each with its source, URL and fetch time. The UI
converts everything to USD or INR. OpenRouter listings also give the quantization a provider serves.
