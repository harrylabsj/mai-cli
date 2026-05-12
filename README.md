# mai-cli

mai-cli is a standalone, SQLite-backed AI consultation runtime for local commerce. Merchants publish shop profiles, products, stock, and delivery rules; buyers search nearby supply, open consultations, and receive deterministic merchant-agent replies.

The MVP is not a transaction system. It does not create commitments, reserve stock, process payment, record payment state, custody funds, handle refunds, dispatch couriers, or claim delivery success. Buyer intent is recorded only as `quote_request` or `purchase_intent` messages in a conversation.

## Install and Verify

```bash
bash scripts/verify.sh
```

Optional API dependencies are declared in `pyproject.toml` for the FastAPI marketplace service:

```bash
pip install -e '.[api]'
```

## Quick Start

```bash
python3 scripts/mai.py --db ./mai-cli.sqlite merchant create \
  --id seller-a \
  --name "West Lake Tea" \
  --city Hangzhou \
  --service-area "West Lake" \
  --contact "wechat:westlake" \
  --hours "09:00-21:00" \
  --delivery-fee 12 \
  --delivery-eta-minutes 45 \
  --tags "tea,gift,longjing"

python3 scripts/mai.py --db ./mai-cli.sqlite product add \
  --merchant seller-a \
  --sku tea-a \
  --title "Longjing Gift Box" \
  --price 88 \
  --stock 5 \
  --category tea \
  --tags "longjing,gift" \
  --delivery-attributes "same-city,courier"

python3 scripts/mai.py --db ./mai-cli.sqlite merchant update --id seller-a --hours "10:00-20:00"
python3 scripts/mai.py --db ./mai-cli.sqlite product update --merchant seller-a --sku tea-a --stock 4 --price 92
python3 scripts/mai.py --db ./mai-cli.sqlite search merchants --query "west lake" --city Hangzhou --format json

python3 scripts/mai.py --db ./mai-cli.sqlite buyer ask \
  --buyer alice \
  --text "longjing gift delivery today" \
  --city Hangzhou \
  --area "West Lake" \
  --format json

python3 scripts/mai.py --db ./mai-cli.sqlite agent run --merchant seller-a --once --format json
python3 scripts/mai.py --db ./mai-cli.sqlite buyer summarize --conversation CONV-0001 --format json
python3 scripts/mai.py --db ./mai-cli.sqlite buyer intent --conversation CONV-0001 --intent purchase_intent --text "Buyer wants merchant confirmation." --format json

printf 'longjing gift delivery today\n/summary\n/quit\n' | \
  python3 scripts/mai.py --db ./mai-cli.sqlite buyer chat --buyer alice --city Hangzhou --area "West Lake" --format json
```

Default database path: `~/.local/share/mai-cli/mai-cli.sqlite`.

## Channel Ingress

External channel adapters can ingest buyer messages through a stable local entry point before real WhatsApp, Telegram, Slack, or OpenClaw/Hermes gateway bridges are attached:

```bash
python3 scripts/mai.py --db ./mai-cli.sqlite channel ingest \
  --channel whatsapp \
  --external-user "+15550001111" \
  --text "longjing gift delivery today" \
  --city Hangzhou \
  --area "West Lake" \
  --format json

python3 scripts/mai.py --db ./mai-cli.sqlite channel ingest \
  --channel whatsapp \
  --external-user "+15550001111" \
  --conversation CONV-0001 \
  --text "Any stock left?" \
  --format json
```

The buyer id is always derived as `<channel>:<external-user>` for public channel ingress. Message payloads preserve `source_id`, `channel`, `external_user_id`, and optional `external_message_id`. When `external_message_id` is provided, retries with the same `(channel, external_user_id, external_message_id)` return the original message instead of appending a duplicate.

## Host Adapter Diagnostics

OpenClaw and Hermes remain optional hosts. Use `adapter` diagnostics to inspect local setup before running demos:

```bash
python3 scripts/mai.py adapter inspect --host openclaw --format json
python3 scripts/mai.py adapter doctor --host hermes --format json
python3 scripts/mai.py adapter install-command --host openclaw --dry-run --format json
```

`inspect` reports host command availability, project root validity, skill root status, symlink target, stale skill detection, and DB path. `doctor` turns those checks into actionable issues. `install-command` prints the install command without executing it.

## Conversation CLI

The raw conversation lifecycle is available without the API server:

```bash
python3 scripts/mai.py --db ./mai-cli.sqlite conversation create --buyer alice --merchant seller-a --sku tea-a --intent ask_stock --text "Is this available?" --format json
python3 scripts/mai.py --db ./mai-cli.sqlite conversation message --conversation CONV-0001 --sender merchant_agent --intent ask_stock --text "Stock is 5." --status waiting_buyer --format json
python3 scripts/mai.py --db ./mai-cli.sqlite conversation human-review --conversation CONV-0001 --reason low_confidence --format json
python3 scripts/mai.py --db ./mai-cli.sqlite conversation resolve-review --conversation CONV-0001 --action reply --sender merchant --text "Human reviewed." --format json
python3 scripts/mai.py --db ./mai-cli.sqlite conversation close --conversation CONV-0001 --sender operator --text "Closed." --format json
python3 scripts/mai.py --db ./mai-cli.sqlite conversation list --buyer alice --status waiting_buyer --format json
python3 scripts/mai.py --db ./mai-cli.sqlite human-review queue --format json
python3 scripts/mai.py --db ./mai-cli.sqlite human-review show --review 1 --format json
python3 scripts/mai.py --db ./mai-cli.sqlite human-review resolve --review 1 --action reply --sender merchant --text "Human reviewed." --format json
python3 scripts/mai.py --db ./mai-cli.sqlite agent list --format json
python3 scripts/mai.py --db ./mai-cli.sqlite agent show --agent mai-cli-merchant-agent:seller-a --format json
```

## Resident Agent Daemon

Use `agent run --once` for a single deterministic polling pass. Use the daemon lifecycle commands when a merchant agent should keep polling in the background:

```bash
python3 scripts/mai.py agent start --merchant seller-a --db ./mai-cli.sqlite --interval 3 --format json
python3 scripts/mai.py agent status --merchant seller-a --db ./mai-cli.sqlite --format json
python3 scripts/mai.py agent logs --merchant seller-a --tail 20 --format json
python3 scripts/mai.py agent stop --merchant seller-a --db ./mai-cli.sqlite --format json
python3 scripts/mai.py agent start --merchant seller-a --api-url http://127.0.0.1:8765 --agent-token "$MAI_AGENT_TOKEN" --interval 3 --format json
```

Pid, state, and log files are written under `~/.local/state/mai-cli/` by default. Set `MAI_CLI_STATE_DIR` to use a different state directory for tests or demos.

To run through the Marketplace API boundary instead of direct SQLite access:

```bash
python3 scripts/mai.py --db ./mai-cli.sqlite agent token --merchant seller-a --format json
python3 scripts/mai.py agent run --merchant seller-a --once --api-url http://127.0.0.1:8765 --agent-token "$MAI_AGENT_TOKEN" --format json
python3 scripts/mai.py agent run --merchant seller-a --api-url http://127.0.0.1:8765 --agent-token "$MAI_AGENT_TOKEN" --interval 3
python3 scripts/mai.py --db ./mai-cli.sqlite agent revoke-token --merchant seller-a --token "$MAI_AGENT_TOKEN" --format json
```

Use `agent token` locally, or `POST /agents/tokens` with a merchant token over the API, to issue a narrower token for the default merchant agent. Use `agent revoke-token` locally, or `POST /agents/tokens/revoke` with a merchant token over the API, to revoke a scoped agent token. API-backed agent runs accept `--agent-token` or `MAI_AGENT_TOKEN` for that scoped token, while `--merchant-token` and `MAI_MERCHANT_TOKEN` remain available for local demos.
Set `MAI_MARKETPLACE_API_URL` or `MAI_API_URL` to omit `--api-url` from repeated agent runs or background starts. `agent start --api-url` passes credentials to the child process through environment variables and keeps tokens out of the recorded pid command.

## Marketplace API

Inspect routes:

```bash
python3 scripts/mai.py --db ./mai-cli.sqlite api routes --format json
```

The local API covers catalog, search, conversations, message append/close, agent token issuance/revocation, agent heartbeats, agent message claim/complete/fail/abandon, LLM tool-call audit records, and human-review queue/detail/resolve operations. In environments without FastAPI installed, `create_app()` still returns a lightweight ASGI app for local tests and demos.

External channel adapters can use `POST /channels/messages` with `channel`, `external_user_id`, `text`, and optional `conversation_id`, `city`, `area`, and `external_message_id`. The optional `external_message_id` is an idempotency key for webhook retry safety.

`POST /merchants` returns a local `merchant_token`. Product writes, merchant profile updates, merchant human replies, merchant/operator closes, and human-review resolution require that merchant token in the JSON body as `merchant_token` or as a Bearer token. Agent heartbeats, agent message processing, merchant-agent replies, merchant-agent closes, and merchant-agent human-review flags may use either the merchant token or a scoped agent token. Buyer search and buyer conversation creation remain tokenless for local demos, but created buyer conversations return a conversation-scoped `buyer_token`. Conversation reads, buyer message appends, buyer closes, and human-review queue/detail reads require an owner token: buyer tokens can read or write only their issued conversation, while merchant and agent tokens can read conversations and review queues for their merchant.

Serve the FastAPI app after installing API dependencies:

```bash
python3 scripts/mai.py --db ./mai-cli.sqlite api serve --host 127.0.0.1 --port 8765
```

## Optional LLM Tool Loop

`llm run` exposes the guarded OpenAI-compatible tool loop for local demos and host adapters:

```bash
export MAI_LLM_API_KEY=...
export MAI_LLM_MODEL=gpt-4.1-mini
python3 scripts/mai.py --db ./mai-cli.sqlite llm run --role buyer --actor alice --text "Find longjing near Hangzhou" --max-tool-calls 4 --provider-retries 1 --format json
python3 scripts/mai.py --db ./mai-cli.sqlite llm run --role buyer --actor alice --conversation CONV-0001 --text "Continue this consultation" --max-tool-calls 4 --format json
python3 scripts/mai.py llm run --role buyer --actor alice --api-url http://127.0.0.1:8765 --auth-token "$MAI_BUYER_TOKEN" --conversation CONV-0001 --text "Continue through API" --format json
```

Set `MAI_LLM_BASE_URL`, `MAI_LLM_MODEL`, `MAI_LLM_TIMEOUT_SECONDS`, and `MAI_LLM_MAX_TOKENS` to target another OpenAI-compatible provider. Add `--conversation` to inject owned conversation context into the prompt; buyer actors must own the buyer side and merchant actors must own the merchant side unless using a privileged local/operator scope. Add `--api-url --auth-token` to route LLM tools through the Marketplace API and its Bearer-token authorization boundary instead of direct SQLite access. API-backed LLM tool calls record `llm_tool_call` audit events with host, session, actor, token scope, tool, status, and error details. The runner enforces scoped marketplace tools, bounded provider retries, `max_steps`, and `max_tool_calls`; tool or provider failures return deterministic fallback content for human review.

## Legacy Import

Existing Mai JSON catalogs can be imported:

```bash
python3 scripts/mai.py --db ./mai-cli.sqlite legacy import --from-json ./mai.json --format json
```

Only merchants and products are imported. Legacy transaction data is ignored by design.
