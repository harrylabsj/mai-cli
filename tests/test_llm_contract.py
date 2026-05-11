import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mai_cli.core.catalog import create_merchant, create_product
from mai_cli.core.conversations import append_message, conversation_summary, ensure_conversation
from mai_cli.db.session import db_session
from mai_cli.llm.dispatcher import MarketplaceToolDispatcher, dispatch_marketplace_tool
from mai_cli.llm.prompts import buyer_system_prompt, merchant_system_prompt
from mai_cli.llm.providers import OpenAICompatibleProvider, provider_from_env
from mai_cli.llm.tools import marketplace_tool_schemas


class LlmContractTest(unittest.TestCase):
    def seed_consultation(self, db_file: Path) -> None:
        with db_session(db_file) as conn:
            create_merchant(
                conn,
                merchant_id="seller-a",
                name="West Lake Tea",
                city="Hangzhou",
                service_area="West Lake",
                delivery_eta_minutes=45,
            )
            create_product(
                conn,
                merchant_id="seller-a",
                sku="tea-a",
                title="Longjing Gift Box",
                price=88,
                stock=5,
                tags=["longjing", "gift"],
            )
            conversation = ensure_conversation(conn, "alice", "seller-a", "tea-a")
            append_message(conn, conversation["id"], "buyer", "ask_delivery", "Can this deliver today?")

    def test_marketplace_tool_schemas_are_openai_function_tools(self):
        tools = marketplace_tool_schemas()
        names = [tool["function"]["name"] for tool in tools]

        self.assertEqual(
            names,
            [
                "catalog_search",
                "conversation_send",
                "conversation_summarize",
                "human_review_flag",
                "merchant_reply",
            ],
        )
        self.assertNotIn("create_order", names)
        self.assertNotIn("charge_payment", names)
        for tool in tools:
            self.assertEqual(tool["type"], "function")
            parameters = tool["function"]["parameters"]
            self.assertEqual(parameters["type"], "object")
            self.assertFalse(parameters["additionalProperties"])

    def test_openai_compatible_provider_builds_payload_with_tools(self):
        calls = []

        def fake_transport(url, headers, payload, timeout):
            calls.append({"url": url, "headers": headers, "payload": payload, "timeout": timeout})
            return {"choices": [{"message": {"content": "consultation reply"}}]}

        provider = OpenAICompatibleProvider(
            base_url="https://llm.example/v1/",
            api_key="secret-token",
            model="mai-test-model",
            timeout=12,
            max_tokens=512,
            transport=fake_transport,
        )

        response = provider.complete(
            [
                {"role": "system", "content": "Stay inside MVP consultation boundaries."},
                {"role": "user", "content": "Can this merchant deliver today?"},
            ],
            tools=marketplace_tool_schemas(),
        )

        self.assertEqual(response.content, "consultation reply")
        self.assertEqual(calls[0]["url"], "https://llm.example/v1/chat/completions")
        self.assertEqual(calls[0]["headers"]["authorization"], "Bearer secret-token")
        self.assertEqual(calls[0]["timeout"], 12)
        payload = calls[0]["payload"]
        self.assertEqual(payload["model"], "mai-test-model")
        self.assertEqual(payload["max_tokens"], 512)
        self.assertEqual(payload["temperature"], 0)
        self.assertEqual(payload["messages"][1]["content"], "Can this merchant deliver today?")
        self.assertEqual(payload["tools"][0]["function"]["name"], "catalog_search")
        self.assertNotIn("secret-token", str(payload))

    def test_provider_from_env_reads_openai_compatible_settings(self):
        env = {
            "MAI_LLM_BASE_URL": "https://llm.example/custom",
            "MAI_LLM_API_KEY": "env-token",
            "MAI_LLM_MODEL": "env-model",
            "MAI_LLM_TIMEOUT_SECONDS": "9",
            "MAI_LLM_MAX_TOKENS": "2048",
        }
        with patch.dict(os.environ, env, clear=False):
            provider = provider_from_env(transport=lambda *_args: {"choices": [{"message": {"content": "ok"}}]})

        self.assertEqual(provider.base_url, "https://llm.example/custom")
        self.assertEqual(provider.api_key, "env-token")
        self.assertEqual(provider.model, "env-model")
        self.assertEqual(provider.timeout, 9)
        self.assertEqual(provider.max_tokens, 2048)

    def test_system_prompts_include_mvp_guardrails(self):
        buyer_prompt = buyer_system_prompt()
        merchant_prompt = merchant_system_prompt("Catalog and delivery only.")
        combined = f"{buyer_prompt}\n{merchant_prompt}".lower()

        self.assertIn("consultation only", combined)
        self.assertIn("do not create orders", combined)
        self.assertIn("do not reserve stock", combined)
        self.assertIn("do not charge", combined)
        self.assertIn("refund", combined)
        self.assertIn("human review", combined)
        self.assertIn("catalog and delivery only", merchant_prompt.lower())

    def test_marketplace_tool_dispatcher_executes_catalog_conversation_and_summary_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            self.seed_consultation(db_file)
            dispatcher = MarketplaceToolDispatcher(db_file, source_id="llm-test")

            catalog = dispatcher.dispatch("catalog_search", {"query": "longjing", "city": "Hangzhou"})
            self.assertEqual(catalog["tool"], "catalog_search")
            self.assertEqual(catalog["result"]["results"][0]["sku"], "tea-a")

            sent = dispatcher.dispatch(
                "conversation_send",
                {
                    "conversation_id": "CONV-0001",
                    "sender": "buyer_cli",
                    "intent": "ask_stock",
                    "text": "How many are available?",
                },
            )
            self.assertEqual(sent["result"]["message"]["sender"], "buyer_cli")
            self.assertEqual(sent["result"]["conversation"]["status"], "waiting_merchant")
            self.assertEqual(sent["result"]["message"]["structured_payload"]["source_id"], "llm-test")

            summary = dispatcher.dispatch("conversation_summarize", {"conversation_id": "CONV-0001"})
            self.assertEqual(summary["result"]["summary"]["conversation"]["id"], "CONV-0001")
            self.assertTrue(summary["result"]["summary"]["no_order_created"])

    def test_marketplace_tool_dispatcher_handles_human_review_and_merchant_reply(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            self.seed_consultation(db_file)

            review = dispatch_marketplace_tool(
                db_file,
                "human_review_flag",
                {"conversation_id": "CONV-0001", "reason": "bargaining", "severity": "review"},
                source_id="llm-merchant",
            )
            self.assertEqual(review["result"]["conversation"]["status"], "human_required")
            self.assertEqual(review["result"]["review"]["reason"], "bargaining")

            reply = dispatch_marketplace_tool(
                db_file,
                "merchant_reply",
                {
                    "conversation_id": "CONV-0001",
                    "intent": "ask_delivery",
                    "text": "A merchant human must confirm this request.",
                    "human_required": True,
                    "reason": "low_stock",
                },
                source_id="llm-merchant",
            )
            self.assertEqual(reply["result"]["message"]["sender"], "merchant_agent")
            self.assertEqual(reply["result"]["conversation"]["status"], "human_required")
            self.assertTrue(any(flag["reason"] == "low_stock" for flag in reply["result"]["conversation"]["flags"]))

    def test_marketplace_tool_dispatcher_rejects_unknown_or_disallowed_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            self.seed_consultation(db_file)
            with self.assertRaises(SystemExit):
                dispatch_marketplace_tool(db_file, "create_order", {"conversation_id": "CONV-0001"})
            with self.assertRaises(SystemExit):
                dispatch_marketplace_tool(
                    db_file,
                    "conversation_send",
                    {
                        "conversation_id": "CONV-0001",
                        "sender": "merchant_agent",
                        "intent": "ask_stock",
                        "text": "Not allowed through buyer send tool.",
                    },
                )

            with db_session(db_file) as conn:
                conversation = conversation_summary(conn, "CONV-0001")
            self.assertEqual([message["sender"] for message in conversation["messages"]], ["buyer"])

    def test_marketplace_tool_dispatcher_enforces_scope_and_audits_tool_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            self.seed_consultation(db_file)

            buyer_dispatcher = MarketplaceToolDispatcher(
                db_file,
                source_id="hermes-buyer",
                host="hermes",
                session_id="sess-buyer",
                actor="alice",
                token_scope="buyer",
            )
            sent = buyer_dispatcher.dispatch(
                "conversation_send",
                {
                    "conversation_id": "CONV-0001",
                    "sender": "buyer",
                    "intent": "ask_stock",
                    "text": "Any stock left?",
                },
            )
            self.assertEqual(sent["result"]["message"]["sender"], "buyer")

            with self.assertRaises(SystemExit):
                buyer_dispatcher.dispatch(
                    "merchant_reply",
                    {
                        "conversation_id": "CONV-0001",
                        "intent": "ask_stock",
                        "text": "Buyer scope should not reply as merchant.",
                    },
                )

            merchant_dispatcher = MarketplaceToolDispatcher(
                db_file,
                source_id="openclaw-merchant",
                host="openclaw",
                session_id="sess-merchant",
                actor="seller-a",
                token_scope="merchant_agent",
            )
            reply = merchant_dispatcher.dispatch(
                "merchant_reply",
                {
                    "conversation_id": "CONV-0001",
                    "intent": "ask_stock",
                    "text": "Stock is 5.",
                },
            )
            self.assertEqual(reply["result"]["conversation"]["status"], "waiting_buyer")

            with db_session(db_file) as conn:
                events = conversation_summary(conn, "CONV-0001")["audit_events"]
            tool_events = [event for event in events if event["event"] == "llm_tool_call"]
            self.assertEqual([event["details"]["status"] for event in tool_events], ["ok", "denied", "ok"])
            self.assertEqual(tool_events[0]["details"]["host"], "hermes")
            self.assertEqual(tool_events[0]["details"]["session_id"], "sess-buyer")
            self.assertEqual(tool_events[0]["details"]["actor"], "alice")
            self.assertEqual(tool_events[0]["details"]["token_scope"], "buyer")
            self.assertEqual(tool_events[1]["details"]["tool"], "merchant_reply")
            self.assertIn("not allowed", tool_events[1]["details"]["error"])
            self.assertEqual(tool_events[2]["details"]["host"], "openclaw")
            self.assertEqual(tool_events[2]["details"]["token_scope"], "merchant_agent")

    def test_marketplace_tool_dispatcher_rejects_cross_merchant_conversation_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            self.seed_consultation(db_file)
            with db_session(db_file) as conn:
                create_merchant(
                    conn,
                    merchant_id="seller-b",
                    name="River Tea",
                    city="Hangzhou",
                    service_area="West Lake",
                    delivery_eta_minutes=30,
                )

            dispatcher = MarketplaceToolDispatcher(
                db_file,
                source_id="openclaw-merchant-b",
                host="openclaw",
                session_id="sess-merchant-b",
                actor="seller-b",
                token_scope="merchant_agent",
            )

            with self.assertRaises(SystemExit):
                dispatcher.dispatch(
                    "merchant_reply",
                    {
                        "conversation_id": "CONV-0001",
                        "intent": "ask_stock",
                        "text": "seller-b must not reply to seller-a conversations.",
                    },
                )
            with self.assertRaises(SystemExit):
                dispatcher.dispatch(
                    "human_review_flag",
                    {"conversation_id": "CONV-0001", "reason": "cross_merchant", "severity": "review"},
                )
            with self.assertRaises(SystemExit):
                dispatcher.dispatch("conversation_summarize", {"conversation_id": "CONV-0001"})

            with db_session(db_file) as conn:
                conversation = conversation_summary(conn, "CONV-0001")
            self.assertEqual([message["sender"] for message in conversation["messages"]], ["buyer"])
            self.assertEqual(conversation["flags"], [])

    def test_marketplace_tool_dispatcher_rejects_cross_buyer_conversation_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            self.seed_consultation(db_file)
            with db_session(db_file) as conn:
                ensure_conversation(conn, "bob", "seller-a", "tea-a")

            dispatcher = MarketplaceToolDispatcher(
                db_file,
                source_id="hermes-buyer-bob",
                host="hermes",
                session_id="sess-buyer-bob",
                actor="bob",
                token_scope="buyer",
            )

            with self.assertRaises(SystemExit):
                dispatcher.dispatch(
                    "conversation_send",
                    {
                        "conversation_id": "CONV-0001",
                        "sender": "buyer",
                        "intent": "ask_stock",
                        "text": "bob must not write to alice conversations.",
                    },
                )
            with self.assertRaises(SystemExit):
                dispatcher.dispatch("conversation_summarize", {"conversation_id": "CONV-0001"})

            with db_session(db_file) as conn:
                conversation = conversation_summary(conn, "CONV-0001")
            self.assertEqual([message["sender"] for message in conversation["messages"]], ["buyer"])


if __name__ == "__main__":
    unittest.main()
