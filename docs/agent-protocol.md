# Agent Protocol

## Agent

- `id`
- `type`: `merchant`
- `owner_id`
- `status`: `online`, `away`, or `human_required`
- `capabilities`
- `last_seen_at`
- optional runtime metadata: `pid`, `version`, `last_error`, `checked_count`, `replied_count`, and `stale`
- stale detection uses `MAI_AGENT_STALE_TTL_SECONDS` when set, defaulting to 60 seconds

## Conversation

- `id`
- `buyer_id`
- `merchant_id`
- optional `sku`
- `status`: `open`, `waiting_buyer`, `waiting_merchant`, `human_required`, or `closed`
- `messages`
- timestamps

## Message

- `sender`: `buyer`, `merchant_agent`, or `merchant`
- `intent`: `ask_product`, `ask_stock`, `ask_delivery`, `ask_price`, `negotiate`, `quote_request`, `purchase_intent`, or `support`
- `text`
- optional structured payload, including `source_id` for the buyer CLI, merchant agent, or operator process that created the message

Merchant agents may answer public catalog, stock, price, delivery, and substitution questions. Private discounts, commitments, stock reservation, payment evidence, refunds, disputes, and unclear requests require `human_required`.

## Tool Boundary

Merchant-agent logic uses the `MerchantAgentTools` boundary rather than directly mutating marketplace state. The local implementation is SQLite-backed, but the agent loop only depends on typed operations: heartbeat, waiting-conversation polling, product summary lookup, reply append, and human-review flag creation.

## Daemon Lifecycle

Merchant agents can run as one-shot workers or resident local daemons:

```bash
python3 scripts/mai.py --db ./mai-cli.sqlite agent run --merchant seller-a --once --format json
python3 scripts/mai.py agent start --merchant seller-a --db ./mai-cli.sqlite --interval 3 --format json
python3 scripts/mai.py agent status --merchant seller-a --db ./mai-cli.sqlite --format json
python3 scripts/mai.py agent logs --merchant seller-a --tail 20 --format json
python3 scripts/mai.py agent stop --merchant seller-a --db ./mai-cli.sqlite --format json
```

The daemon writes pid and state files under `~/.local/state/mai-cli/agents/` and JSON-line logs under `~/.local/state/mai-cli/logs/`. Set `MAI_CLI_STATE_DIR` to isolate state for tests or demos.
