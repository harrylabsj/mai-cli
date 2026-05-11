import unittest

from mai_cli.agents import merchant_agent


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
