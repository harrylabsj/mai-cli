# OpenClaw Hermes API E2E Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first reliable OpenClaw merchant + Hermes buyer demo that exercises the `mai-cli` Marketplace API boundary instead of sharing SQLite directly.

**Architecture:** Keep OpenClaw and Hermes as thin host adapters. Hermes creates the buyer consultation through API request descriptors, OpenClaw runs the merchant agent through `HTTPMerchantAgentTools`, and `mai-cli` remains the only owner of merchant, product, conversation, message, token, and audit state. Tests run in-process against `create_app()` so they are deterministic and do not require real OpenClaw, Hermes, uvicorn, or network ports.

**Tech Stack:** Python 3 standard library, `unittest`, `mai_cli.api.app.create_app`, fallback ASGI request helpers, `HTTPMerchantAgentTools`, existing adapter modules.

---

## Current Baseline

Existing coverage:
- `tests/test_host_adapter_e2e.py` proves OpenClaw and Hermes command builders can share one local SQLite database through CLI commands.
- `mai_cli.adapters.openclaw` exposes local CLI command builders for merchant creation, product creation, and merchant agent runs.
- `mai_cli.adapters.hermes` exposes local CLI command builders for buyer ask, buyer summarize, and buyer intent.
- `HTTPMerchantAgentTools` already lets the resident merchant agent mutate trusted state through the Marketplace API.

Remaining gap:
- There is no first-party E2E test proving Hermes buyer actions and OpenClaw merchant-agent processing can complete through the Marketplace API boundary.
- Adapter helpers do not yet model API request descriptors as first-class host-facing contracts.
- The demo does not yet prove host identity/session metadata and API ownership checks are preserved.

## File Structure

- Modify `mai_cli/adapters/hermes.py`
  - Add API request descriptor helpers for Hermes buyer actions.
- Modify `mai_cli/adapters/openclaw.py`
  - Add API-backed merchant-agent command helper options or a request descriptor for OpenClaw agent identity metadata.
- Create `tests/test_host_adapter_api_e2e.py`
  - Add an in-process ASGI client and the API-backed Hermes buyer + OpenClaw merchant-agent E2E flow.
- Modify `tests/test_project_shape.py`
  - Lock the new adapter entrypoints as stable public helper APIs.
- Modify `docs/superpowers/specs/2026-05-09-mai-cli-design.md`
  - Mark this API-backed E2E as the first slice under the OpenClaw/Hermes roadmap after it lands.

## Task 1: Add Hermes API Request Descriptors

**Files:**
- Modify: `mai_cli/adapters/hermes.py`
- Test: `tests/test_project_shape.py`

- [x] **Step 1: Write the failing test**

Add this assertion block to `ProjectShapeTest.test_config_and_host_adapters_expose_stable_entrypoints` after the existing `buyer_command` assertions:

```python
            buyer_request = hermes.buyer_ask_request(
                "alice",
                "longjing gift",
                city="Hangzhou",
                area="West Lake",
                session_id="hermes-session-1",
            )
            self.assertEqual(buyer_request["method"], "POST")
            self.assertEqual(buyer_request["path"], "/buyer/ask")
            self.assertEqual(
                buyer_request["payload"],
                {
                    "buyer_id": "alice",
                    "text": "longjing gift",
                    "city": "Hangzhou",
                    "area": "West Lake",
                    "source_id": "hermes-buyer:alice",
                    "host": "hermes",
                    "session_id": "hermes-session-1",
                },
            )
```

- [x] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m unittest discover -s tests -p test_project_shape.py -k config_and_host_adapters
```

Expected: FAIL with `AttributeError: module 'mai_cli.adapters.hermes' has no attribute 'buyer_ask_request'`.

- [x] **Step 3: Add the minimal implementation**

Add this function to `mai_cli/adapters/hermes.py` below `buyer_ask_command`:

```python
def buyer_ask_request(
    buyer_id: str,
    text: str,
    city: str = "",
    area: str = "",
    session_id: str = "",
) -> dict:
    return {
        "method": "POST",
        "path": "/buyer/ask",
        "payload": {
            "buyer_id": buyer_id,
            "text": text,
            "city": city,
            "area": area,
            "source_id": f"hermes-buyer:{buyer_id}",
            "host": "hermes",
            "session_id": session_id,
        },
    }
```

Add `"buyer_ask_request"` to `__all__`.

- [x] **Step 4: Run test to verify it passes**

Run:

```bash
python3 -m unittest discover -s tests -p test_project_shape.py -k config_and_host_adapters
```

Expected: `OK`.

- [x] **Step 5: Commit**

```bash
git add mai_cli/adapters/hermes.py tests/test_project_shape.py
git commit -m "Add Hermes API request descriptor"
```

## Task 2: Add OpenClaw API-Backed Agent Command and Context Metadata

**Files:**
- Modify: `mai_cli/adapters/openclaw.py`
- Test: `tests/test_project_shape.py`

- [x] **Step 1: Write the failing test**

Add this assertion block to `ProjectShapeTest.test_config_and_host_adapters_expose_stable_entrypoints` after the existing `merchant_command` assertions:

```python
            api_agent_command = openclaw.merchant_agent_command(
                "seller-a",
                api_url="http://mai.test",
                agent_token="agent-token",
                once=True,
            )
            self.assertIn("--api-url", api_agent_command)
            self.assertIn("http://mai.test", api_agent_command)
            self.assertIn("--agent-token", api_agent_command)
            self.assertIn("agent-token", api_agent_command)
            self.assertNotIn("--db", api_agent_command)

            agent_context = openclaw.merchant_agent_context("seller-a", session_id="openclaw-session-1")
            self.assertEqual(
                agent_context,
                {
                    "host": "openclaw",
                    "session_id": "openclaw-session-1",
                    "actor": "mai-cli-merchant-agent:seller-a",
                    "source_id": "openclaw-merchant:seller-a:openclaw-session-1",
                    "token_scope": "merchant_agent",
                },
            )
```

- [x] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m unittest discover -s tests -p test_project_shape.py -k config_and_host_adapters
```

Expected: FAIL with `TypeError: merchant_agent_command() got an unexpected keyword argument 'api_url'`.

- [x] **Step 3: Add the minimal implementation**

Change the `merchant_agent_command` signature in `mai_cli/adapters/openclaw.py` to:

```python
def merchant_agent_command(
    merchant_id: str,
    db_path: str | Path | None = None,
    project_root: str | Path | None = None,
    once: bool = False,
    interval: float | None = None,
    api_url: str = "",
    agent_token: str = "",
) -> list[str]:
```

Inside the function, after the existing `interval` handling, add:

```python
    if api_url:
        args.extend(["--api-url", api_url])
    if agent_token:
        args.extend(["--agent-token", agent_token])
```

If `api_url` is provided, call `build_mai_command(args, project_root=project_root)` without `db_path`; otherwise keep the existing `db_path` behavior. This prevents API-backed demos from depending on a local SQLite path in the host adapter.

Add this helper below `merchant_agent_command`:

```python
def merchant_agent_context(merchant_id: str, session_id: str = "") -> dict:
    return {
        "host": "openclaw",
        "session_id": session_id,
        "actor": f"mai-cli-merchant-agent:{merchant_id}",
        "source_id": f"openclaw-merchant:{merchant_id}:{session_id}" if session_id else f"openclaw-merchant:{merchant_id}",
        "token_scope": "merchant_agent",
    }
```

Add `"merchant_agent_context"` to `__all__`.

- [x] **Step 4: Run test to verify it passes**

Run:

```bash
python3 -m unittest discover -s tests -p test_project_shape.py -k config_and_host_adapters
```

Expected: `OK`.

- [x] **Step 5: Commit**

```bash
git add mai_cli/adapters/openclaw.py tests/test_project_shape.py
git commit -m "Add OpenClaw API agent command metadata"
```

## Task 3: Add API-Backed Host Adapter E2E Test

**Files:**
- Create: `tests/test_host_adapter_api_e2e.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_host_adapter_api_e2e.py` with this structure:

```python
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlparse

from mai_cli.adapters import hermes
from mai_cli.agents import merchant_agent
from mai_cli.agents.tools import HTTPMerchantAgentTools
from mai_cli.api.app import create_app


class Response:
    def __init__(self, body):
        self.body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.body


class HostAdapterAPIE2ETest(unittest.TestCase):
    async def asgi_request(self, app, method, path, payload=None, query_string="", headers=None):
        body = json.dumps(payload or {}).encode("utf-8") if payload is not None else b""
        sent = []
        received = False
        request_headers = [(b"content-type", b"application/json")]
        for key, value in (headers or {}).items():
            request_headers.append((str(key).lower().encode("latin1"), str(value).encode("latin1")))

        async def receive():
            nonlocal received
            if received:
                return {"type": "http.disconnect"}
            received = True
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            sent.append(message)

        await app(
            {
                "type": "http",
                "method": method,
                "path": path,
                "query_string": query_string.encode("utf-8"),
                "headers": request_headers,
            },
            receive,
            send,
        )
        status = next(message["status"] for message in sent if message["type"] == "http.response.start")
        raw = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
        return status, json.loads(raw.decode("utf-8") or "{}")

    def request(self, app, method, path, payload=None, query_string="", headers=None):
        return asyncio.run(self.asgi_request(app, method, path, payload, query_string, headers))

    def opener_for(self, app):
        def opener(request, timeout=10):
            parsed = urlparse(request.full_url)
            payload = json.loads((request.data or b"{}").decode("utf-8")) if request.data else None
            headers = {key: value for key, value in request.header_items()}
            status, body = self.request(
                app,
                request.get_method(),
                parsed.path,
                payload=payload,
                query_string=parsed.query,
                headers=headers,
            )
            if status >= 400:
                raise AssertionError(f"unexpected API status {status}: {body}")
            return Response(body)

        return opener

    def test_openclaw_merchant_and_hermes_buyer_complete_consultation_through_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            app = create_app(db_file)

            status, merchant = self.request(
                app,
                "POST",
                "/merchants",
                {
                    "id": "seller-a",
                    "name": "West Lake Tea",
                    "city": "Hangzhou",
                    "service_area": "West Lake",
                    "delivery_eta_minutes": 45,
                },
            )
            self.assertEqual(status, 200)
            merchant_token = merchant["merchant_token"]

            status, product = self.request(
                app,
                "POST",
                "/products",
                {
                    "merchant_id": "seller-a",
                    "merchant_token": merchant_token,
                    "sku": "tea-a",
                    "title": "Longjing Gift Box",
                    "price": 88,
                    "stock": 5,
                    "tags": ["longjing", "gift"],
                },
            )
            self.assertEqual(status, 200)
            self.assertEqual(product["product"]["sku"], "tea-a")

            status, issued = self.request(
                app,
                "POST",
                "/agents/tokens",
                {"merchant_id": "seller-a", "merchant_token": merchant_token, "ttl_seconds": 86400},
            )
            self.assertEqual(status, 200)
            agent_token = issued["agent_token"]

            buyer_request = hermes.buyer_ask_request(
                "alice",
                "longjing gift delivery today",
                city="Hangzhou",
                area="West Lake",
                session_id="hermes-session-1",
            )
            status, ask = self.request(app, buyer_request["method"], buyer_request["path"], buyer_request["payload"])
            self.assertEqual(status, 200)
            self.assertEqual(ask["conversation"]["id"], "CONV-0001")
            self.assertEqual(ask["conversation"]["status"], "waiting_merchant")
            buyer_token = ask["buyer_token"]

            tools = HTTPMerchantAgentTools(
                "http://mai.test",
                "seller-a",
                agent_token,
                opener=self.opener_for(app),
            )
            result = merchant_agent.process_once_with_tools(tools, "seller-a")
            self.assertEqual(result["replied"][0]["conversation_id"], "CONV-0001")

            status, summary = self.request(
                app,
                "GET",
                "/conversations/CONV-0001",
                headers={"authorization": f"Bearer {buyer_token}"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(summary["conversation"]["status"], "waiting_buyer")
            self.assertTrue(summary["conversation"]["messages"][-1]["structured_payload"]["source_id"].startswith("mai-cli-merchant-agent:"))
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m unittest discover -s tests -p test_host_adapter_api_e2e.py
```

Expected before Tasks 1 and 2 are complete: FAIL because `hermes.buyer_ask_request` is missing. Expected after Tasks 1 and 2 but before API payload support is complete: FAIL on any missing host/session preservation assertion.

- [ ] **Step 3: Implement only the missing behavior**

If `_buyer_ask()` ignores `source_id`, `host`, or `session_id`, update `mai_cli/api/app.py` and `mai_cli/agents/buyer_cli.py` so the initial buyer message structured payload preserves:

```python
{
    "source_id": payload.get("source_id") or "buyer-cli",
    "host": payload.get("host") or "",
    "session_id": payload.get("session_id") or "",
}
```

Keep the existing tokenless buyer creation behavior and returned `buyer_token` unchanged.

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
python3 -m unittest discover -s tests -p test_host_adapter_api_e2e.py
```

Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add mai_cli/api/app.py mai_cli/agents/buyer_cli.py tests/test_host_adapter_api_e2e.py
git commit -m "Add API-backed OpenClaw Hermes E2E test"
```

## Task 4: Add Host Metadata Audit Coverage

**Files:**
- Modify: `mai_cli/agents/tools.py`
- Modify: `mai_cli/api/app.py`
- Test: `tests/test_host_adapter_api_e2e.py`

- [ ] **Step 1: Extend the failing E2E test**

Append this assertion to `test_openclaw_merchant_and_hermes_buyer_complete_consultation_through_api`:

```python
            status, merchant_summary = self.request(
                app,
                "GET",
                "/conversations/CONV-0001",
                headers={"authorization": f"Bearer {merchant_token}"},
            )
            self.assertEqual(status, 200)
            tool_events = [
                event
                for event in merchant_summary["conversation"]["audit_events"]
                if event["event"] == "llm_tool_call"
            ]
            self.assertTrue(
                any(
                    event["details"].get("host") == "openclaw"
                    and event["details"].get("session_id") == "openclaw-session-1"
                    for event in audit["events"]
                )
            )
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m unittest discover -s tests -p test_host_adapter_api_e2e.py
```

Expected: FAIL because HTTP merchant-agent tool calls do not yet record host/session audit metadata.

- [ ] **Step 3: Add minimal audit metadata plumbing**

Add optional `host` and `session_id` parameters to `HTTPMerchantAgentTools.__init__`:

```python
host: str = "",
session_id: str = "",
```

Store them as `self.host` and `self.session_id`. After each successful mutating request in `heartbeat`, `append_message`, `add_flag`, `claim_message`, `complete_message`, `fail_message`, `abandon_message`, and `abandon_stale_messages`, call `/audit/tool-calls` with:

```python
{
    "merchant_id": self.merchant_id,
    "merchant_token": self.merchant_token,
    "host": self.host or "openclaw",
    "session_id": self.session_id,
    "actor": f"mai-cli-merchant-agent:{self.merchant_id}",
    "token_scope": "merchant_agent",
    "tool": tool_name,
    "status": "ok",
}
```

Include `"conversation_id": conversation_id` for conversation-specific tools such as `append_message`, `add_flag`, `claim_message`, `complete_message`, `fail_message`, `abandon_message`, and `abandon_stale_messages` whenever a conversation id is available. For request failures, record the same shape with `"status": "error"` and `"error": str(exc)` before re-raising when the API is reachable enough to accept the audit call.

- [ ] **Step 4: Pass host metadata from the E2E test**

Change the tools construction in `tests/test_host_adapter_api_e2e.py` to:

```python
            tools = HTTPMerchantAgentTools(
                "http://mai.test",
                "seller-a",
                agent_token,
                opener=self.opener_for(app),
                host="openclaw",
                session_id="openclaw-session-1",
            )
```

- [ ] **Step 5: Run test to verify it passes**

Run:

```bash
python3 -m unittest discover -s tests -p test_host_adapter_api_e2e.py
```

Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
git add mai_cli/agents/tools.py mai_cli/api/app.py tests/test_host_adapter_api_e2e.py
git commit -m "Audit host metadata for API-backed agent tools"
```

## Task 5: Documentation and Full Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-05-09-mai-cli-design.md`

- [ ] **Step 1: Document the API-backed demo**

Add this paragraph to the Host Adapter Diagnostics section of `README.md`:

```markdown
The API-backed host adapter E2E test proves the intended production boundary: Hermes creates the buyer consultation through the Marketplace API, OpenClaw runs the merchant agent through API-backed tools, and `mai-cli` owns all commerce state, tokens, conversation routing, and audit events.
```

- [ ] **Step 2: Mark the roadmap slice as implemented**

In `docs/superpowers/specs/2026-05-09-mai-cli-design.md`, under "Current OpenClaw/Hermes improvement plan", add:

```markdown
   - First implementation slice: add an API-backed E2E test where Hermes buyer request descriptors and OpenClaw API-backed merchant-agent tools complete a consultation through `create_app()` without sharing SQLite directly.
```

- [ ] **Step 3: Run focused tests**

Run:

```bash
python3 -m unittest discover -s tests -p test_project_shape.py
python3 -m unittest discover -s tests -p test_host_adapter_e2e.py
python3 -m unittest discover -s tests -p test_host_adapter_api_e2e.py
```

Expected: all tests pass.

- [ ] **Step 4: Run full verification**

Run:

```bash
bash scripts/verify.sh
git diff --check
```

Expected: `verification ok` and no `git diff --check` output.

- [ ] **Step 5: Commit docs**

```bash
git add README.md docs/superpowers/specs/2026-05-09-mai-cli-design.md
git commit -m "Document API-backed host adapter demo"
```

## Self-Review Checklist

- Spec coverage: This plan covers the first current OpenClaw/Hermes improvement item, the host-native permission mapping foundation, and the audit proof needed before real host binaries are involved.
- Existing baseline respected: It keeps `tests/test_host_adapter_e2e.py` as the local SQLite adapter test and adds a separate API-backed test rather than weakening current coverage.
- MVP boundary preserved: No order, payment, stock reservation, fulfillment, refund, escrow, or courier behavior is introduced.
- Testability: Every task has a focused failing test, a passing check, and a commit boundary.
- Host state boundary: OpenClaw and Hermes adapters remain request/command builders; trusted commerce state remains in `mai-cli`.
