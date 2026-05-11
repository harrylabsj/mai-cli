# Marketplace API

The old JSON registry has been replaced by the SQLite-backed marketplace API.

Inspect route metadata:

```bash
python3 scripts/mai.py --db ./mai-cli.sqlite api routes --format json
```

Serve locally after installing API dependencies:

```bash
pip install -e '.[api]'
python3 scripts/mai.py --db ./mai-cli.sqlite api serve --host 127.0.0.1 --port 8765
```

MVP routes:

- `GET /health`
- `GET /merchants`
- `POST /merchants`
- `GET /merchants/{merchant_id}`
- `PATCH /merchants/{merchant_id}`
- `POST /products`
- `GET /products/{sku}`
- `PATCH /products/{sku}`
- `GET /search/products`
- `GET /search/merchants`
- `POST /buyer/ask`
- `POST /conversations`
- `GET /conversations/{conversation_id}`
- `GET /buyers/{buyer_id}/conversations`
- `GET /merchants/{merchant_id}/conversations`
- `POST /conversations/{conversation_id}/messages`
- `POST /conversations/{conversation_id}/close`
- `POST /agents/heartbeat`
- `GET /agents`
- `GET /agents/{agent_id}`
- `GET /merchants/{merchant_id}/agents`
- `GET /human-review/queue`
- `GET /merchants/{merchant_id}/human-review`
- `POST /conversations/{conversation_id}/human-review`
- `POST /conversations/{conversation_id}/human-review/resolve`

The API is the trusted state boundary for consultation data. Merchant agents should use API/CLI operations instead of writing SQLite directly.

## Local Merchant Tokens

In local/demo mode, `POST /merchants` returns a `merchant_token`. Merchant-scoped write operations require that token either as `merchant_token` in the JSON body or as `Authorization: Bearer <token>`.

Token-protected operations include product writes, merchant profile updates, merchant/merchant-agent conversation replies, agent heartbeats, and human-review create/resolve actions. Public buyer conversation creation and buyer messages remain tokenless for local demos.
