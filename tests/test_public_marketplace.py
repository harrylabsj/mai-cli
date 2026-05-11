import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mai_cli.api.app import _list_agents
from mai_cli.api.app import create_app
from mai_cli.db.session import db_session


class PublicMarketplaceTest(unittest.TestCase):
    async def asgi_request(self, app, method, path, payload=None, query_string=""):
        body = json.dumps(payload or {}).encode("utf-8") if payload is not None else b""
        received = False
        sent = []

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
                "headers": [(b"content-type", b"application/json")],
            },
            receive,
            send,
        )
        status = next(message["status"] for message in sent if message["type"] == "http.response.start")
        response_body = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
        return status, json.loads(response_body.decode("utf-8") or "{}")

    def request(self, app, method, path, payload=None, query_string=""):
        return asyncio.run(self.asgi_request(app, method, path, payload=payload, query_string=query_string))

    def test_api_factory_exposes_consultation_routes_and_initializes_sqlite(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "marketplace.sqlite"
            app = create_app(db_file)
            route_paths = {route.path for route in getattr(app, "routes", []) if hasattr(route, "path")}
            self.assertIn("/health", route_paths)
            self.assertIn("/merchants", route_paths)
            self.assertIn("/merchants/{merchant_id}", route_paths)
            self.assertIn("/products", route_paths)
            self.assertIn("/products/{sku}", route_paths)
            self.assertIn("/search/products", route_paths)
            self.assertIn("/search/merchants", route_paths)
            self.assertIn("/buyer/ask", route_paths)
            self.assertIn("/conversations", route_paths)
            self.assertIn("/conversations/{conversation_id}", route_paths)
            self.assertIn("/conversations/{conversation_id}/messages", route_paths)
            self.assertIn("/conversations/{conversation_id}/close", route_paths)
            self.assertIn("/buyers/{buyer_id}/conversations", route_paths)
            self.assertIn("/agents/heartbeat", route_paths)
            self.assertIn("/agents", route_paths)
            self.assertIn("/agents/{agent_id}", route_paths)
            self.assertIn("/merchants/{merchant_id}/agents", route_paths)
            self.assertIn("/human-review/queue", route_paths)
            self.assertIn("/merchants/{merchant_id}/conversations", route_paths)
            self.assertIn("/merchants/{merchant_id}/human-review", route_paths)
            self.assertIn("/conversations/{conversation_id}/human-review", route_paths)
            self.assertIn("/conversations/{conversation_id}/human-review/resolve", route_paths)

            with db_session(db_file):
                pass
            conn = sqlite3.connect(db_file)
            try:
                tables = {row[0] for row in conn.execute("select name from sqlite_master where type = 'table'")}
            finally:
                conn.close()
            self.assertIn("conversations", tables)
            self.assertIn("messages", tables)
            self.assertIn("agents", tables)
            self.assertIn("moderation_flags", tables)
            self.assertNotIn("payments", tables)

    def test_fallback_asgi_api_runs_marketplace_consultation_flow_with_sqlite(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "marketplace.sqlite"
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
                    "contact": "wechat:westlake",
                    "delivery_eta_minutes": 45,
                    "tags": ["tea", "gift"],
                },
            )
            self.assertEqual(status, 200)
            self.assertEqual(merchant["merchant"]["id"], "seller-a")
            merchant_token = merchant["merchant_token"]

            status, product = self.request(
                app,
                "POST",
                "/products",
                {
                    "merchant_id": "seller-a",
                    "sku": "tea-a",
                    "title": "Longjing Gift Box",
                    "price": 88,
                    "stock": 5,
                    "tags": ["longjing", "gift"],
                    "merchant_token": merchant_token,
                },
            )
            self.assertEqual(status, 200)
            self.assertEqual(product["product"]["sku"], "tea-a")

            status, merchants = self.request(app, "GET", "/search/merchants", query_string="query=west&city=Hangzhou")
            self.assertEqual(status, 200)
            self.assertEqual(merchants["results"][0]["id"], "seller-a")

            status, ask = self.request(
                app,
                "POST",
                "/buyer/ask",
                {"buyer_id": "alice", "text": "longjing gift delivery today", "city": "Hangzhou"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(ask["conversation"]["id"], "CONV-0001")

            status, heartbeat = self.request(
                app,
                "POST",
                "/agents/heartbeat",
                {"merchant_id": "seller-a", "status": "online"},
            )
            self.assertEqual(status, 403)
            status, heartbeat = self.request(
                app,
                "POST",
                "/agents/heartbeat",
                {"merchant_id": "seller-a", "status": "online", "merchant_token": merchant_token},
            )
            self.assertEqual(status, 200)
            self.assertEqual(heartbeat["agent"]["status"], "online")

            status, conversation = self.request(app, "GET", "/conversations/CONV-0001")
            self.assertEqual(status, 200)
            self.assertEqual(conversation["conversation"]["status"], "waiting_merchant")

            status, message = self.request(
                app,
                "POST",
                "/conversations/CONV-0001/messages",
                {
                    "sender": "merchant_agent",
                    "intent": "ask_delivery",
                    "text": "Stock is 5 and delivery ETA is 45 minutes.",
                    "status": "waiting_buyer",
                },
            )
            self.assertEqual(status, 403)
            status, message = self.request(
                app,
                "POST",
                "/conversations/CONV-0001/messages",
                {
                    "sender": "merchant_agent",
                    "intent": "ask_delivery",
                    "text": "Stock is 5 and delivery ETA is 45 minutes.",
                    "status": "waiting_buyer",
                    "merchant_token": merchant_token,
                },
            )
            self.assertEqual(status, 200)
            self.assertEqual(message["message"]["sender"], "merchant_agent")

            status, conversations = self.request(
                app,
                "GET",
                "/merchants/seller-a/conversations",
                query_string="status=waiting_buyer",
            )
            self.assertEqual(status, 200)
            self.assertEqual(conversations["conversations"][0]["id"], "CONV-0001")

            status, update = self.request(app, "PATCH", "/products/tea-a", {"merchant_id": "seller-a", "stock": 4})
            self.assertEqual(status, 403)
            status, update = self.request(
                app,
                "PATCH",
                "/products/tea-a",
                {"merchant_id": "seller-a", "stock": 4, "merchant_token": merchant_token},
            )
            self.assertEqual(status, 200)
            self.assertEqual(update["product"]["stock"], 4)

    def test_api_exposes_conversation_agent_and_human_review_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "marketplace.sqlite"
            app = create_app(db_file)
            _, merchant = self.request(app, "POST", "/merchants", {"id": "seller-a", "name": "West Lake Tea"})
            merchant_token = merchant["merchant_token"]
            self.request(
                app,
                "POST",
                "/products",
                {
                    "merchant_id": "seller-a",
                    "sku": "tea-a",
                    "title": "Longjing",
                    "price": 88,
                    "stock": 5,
                    "merchant_token": merchant_token,
                },
            )

            status, created = self.request(
                app,
                "POST",
                "/conversations",
                {
                    "buyer_id": "alice",
                    "merchant_id": "seller-a",
                    "sku": "tea-a",
                    "intent": "ask_stock",
                    "text": "Is this in stock?",
                    "source_id": "buyer-cli-test",
                },
            )
            self.assertEqual(status, 200)
            self.assertEqual(created["conversation"]["status"], "waiting_merchant")
            self.assertEqual(created["conversation"]["messages"][0]["structured_payload"]["source_id"], "buyer-cli-test")

            status, buyer_conversations = self.request(
                app,
                "GET",
                "/buyers/alice/conversations",
                query_string="status=waiting_merchant&sku=tea-a",
            )
            self.assertEqual(status, 200)
            self.assertEqual(buyer_conversations["conversations"][0]["id"], "CONV-0001")

            status, agent = self.request(
                app,
                "POST",
                "/agents/heartbeat",
                {
                    "merchant_id": "seller-a",
                    "status": "online",
                    "pid": 1234,
                    "version": "2.0.0",
                    "checked_count": 2,
                    "replied_count": 1,
                    "last_error": "",
                    "merchant_token": merchant_token,
                },
            )
            self.assertEqual(status, 200)
            self.assertEqual(agent["agent"]["checked_count"], 2)
            agent_id = agent["agent"]["id"]

            status, agent_list = self.request(app, "GET", "/agents")
            self.assertEqual(status, 200)
            self.assertEqual(agent_list["agents"][0]["id"], agent_id)

            status, agent_detail = self.request(app, "GET", f"/agents/{agent_id}")
            self.assertEqual(status, 200)
            self.assertEqual(agent_detail["agent"]["replied_count"], 1)

            status, merchant_agents = self.request(app, "GET", "/merchants/seller-a/agents")
            self.assertEqual(status, 200)
            self.assertEqual(merchant_agents["agents"][0]["id"], agent_id)

            conn = sqlite3.connect(db_file)
            try:
                conn.execute("update agents set last_seen_at = '2000-01-01T00:00:00' where id = ?", (agent_id,))
                conn.commit()
            finally:
                conn.close()
            status, stale_agent = self.request(app, "GET", f"/agents/{agent_id}")
            self.assertEqual(status, 200)
            self.assertTrue(stale_agent["agent"]["stale"])

            status, review = self.request(
                app,
                "POST",
                "/conversations/CONV-0001/human-review",
                {
                    "reason": "unclear_delivery",
                    "severity": "review",
                    "source_id": "agent-test",
                    "merchant_token": merchant_token,
                },
            )
            self.assertEqual(status, 200)
            self.assertEqual(review["conversation"]["status"], "human_required")
            self.assertEqual(review["review"]["reason"], "unclear_delivery")
            self.assertIsNone(review["review"]["resolved_at"])

            status, queue = self.request(app, "GET", "/human-review/queue")
            self.assertEqual(status, 200)
            self.assertEqual(queue["reviews"][0]["conversation_id"], "CONV-0001")
            self.assertEqual(queue["reviews"][0]["merchant_id"], "seller-a")
            self.assertEqual(queue["reviews"][0]["buyer_id"], "alice")

            status, resolved = self.request(
                app,
                "POST",
                "/conversations/CONV-0001/human-review/resolve",
                {
                    "action": "reply",
                    "text": "Human confirmed delivery details.",
                    "sender": "merchant",
                    "merchant_token": merchant_token,
                },
            )
            self.assertEqual(status, 200)
            self.assertEqual(resolved["conversation"]["status"], "waiting_buyer")
            self.assertIsNotNone(resolved["reviews"][0]["resolved_at"])

            status, closed = self.request(
                app,
                "POST",
                "/conversations/CONV-0001/close",
                {"sender": "operator", "text": "Closed after confirmation."},
            )
            self.assertEqual(status, 200)
            self.assertEqual(closed["conversation"]["status"], "closed")

    def test_agent_stale_ttl_is_configurable(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "marketplace.sqlite"
            app = create_app(db_file)
            _, merchant = self.request(app, "POST", "/merchants", {"id": "seller-a", "name": "West Lake Tea"})
            merchant_token = merchant["merchant_token"]
            self.request(
                app,
                "POST",
                "/agents/heartbeat",
                {"merchant_id": "seller-a", "status": "online", "merchant_token": merchant_token},
            )
            with db_session(db_file) as conn:
                conn.execute(
                    "update agents set last_seen_at = '2000-01-01T00:00:00' where id = 'mai-cli-merchant-agent:seller-a'"
                )

            with patch.dict("os.environ", {"MAI_AGENT_STALE_TTL_SECONDS": "9999999999"}):
                agents = _list_agents(db_file)

            self.assertFalse(agents["agents"][0]["stale"])
            self.assertEqual(agents["agents"][0]["stale_ttl_seconds"], 9999999999)


if __name__ == "__main__":
    unittest.main()
