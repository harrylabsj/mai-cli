import tempfile
import unittest
import urllib.error
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from mai_cli.agents import merchant_agent
from mai_cli.agents.tools import record_heartbeat
from mai_cli.core.catalog import create_merchant
from mai_cli.db.session import db_session


class FakeHTTPResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        import json

        return json.dumps(self.payload).encode("utf-8")


class CapturingHTTPOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout=0):
        import json

        body = None
        if request.data:
            body = json.loads(request.data.decode("utf-8"))
        self.requests.append({"request": request, "timeout": timeout, "body": body})
        return FakeHTTPResponse(self.responses.pop(0))


class FakeMarketplaceTools:
    def __init__(self):
        self.calls = []
        self.messages = []
        self.processes = []
        self.failures = []

    def heartbeat(self, merchant_id, status="online", **kwargs):
        self.calls.append(("heartbeat", merchant_id, status, kwargs))
        return {
            "id": f"mai-cli-merchant-agent:{merchant_id}",
            "type": "merchant",
            "owner_id": merchant_id,
            "status": status,
            "capabilities": ["catalog", "inventory", "delivery", "consultation"],
            "last_seen_at": "2026-05-10T00:00:00",
            **kwargs,
        }

    def waiting_merchant_conversations(self, merchant_id):
        self.calls.append(("waiting_merchant_conversations", merchant_id))
        return [
            {
                "id": "CONV-0001",
                "merchant_id": merchant_id,
                "sku": "tea-a",
                "messages": [
                    {
                        "id": 1,
                        "sender": "buyer",
                        "intent": "ask_delivery",
                        "text": "Can longjing ship today?",
                    }
                ],
            }
        ]

    def product_summary(self, sku):
        self.calls.append(("product_summary", sku))
        return {
            "sku": sku,
            "title": "Longjing Gift Box",
            "price": 88.0,
            "currency": "CNY",
            "stock": 5,
            "delivery": {"service_area": "West Lake", "eta_minutes": 45, "fee": 12.0, "currency": "CNY"},
        }

    def append_message(self, conversation_id, sender, intent, text, structured_payload, status):
        self.calls.append(("append_message", conversation_id, sender, status))
        message = {
            "id": 2,
            "conversation_id": conversation_id,
            "sender": sender,
            "intent": intent,
            "text": text,
            "structured_payload": structured_payload,
        }
        self.messages.append(message)
        return message

    def add_flag(self, conversation_id, reason, sku=""):
        self.calls.append(("add_flag", conversation_id, reason, sku))
        return {"id": 1, "conversation_id": conversation_id, "reason": reason, "sku": sku}

    def claim_message(self, agent_id, conversation_id, message_id, idempotency_key):
        self.calls.append(("claim_message", agent_id, conversation_id, message_id, idempotency_key))
        return {"claimed": True, "attempts": 1, "idempotency_key": idempotency_key}

    def complete_message(self, agent_id, message_id):
        self.calls.append(("complete_message", agent_id, message_id))
        self.processes.append((agent_id, message_id))
        return {"status": "processed"}

    def fail_message(self, agent_id, message_id, error):
        self.calls.append(("fail_message", agent_id, message_id, error))
        self.failures.append((agent_id, message_id, error))
        return {"status": "failed", "last_error": error}


class FailingMarketplaceTools(FakeMarketplaceTools):
    def product_summary(self, sku):
        self.calls.append(("product_summary", sku))
        raise RuntimeError("temporary catalog failure")


class AgentToolsBoundaryTest(unittest.TestCase):
    def test_record_heartbeat_rejects_fractional_runtime_counters(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            with db_session(db_file) as conn:
                create_merchant(conn, "seller-a", "West Lake Tea")

                with self.assertRaises(ValueError) as checked_error:
                    record_heartbeat(conn, "seller-a", checked_count=1.5)
                self.assertIn("checked_count must be a whole number", str(checked_error.exception))

                with self.assertRaises(ValueError) as replied_error:
                    record_heartbeat(conn, "seller-a", replied_count=1.5)
                self.assertIn("replied_count must be a whole number", str(replied_error.exception))

                with self.assertRaises(ValueError) as pid_error:
                    record_heartbeat(conn, "seller-a", pid=1.5)
                self.assertIn("pid must be a whole number", str(pid_error.exception))

    def test_http_merchant_agent_tools_call_marketplace_api_contract(self):
        from mai_cli.agents.tools import HTTPMerchantAgentTools

        opener = CapturingHTTPOpener(
            [
                {
                    "ok": True,
                    "agent": {
                        "id": "mai-cli-merchant-agent:seller-a",
                        "owner_id": "seller-a",
                        "status": "online",
                    },
                },
                {"ok": True, "conversations": [{"id": "CONV-0001"}]},
                {"ok": True, "claim": {"claimed": True, "attempts": 1}},
                {"ok": True, "message": {"id": 2, "sender": "merchant_agent"}},
            ]
        )
        tools = HTTPMerchantAgentTools(
            "http://127.0.0.1:8765/",
            merchant_id="seller-a",
            merchant_token="tok_seller_a",
            opener=opener,
            timeout=12,
        )

        agent = tools.heartbeat("seller-a", checked_count=1)
        conversations = tools.waiting_merchant_conversations("seller-a")
        claim = tools.claim_message("mai-cli-merchant-agent:seller-a", "CONV-0001", 1, "claim-key")
        message = tools.append_message(
            "CONV-0001",
            "merchant_agent",
            "ask_delivery",
            "Stock is 5.",
            structured_payload={"source_id": "mai-cli-merchant-agent:seller-a"},
            status="waiting_buyer",
        )

        self.assertEqual(agent["status"], "online")
        self.assertEqual(conversations, [{"id": "CONV-0001"}])
        self.assertTrue(claim["claimed"])
        self.assertEqual(message["id"], 2)
        self.assertEqual(opener.requests[0]["request"].full_url, "http://127.0.0.1:8765/agents/heartbeat")
        self.assertEqual(opener.requests[0]["body"]["merchant_id"], "seller-a")
        self.assertEqual(opener.requests[0]["body"]["merchant_token"], "tok_seller_a")
        self.assertEqual(opener.requests[0]["request"].get_header("Authorization"), "Bearer tok_seller_a")
        parsed = urlparse(opener.requests[1]["request"].full_url)
        self.assertEqual(parsed.path, "/merchants/seller-a/conversations")
        self.assertEqual(parse_qs(parsed.query), {"status": ["waiting_merchant"]})
        self.assertEqual(opener.requests[2]["request"].full_url, "http://127.0.0.1:8765/agents/messages/claim")
        self.assertEqual(opener.requests[2]["body"]["idempotency_key"], "claim-key")
        self.assertEqual(opener.requests[2]["body"]["merchant_token"], "tok_seller_a")
        self.assertEqual(opener.requests[3]["body"]["status"], "waiting_buyer")
        self.assertEqual(opener.requests[3]["body"]["merchant_token"], "tok_seller_a")

    def test_http_merchant_agent_tools_reject_fractional_agent_numbers_before_request(self):
        from mai_cli.agents.tools import HTTPMerchantAgentTools

        opener = CapturingHTTPOpener([])
        tools = HTTPMerchantAgentTools(
            "http://127.0.0.1:8765/",
            merchant_id="seller-a",
            merchant_token="tok_seller_a",
            opener=opener,
        )

        cases = (
            lambda: tools.heartbeat("seller-a", checked_count=1.5),
            lambda: tools.claim_message("mai-cli-merchant-agent:seller-a", "CONV-0001", 1.5, "claim-key"),
            lambda: tools.complete_message("mai-cli-merchant-agent:seller-a", 1.5),
            lambda: tools.fail_message("mai-cli-merchant-agent:seller-a", 1.5, "failed"),
            lambda: tools.abandon_message("mai-cli-merchant-agent:seller-a", 1.5, "abandoned"),
            lambda: tools.abandon_stale_messages("mai-cli-merchant-agent:seller-a", stale_after_seconds=0.5),
            lambda: tools.abandon_stale_messages("mai-cli-merchant-agent:seller-a", stale_after_seconds=0),
        )
        for call in cases:
            with self.assertRaises(ValueError):
                call()
        self.assertEqual(opener.requests, [])

    def test_http_merchant_agent_tools_wrap_transport_errors(self):
        from mai_cli.agents.tools import HTTPMarketplaceError, HTTPMerchantAgentTools

        def failing_opener(_request, timeout=0):
            raise urllib.error.URLError("connection refused")

        tools = HTTPMerchantAgentTools(
            "http://127.0.0.1:8765",
            merchant_id="seller-a",
            merchant_token="tok_seller_a",
            opener=failing_opener,
        )

        with self.assertRaises(HTTPMarketplaceError) as exc:
            tools.heartbeat("seller-a")
        self.assertIn("Marketplace API request failed", str(exc.exception))
        self.assertIn("connection refused", str(exc.exception))

    def test_process_once_uses_marketplace_tools_without_sqlite_connection(self):
        tools = FakeMarketplaceTools()

        result = merchant_agent.process_once_with_tools(tools, "seller-a")

        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["replied"][0]["conversation_id"], "CONV-0001")
        self.assertFalse(result["replied"][0]["human_required"])
        self.assertIn(("product_summary", "tea-a"), tools.calls)
        self.assertIn(("append_message", "CONV-0001", "merchant_agent", "waiting_buyer"), tools.calls)
        self.assertIn(("complete_message", "mai-cli-merchant-agent:seller-a", 1), tools.calls)
        self.assertEqual(
            tools.messages[0]["structured_payload"]["source_id"],
            "mai-cli-merchant-agent:seller-a",
        )
        self.assertEqual(tools.messages[0]["structured_payload"]["processed_message_id"], 1)
        self.assertEqual(tools.messages[0]["structured_payload"]["idempotency_key"], "mai-cli-merchant-agent:seller-a:1")

    def test_process_once_records_failed_message_for_retry_and_heartbeat_error(self):
        tools = FailingMarketplaceTools()

        result = merchant_agent.process_once_with_tools(tools, "seller-a")

        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["replied"], [])
        self.assertEqual(result["failed"][0]["conversation_id"], "CONV-0001")
        self.assertIn("temporary catalog failure", result["failed"][0]["error"])
        self.assertIn(("fail_message", "mai-cli-merchant-agent:seller-a", 1, "RuntimeError: temporary catalog failure"), tools.calls)
        self.assertTrue(
            any(call[0] == "heartbeat" and call[3].get("last_error") == "RuntimeError: temporary catalog failure" for call in tools.calls)
        )


if __name__ == "__main__":
    unittest.main()
