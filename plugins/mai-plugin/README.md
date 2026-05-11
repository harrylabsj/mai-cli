# mai-cli Plugin

`mai-plugin` is the lightweight OpenClaw native bridge for local mai-cli consultation tools.

It exposes tools for merchant setup, product publishing, product search, buyer consultations, merchant-agent polling, summaries, and recording `quote_request` or `purchase_intent` as conversation messages.

Configure `projectRoot` only if the skill is installed somewhere other than `~/.openclaw/workspace/skills/mai` or `~/.hermes/skills/commerce/mai`.

Environment fallbacks:

- `MAI_ROOT`
- `MAI_DB`
- `MAI_DATA` deprecated alias for `MAI_DB`
