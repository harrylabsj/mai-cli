# mai-cli Design Spec

## Goal

Build `mai-cli`, an independent AI marketplace consultation runtime for local commerce. Buyers use buyer-side CLI/tools, merchants run resident merchant agents, and both sides communicate through a marketplace service without requiring OpenClaw or Hermes.

The first wedge is nearby local-life commerce: tea shops, restaurants, flowers, gifts, and same-city merchants where real-time consultation, stock checks, delivery feasibility, substitutions, and human confirmation matter more than pure SKU search.

## Chosen Approach

- Create a standalone runtime named `mai-cli`.
- Keep OpenClaw and Hermes as optional adapters, not required hosts.
- Reuse current Mai catalog, search, risk, conversation, and merchant-agent concepts.
- Upgrade the JSON/HTTP registry into a service-oriented API with database-backed trusted state.
- Start with a deterministic vertical slice before adding full LLM buyer/merchant runtimes.

## Product Boundary

`mai-cli` MVP is a two-sided AI consultation network, not a transaction system.

- Buyer tools help buyers express needs, search merchants, compare options, ask follow-up questions, and summarize responses.
- Merchant resident agents answer product, stock, delivery, price, and substitution questions using shop data and public merchant rules.
- The marketplace API stores trusted state: merchants, products, delivery rules, conversations, messages, agent heartbeats, moderation decisions, and risk flags.
- The MVP does not confirm orders, reserve inventory, charge payments, custody funds, dispatch couriers, or process refunds.

Core principle: AI can consult, compare, and negotiate within public rules; formal merchant commitments or transactions are later milestones and require explicit merchant confirmation.

## MVP Flow

1. Merchant creates a shop profile with city, service area, contact, hours, and delivery rules.
2. Merchant publishes products with price, stock, category, tags, and delivery attributes.
3. Merchant starts a resident merchant agent process.
4. Buyer uses the buyer CLI to ask for a nearby product or service.
5. Buyer CLI searches marketplace inventory and merchants.
6. Buyer CLI opens a conversation with one merchant agent.
7. Merchant agent answers stock, price, delivery range, ETA, and substitution questions.
8. Buyer CLI summarizes the option, warnings, missing facts, and next action.
9. If buyer wants to proceed, MVP records `purchase_intent` or `quote_request` as a message only; no order is created.

Example:

```text
Buyer: 我在西湖附近，今天想买两盒龙井礼盒，能送吗？
Buyer CLI: searches nearby tea merchants and opens a conversation.
Merchant Agent: checks stock=5 and delivery rules, then replies with price and ETA.
Buyer CLI: summarizes the option and states purchase/payment is not handled in this version.
System: conversation is recorded; no inventory is reserved and no order is created.
```

## Architecture

```text
Buyer CLI <-> Marketplace API <-> Merchant Agent <-> Merchant CLI
                          |
                          v
                    Database / Events
```

### Marketplace API

The API is the trusted state boundary. It owns merchant profiles, products, inventory attributes, delivery rules, conversations, messages, agent heartbeat records, moderation, and risk flags.

Agents call this API through typed tools. They do not mutate trusted state directly and should not write the database directly.

The marketplace is a gateway, state store, and message broker. It is not where merchant agents run.

### Marketplace API Completeness

The MVP API must expose the full consultation loop, not only catalog/search. These routes should be available through both FastAPI and equivalent CLI commands.

Conversation APIs:

```text
POST /conversations
GET /conversations/{conversation_id}
GET /buyers/{buyer_id}/conversations
GET /merchants/{merchant_id}/conversations
POST /conversations/{conversation_id}/messages
POST /conversations/{conversation_id}/close
```

Conversation requirements:

- Public buyers can create conversations and buyer messages without an API key in local/demo mode.
- Merchant-scoped tokens are required for merchant or merchant-agent replies.
- Conversation list endpoints support `status`, `merchant_id`, `buyer_id`, `sku`, and `updated_since` filters.
- Message creation updates conversation status deterministically:
  - buyer message -> `waiting_merchant`
  - merchant/merchant_agent message -> `waiting_buyer`
  - human-review flag -> `human_required`
  - close action -> `closed`
- Every message stores sender, intent, text, structured payload, created_at, and source agent/process id.

API behavior requirements:

- FastAPI and fallback ASGI routes must return the same status codes and JSON error shapes.
- Auth failures return `403 {"ok": false, "error": ...}` in both implementations, never an unhandled 500.
- Missing or malformed request fields return deterministic `400` errors with no partial state changes.
- Route metadata from `api routes` must match the live FastAPI app and fallback app.
- Token-bearing requests may pass credentials in `Authorization: Bearer <token>` or local-demo JSON payloads; responses must not echo tokens except at merchant creation time.

Agent heartbeat APIs:

```text
POST /agents/heartbeat
GET /agents
GET /agents/{agent_id}
GET /merchants/{merchant_id}/agents
```

Heartbeat requirements:

- Heartbeats include `agent_id`, `type`, `owner_id`, `status`, `capabilities`, `pid` optional, `version`, `last_error` optional, and counters such as `checked_count` and `replied_count`.
- The API marks agents stale when `last_seen_at` exceeds a configured TTL.
- `agent status` CLI reads the same heartbeat record plus local pid/log state.

Human-review APIs:

```text
GET /human-review/queue
GET /merchants/{merchant_id}/human-review
POST /conversations/{conversation_id}/human-review
POST /conversations/{conversation_id}/human-review/resolve
```

Human-review requirements:

- A review item links to conversation_id, merchant_id, buyer_id, optional sku, reason, severity, created_at, and resolved_at.
- Merchant agents create review items for bargaining, low stock, unclear delivery, unsupported product, suspicious content, or low confidence.
- Merchant humans can resolve with `reply`, `approve_public_answer`, `reject`, or `close`.
- Resolution appends an auditable conversation message and updates conversation status.

### Multi-Agent Orchestration Harness

`mai-cli` needs a small multi-agent orchestration layer. This is the product's harness: the runtime that coordinates independent buyer-side processes, merchant agents, the marketplace API, and human review without embedding all logic in one process.

In this context, a harness means:

- agent identity and lifecycle management
- conversation routing between buyer, buyer CLI/agent, merchant agent, merchant human, and operator
- tool invocation boundaries and permissions
- message delivery state, retries, and idempotency
- human-review escalation and resolution
- audit logs for every agent-visible state transition

It is not the same as the marketplace API. The API stores trusted state and exposes tools; the harness decides which agent/process should act next and records that action through the API.

MVP orchestration model:

```text
buyer_cli sends message
  -> marketplace stores message and sets conversation=waiting_merchant
  -> merchant_agent daemon polls assigned conversations
  -> merchant_agent replies or creates human_review item
  -> buyer_cli summarizes replies or waits for human review
```

Future orchestration model:

```text
buyer_agent daemon <-> harness queue <-> merchant_agent daemon <-> merchant_human console
                            |
                            v
                     marketplace API/state
```

Harness requirements for MVP:

- Assign each conversation to a merchant agent by `merchant_id`.
- Ensure each buyer message is processed at most once by the same merchant agent using message idempotency keys.
- Claiming a message must be atomic across multiple local daemons or processes. A claim succeeds only when no processed/in-flight claim already owns `(agent_id, message_id)`, and the worker must not append a reply unless it owns the claim.
- Retry is allowed only from explicit failed/abandoned processing state; processed messages are immutable for idempotency purposes.
- Track per-conversation `next_actor`: `buyer`, `merchant_agent`, `merchant_human`, or `operator`.
- Support retry after transient failures and store `last_error` on the agent heartbeat.
- Move ambiguous/bargaining/high-risk conversations to `human_required` instead of looping agent replies.
- Preserve an append-only audit trail of routing decisions and agent actions.

Deferred harness features:

- Dedicated queue backend.
- WebSocket/webhook delivery.
- Multiple merchant agents competing for the same merchant workload.
- Buyer-agent daemon with autonomous multi-step planning.
- Cross-merchant auctioning or routing optimization.

### Resident Merchant Agent

A resident merchant agent is an independent long-running process or daemon, not a registry thread.

First version behavior:

- Poll marketplace conversations for `waiting_merchant` messages.
- Read trusted shop/product/delivery data through the API.
- Reply deterministically to stock, price, delivery, timing, and substitution questions.
- Mark unclear, high-risk, bargaining, or unsupported conversations as `human_required`.
- Send heartbeats so the marketplace can show agent health.

Use polling for MVP because it runs locally, in OpenClaw/Hermes, under launchd, in tmux, or in a container. Add webhooks/WebSocket later only after the vertical slice is reliable.

### Agent Daemon Runtime

The first missing platform capability is a real long-running agent daemon with lifecycle management. This should be implemented before LLM autonomy or order drafts.

Required CLI surface:

```bash
mai-cli agent start --merchant seller-a --db ./mai-cli.sqlite --interval 3
mai-cli agent stop --merchant seller-a
mai-cli agent status --merchant seller-a
mai-cli agent logs --merchant seller-a
mai-cli agent run --merchant seller-a --once
```

Runtime responsibilities:

- Create a pid file per merchant agent, for example `~/.local/state/mai-cli/agents/seller-a.pid`.
- Write structured logs per merchant agent, for example `~/.local/state/mai-cli/logs/seller-a.log`.
- Send heartbeat records to the marketplace while running.
- Poll conversations continuously and call the same deterministic `process_once` logic used by `--once`.
- Exit cleanly on SIGTERM/SIGINT and mark the agent `away` or `offline`.
- Refuse duplicate starts for the same merchant unless the previous pid is stale.
- Provide `status` output with pid, running state, last heartbeat, checked/replied counters, and last error.

Implementation options:

- MVP: foreground/background local process started by `mai-cli agent start`, with pid/log files.
- macOS/Linux integration: generate launchd/systemd snippets after the MVP works.
- Container integration: run the same daemon command as the container entrypoint.

Do not put this loop inside the Marketplace API process. The API remains the gateway and state boundary; the agent daemon is a separate worker process.

### Buyer CLI

The first buyer implementation should be CLI-first and deterministic.

Responsibilities:

- parse buyer demand into simple search terms and optional city/area constraints
- search merchants and products
- open a conversation with a selected merchant
- send stock, delivery, price, and substitution questions
- summarize replies, warnings, and missing facts
- record purchase intent only as a conversation message

A full LLM buyer agent is a later milestone.

### Merchant CLI

The first version can be CLI-only. It must allow merchants to create/edit shop profiles, manage products and stock, configure delivery rules, configure automation boundaries, start the resident merchant agent, and view conversations requiring human review.

### CLI UX Contract

The CLI is both a human tool and an adapter boundary, so its behavior must stay predictable:

- Global `--help` prints top-level commands; nested help such as `merchant create --help` prints that subcommand's options.
- `--format json` emits exactly one JSON value for one-shot commands and one JSON object per line for streaming chat/daemon log events.
- Text output may be concise, but JSON output is the compatibility surface for tests, host adapters, and demos.
- `--db` and legacy `--data` resolve to the same database path behavior; command-specific `--db` aliases must not shadow the global path unexpectedly.
- Errors should exit non-zero for CLI use while preserving machine-readable JSON only where a command explicitly promises JSON output.

### Optional Host Adapters

OpenClaw and Hermes remain optional adapters. They should call the same marketplace API and CLI commands as standalone `mai-cli`; they should not own core business state.

## Internal Agent Protocol

### Agent

```text
id
type: merchant | buyer_cli
owner_id
status: online | away | human_required
capabilities
last_seen_at
```

### Conversation

```text
id
buyer_id
merchant_id
sku optional
status: open | waiting_buyer | waiting_merchant | human_required | closed
messages
created_at
updated_at
```

### Message

```text
sender: buyer | buyer_cli | merchant_agent | merchant | operator
intent: ask_product | ask_stock | ask_delivery | ask_price | negotiate | purchase_intent | support
text
structured_payload optional
created_at
```

### Permissions

- Public buyers can search and create buyer messages.
- Merchant agents require merchant-scoped tokens.
- Merchant agents can reply to consultations and mark conversations as `human_required`.
- Merchant humans are required for private discounts, binding commitments, inventory reservation, order confirmation, payment evidence, refunds, and disputes.
- Operators can moderate products and suspicious conversations.

## Deferred Order Model

Order drafts are not in the MVP. Use these lighter concepts first:

```text
inquiry -> quote_request -> purchase_intent
```

Rules for MVP:

- `purchase_intent` is only a message/intent in a conversation.
- No stock is reserved.
- No order status exists.
- No payment state is recorded.
- The agent must explicitly say that purchase, payment, delivery success, refund, and escrow are outside the current version.

Later order milestone:

```text
draft -> confirmed -> payment_pending -> paid_external -> fulfilled -> completed
```

Only add this after the consultation network is reliable.

## Technical Stack

Use Python first because the existing implementation is already Python.

Recommended MVP stack:

```text
Python 3.11+
FastAPI for Marketplace API
Typer or argparse for CLI entry points
SQLite for local development and demos
Pydantic for typed schemas
Small polling worker loop for merchant agents
```

Defer until after MVP: Postgres, SQLAlchemy migrations, APScheduler, hosted deployment, payment integrations, and full LLM provider abstraction.

## Project Shape

```text
mai-cli/
  pyproject.toml
  README.md
  docs/
    architecture.md
    agent-protocol.md
    migration-from-mai.md
  mai_cli/
    cli.py
    config.py
    core/
      catalog.py
      conversations.py
      risk.py
      delivery.py
    api/
      app.py
      routes_merchants.py
      routes_marketplace.py
      routes_conversations.py
      routes_agents.py
    agents/
      merchant_agent.py
      buyer_cli.py
      tools.py
    db/
      models.py
      session.py
    adapters/
      mai_legacy.py
      openclaw.py
      hermes.py
  tests/
    test_merchant_agent.py
    test_marketplace_flow.py
    test_conversations.py
```

## Migration From Existing Mai

Move or adapt current pieces instead of rewriting business rules:

- `search_products`, `product_summary`, and merchant rating logic into `core/catalog.py`.
- Registry risk scoring and moderation into `core/risk.py`.
- Message and conversation concepts into `core/conversations.py`.
- Resident merchant-agent polling into `agents/merchant_agent.py`.
- Existing CLI smoke workflows into end-to-end tests.

Upgrade JSON file storage to SQLite, the dependency-free HTTP server to FastAPI, API-key-only auth to user/merchant/agent tokens, and host-specific prompts to reusable optional adapters.

## MVP Features

### Merchant

- Create merchant profile.
- Create and update products.
- Adjust inventory attributes.
- Configure delivery area, fee, and estimated delivery time.
- Configure automation boundaries.
- Start merchant agent runtime.
- View conversations requiring human review.

### Buyer

- Express demand through CLI text.
- Search nearby merchants and products.
- Compare one to three candidates.
- Ask merchant agents about stock, delivery, price, and substitutions.
- Record purchase intent as conversation context only.

### Marketplace

- Merchant and product CRUD.
- Product and merchant search.
- Complete conversation and message APIs.
- Agent heartbeat APIs with stale-agent detection.
- Human-review queue, flag, and resolution APIs.
- Basic risk and moderation flags.

## Explicit Non-Goals For MVP

- Order creation, confirmation, or inventory reservation.
- Real payment charging, payment status recording, or fund custody.
- Automatic refunds or disputes.
- Courier or rider dispatch.
- Full LLM autonomous buyer/merchant agents.
- Web console, native mobile apps, advertising, bidding, or large-scale ranking.
- Full KYC/KYB and tax handling.

## Acceptance Criteria

- `mai-cli` can run without OpenClaw or Hermes.
- Marketplace API can run locally with SQLite.
- FastAPI and fallback ASGI modes pass the same auth/error contract tests.
- A merchant can create a shop, product, inventory attributes, and delivery rule.
- A merchant agent can run as an independent process.
- Duplicate merchant-agent daemons cannot produce duplicate replies for the same buyer message.
- A buyer can ask for a nearby product through CLI text.
- Buyer CLI can search the marketplace and contact a merchant agent.
- Merchant agent can answer stock, price, and delivery questions from structured shop data.
- Buyer CLI can summarize options and missing facts.
- Risky, unsupported, bargaining, or unclear situations are marked for human review.
- Nested command help works for every public subcommand.
- No order, payment, refund, escrow, or delivery-success claim is made in MVP.
- Existing Mai data can be imported through a legacy adapter.

## First Implementation Milestone

Build the smallest runnable vertical slice:

1. Project scaffold and CLI entry point.
2. SQLite-backed models for merchants, products, delivery rules, conversations, messages, agents, and moderation flags.
3. FastAPI marketplace service.
4. Deterministic merchant agent tools for catalog, inventory, delivery, and human-review flags.
5. Complete conversations, heartbeat, and human-review API surfaces with matching CLI commands.
6. MVP multi-agent orchestration harness for conversation assignment, `next_actor`, idempotent message processing, retry/error tracking, and audit events.
7. Long-running merchant agent daemon with `start`, `stop`, `status`, `logs`, and `run --once` lifecycle commands.
8. Simple buyer CLI tools for search, merchant conversation, and response summary.
9. End-to-end demo test for the Hangzhou Longjing gift box consultation scenario, including conversation APIs, heartbeat, human review, harness routing, and daemon start/status/stop.

Do not implement order drafts, payment evidence, hosted deployment, or full LLM autonomy until the local consultation vertical slice and agent daemon lifecycle are reliable.

## Next Development Roadmap

After the first vertical slice, development should proceed in small, shippable increments. Each stage must preserve the MVP boundary: consultation first, no transaction commitments unless explicitly introduced in a later milestone.

### Stage 1: Complete local platform primitives

- Implement full conversation CRUD, message append, close, and list filters.
- Add heartbeat read/list APIs and stale-agent detection.
- Add human-review queue, flag, resolve, and merchant review CLI.
- Add append-only audit events for conversation status changes, agent replies, human-review creation, and human-review resolution.
- Add tests for idempotent message processing, concurrent claim attempts, and duplicate agent starts.
- Add parity tests that exercise auth failures and malformed payloads through both FastAPI and fallback ASGI paths.
- Add CLI contract tests for nested help, JSON output shape, and `--db`/`--data` path resolution.

### Stage 2: Agent daemon lifecycle

- Implement `mai-cli agent start/stop/status/logs`.
- Add pid files, structured logs, graceful SIGTERM/SIGINT handling, and stale pid cleanup.
- Persist daemon counters: checked conversations, replies sent, human-review flags, last error.
- Provide launchd/systemd template generation after local lifecycle commands are reliable.

### Stage 3: Harness routing and reliability

- Add `next_actor` to conversations.
- Add idempotency keys per processed buyer message.
- Add retry policy for transient agent failures.
- Add dead-letter/human-review fallback for repeated failures.
- Add audit log entries for routing decisions.

### Stage 4: Better buyer-side experience

- Add `mai-cli buyer chat` as a lightweight REPL.
- Improve deterministic demand parsing for city, area, category, quantity, time, budget, and delivery constraints.
- Let buyer CLI compare multiple merchant replies in one summary.
- Add conversation history export for debugging and demos.

### Stage 5: LLM provider and tool protocol

- Add provider abstraction for OpenAI-compatible APIs first.
- Define typed tool schemas for catalog search, conversation send, summarize, human-review flag, and merchant reply.
- Add prompt templates for buyer assistant and merchant assistant.
- Add guardrails: no payment claims, no binding merchant commitment, no private rule leakage.
- Add token/time budgets, retries, and deterministic fallback when the model is unavailable.

### Stage 6: Optional OpenClaw/Hermes adapters

- Keep adapters thin: they should call `mai-cli` CLI/API rather than own business state.
- Map OpenClaw tools to SQLite-backed `mai-cli` commands.
- Add Hermes skill instructions for buyer/merchant workflows.
- Add adapter tests that prove OpenClaw/Hermes can share the same marketplace database/API.

### Stage 7: Hosted-readiness, not production payments

- Add Postgres support only after SQLite local flows are stable.
- Add migration scripts and data export/import.
- Add scoped buyer, merchant, agent, and operator tokens.
- Add rate limits, request logs, and moderation dashboards/API.
- Keep real payment, escrow, refund, and courier dispatch out of scope until a separate transaction design is approved.

### Stage 8: Later transaction milestone

- Introduce `order_draft` only after consultation, harness, human review, and daemon lifecycle are reliable.
- Require merchant human confirmation before inventory reservation.
- Record payment evidence only through external PSP references.
- Add transaction-specific safety, compliance, and dispute design before implementation.

## Immediate Next Build Candidates

If choosing the next concrete engineering task, prefer this order:

1. Make merchant-agent message claims atomic and add a concurrent-claim regression test.
2. Add FastAPI/fallback ASGI parity tests for auth failures, malformed payloads, and route metadata.
3. Fix nested CLI help and add CLI contract tests for all public subcommands.
4. Harden daemon retry semantics: failed, abandoned, and processed message states should have explicit transitions.
5. Expand buyer `chat` history and summary coverage for multi-turn and human-review flows.
6. Add LLM provider abstraction and typed tools only after the deterministic runtime contract is stable.
