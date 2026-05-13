import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mai_cli.api.app import AuthError, _list_agents, route_info
from mai_cli.api.app import create_app
from mai_cli.db.session import db_session


class FakeFastAPI:
    def __init__(
        self,
        *,
        title,
        version,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    ):
        self.title = title
        self.version = version
        self.state = SimpleNamespace()
        self.routes = []
        self.exception_handlers = {}
        for path in (openapi_url, docs_url, redoc_url):
            if path is not None:
                self.routes.append(SimpleNamespace(methods={"GET"}, path=path, endpoint=lambda: None))

    def exception_handler(self, exc_type):
        def decorator(func):
            self.exception_handlers[exc_type] = func
            return func

        return decorator

    def get(self, path):
        return self._route("GET", path)

    def post(self, path):
        return self._route("POST", path)

    def patch(self, path):
        return self._route("PATCH", path)

    def _route(self, method, path):
        def decorator(func):
            self.routes.append(SimpleNamespace(methods={method}, path=path, endpoint=func))
            return func

        return decorator


class PublicMarketplaceTest(unittest.TestCase):
    async def asgi_request(self, app, method, path, payload=None, query_string="", headers=None):
        body = json.dumps(payload or {}).encode("utf-8") if payload is not None else b""
        received = False
        sent = []
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
        response_body = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
        return status, json.loads(response_body.decode("utf-8") or "{}")

    def request(self, app, method, path, payload=None, query_string="", headers=None):
        return asyncio.run(
            self.asgi_request(app, method, path, payload=payload, query_string=query_string, headers=headers)
        )

    def fastapi_request(self, app, method, path, *args):
        endpoint = next(
            route.endpoint
            for route in app.routes
            if route.path == path and method in route.methods
        )
        try:
            return 200, endpoint(*args)
        except BaseException as exc:
            for exc_type, handler in app.exception_handlers.items():
                if isinstance(exc, exc_type):
                    response = handler(None, exc)
                    return response.status_code, json.loads(response.body.decode("utf-8"))
            raise

    def route_map(self, app):
        routes = {}
        for route in getattr(app, "routes", []):
            if not hasattr(route, "path"):
                continue
            routes.setdefault(route.path, set()).update(route.methods)
        return routes

    def test_api_factory_exposes_consultation_routes_and_initializes_sqlite(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "marketplace.sqlite"
            app = create_app(db_file)
            routes = [route for route in getattr(app, "routes", []) if hasattr(route, "path")]
            route_paths = {route.path for route in routes}
            self.assertIn("/health", route_paths)
            self.assertIn("/merchants", route_paths)
            self.assertIn("/merchants/{merchant_id}", route_paths)
            self.assertIn("/products", route_paths)
            self.assertIn("/products/{sku}", route_paths)
            self.assertIn("/search/products", route_paths)
            self.assertIn("/search/merchants", route_paths)
            self.assertIn("/channels/messages", route_paths)
            self.assertIn("/buyer/ask", route_paths)
            self.assertIn("/conversations", route_paths)
            self.assertIn("/conversations/{conversation_id}", route_paths)
            self.assertIn("/conversations/{conversation_id}/messages", route_paths)
            self.assertIn("/conversations/{conversation_id}/close", route_paths)
            self.assertIn("/buyers/{buyer_id}/conversations", route_paths)
            self.assertIn("/agents/heartbeat", route_paths)
            self.assertIn("/agents/tokens", route_paths)
            token_route = next(route for route in routes if route.path == "/agents/tokens")
            self.assertEqual(token_route.methods, {"GET", "POST"})
            self.assertIn("/agents/tokens/revoke", route_paths)
            self.assertIn("/agents/tokens/rotate", route_paths)
            self.assertIn("/agents/messages/claim", route_paths)
            self.assertIn("/agents/messages/complete", route_paths)
            self.assertIn("/agents/messages/fail", route_paths)
            self.assertIn("/agents/messages/abandon", route_paths)
            self.assertIn("/agents/messages/abandon-stale", route_paths)
            self.assertIn("/agents", route_paths)
            self.assertIn("/agents/{agent_id}", route_paths)
            self.assertIn("/merchants/{merchant_id}/agents", route_paths)
            self.assertIn("/audit/tool-calls", route_paths)
            self.assertIn("/audit/events", route_paths)
            self.assertIn("/human-review/queue", route_paths)
            self.assertIn("/human-review/{review_id}", route_paths)
            self.assertIn("/human-review/{review_id}/resolve", route_paths)
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

    def test_fastapi_auth_errors_are_mapped_to_403_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "marketplace.sqlite"
            with patch("mai_cli.api.app.FastAPI", FakeFastAPI):
                app = create_app(db_file)

        self.assertTrue(app.state.fastapi_available)
        self.assertIn(AuthError, app.exception_handlers)

        response = app.exception_handlers[AuthError](None, AuthError("merchant token required"))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            json.loads(response.body.decode("utf-8")),
            {"ok": False, "error": "merchant token required"},
        )

        create_product = next(
            route.endpoint
            for route in app.routes
            if route.path == "/products" and "POST" in route.methods
        )
        with self.assertRaises(AuthError):
            create_product({"merchant_id": "seller-a", "name": "Tea", "price_cents": 500})

    def test_fastapi_conversation_reads_require_owner_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "marketplace.sqlite"
            with patch("mai_cli.api.app.FastAPI", FakeFastAPI):
                app = create_app(db_file)

            _, merchant = self.fastapi_request(app, "POST", "/merchants", {"id": "seller-a", "name": "West Lake Tea"})
            merchant_token = merchant["merchant_token"]
            self.fastapi_request(
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
            _, created = self.fastapi_request(
                app,
                "POST",
                "/buyer/ask",
                {"buyer_id": "alice", "text": "longjing"},
            )

            anonymous = self.fastapi_request(app, "GET", "/conversations/{conversation_id}", "CONV-0001")
            self.assertEqual(anonymous[0], 403)
            buyer_view = self.fastapi_request(
                app,
                "GET",
                "/conversations/{conversation_id}",
                "CONV-0001",
                f"Bearer {created['buyer_token']}",
            )
            self.assertEqual(buyer_view[0], 200)
            anonymous_write = self.fastapi_request(
                app,
                "POST",
                "/conversations/{conversation_id}/messages",
                "CONV-0001",
                {"sender": "buyer", "intent": "ask_stock", "text": "Anonymous write should fail."},
                "",
            )
            self.assertEqual(anonymous_write[0], 403)
            buyer_write = self.fastapi_request(
                app,
                "POST",
                "/conversations/{conversation_id}/messages",
                "CONV-0001",
                {"sender": "buyer", "intent": "ask_stock", "text": "Any stock left?"},
                f"Bearer {created['buyer_token']}",
            )
            self.assertEqual(buyer_write[0], 200)
            merchant_view = self.fastapi_request(
                app,
                "GET",
                "/merchants/{merchant_id}/conversations",
                "seller-a",
                "",
                "",
                "",
                "",
                f"Bearer {merchant_token}",
            )
            self.assertEqual(merchant_view[0], 200)

            review = self.fastapi_request(
                app,
                "POST",
                "/conversations/{conversation_id}/human-review",
                "CONV-0001",
                {"reason": "low_confidence"},
                f"Bearer {merchant_token}",
            )
            self.assertEqual(review[0], 200)
            review_id = review[1]["review"]["id"]
            shown = self.fastapi_request(
                app,
                "GET",
                "/human-review/{review_id}",
                review_id,
                f"Bearer {merchant_token}",
            )
            self.assertEqual(shown[0], 200)
            resolved = self.fastapi_request(
                app,
                "POST",
                "/human-review/{review_id}/resolve",
                review_id,
                {"action": "reply", "text": "Human checked this answer."},
                f"Bearer {merchant_token}",
            )
            self.assertEqual(resolved[0], 200)
            self.assertEqual(resolved[1]["conversation"]["status"], "waiting_buyer")

    def test_fastapi_agent_status_reads_require_owner_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "marketplace.sqlite"
            with patch("mai_cli.api.app.FastAPI", FakeFastAPI):
                app = create_app(db_file)

            _, merchant = self.fastapi_request(app, "POST", "/merchants", {"id": "seller-a", "name": "West Lake Tea"})
            merchant_token = merchant["merchant_token"]
            _, heartbeat = self.fastapi_request(
                app,
                "POST",
                "/agents/heartbeat",
                {"merchant_id": "seller-a", "status": "online", "merchant_token": merchant_token},
            )
            agent_id = heartbeat["agent"]["id"]

            anonymous_list = self.fastapi_request(app, "GET", "/agents")
            self.assertEqual(anonymous_list[0], 403)
            owner_list = self.fastapi_request(app, "GET", "/agents", f"Bearer {merchant_token}")
            self.assertEqual(owner_list[0], 200)
            self.assertEqual(owner_list[1]["agents"][0]["id"], agent_id)
            anonymous_detail = self.fastapi_request(app, "GET", "/agents/{agent_id}", agent_id)
            self.assertEqual(anonymous_detail[0], 403)
            owner_detail = self.fastapi_request(app, "GET", "/agents/{agent_id}", agent_id, f"Bearer {merchant_token}")
            self.assertEqual(owner_detail[0], 200)
            merchant_agents = self.fastapi_request(
                app,
                "GET",
                "/merchants/{merchant_id}/agents",
                "seller-a",
                f"Bearer {merchant_token}",
            )
            self.assertEqual(merchant_agents[0], 200)
            self.assertEqual(merchant_agents[1]["agents"][0]["id"], agent_id)

    def test_fastapi_product_search_honors_price_and_stock_filters(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "marketplace.sqlite"
            with patch("mai_cli.api.app.FastAPI", FakeFastAPI):
                app = create_app(db_file)

            _, merchant = self.fastapi_request(app, "POST", "/merchants", {"id": "seller-a", "name": "West Lake Tea"})
            for sku, price, stock in (
                ("tea-cheap", 88, 5),
                ("tea-expensive", 188, 5),
                ("tea-soldout", 66, 0),
            ):
                status, _product = self.fastapi_request(
                    app,
                    "POST",
                    "/products",
                    {
                        "merchant_id": "seller-a",
                        "sku": sku,
                        "title": f"Longjing {sku}",
                        "price": price,
                        "stock": stock,
                        "tags": ["longjing"],
                        "merchant_token": merchant["merchant_token"],
                    },
                )
                self.assertEqual(status, 200)

            under_budget = self.fastapi_request(app, "GET", "/search/products", "longjing", "", "", "100", "")
            self.assertEqual(under_budget[0], 200)
            self.assertEqual([product["sku"] for product in under_budget[1]["results"]], ["tea-cheap"])

            with_sold_out = self.fastapi_request(app, "GET", "/search/products", "longjing", "", "", "100", "true")
            self.assertEqual(with_sold_out[0], 200)
            self.assertEqual(
                {product["sku"] for product in with_sold_out[1]["results"]},
                {"tea-cheap", "tea-soldout"},
            )

    def test_route_metadata_matches_fastapi_and_fallback_apps(self):
        expected = {route.path: set(route.methods) for route in route_info()}
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "marketplace.sqlite"
            with patch("mai_cli.api.app.FastAPI", None):
                fallback_app = create_app(db_file)
            with patch("mai_cli.api.app.FastAPI", FakeFastAPI):
                fastapi_app = create_app(db_file)

        self.assertEqual(self.route_map(fallback_app), expected)
        self.assertEqual(self.route_map(fastapi_app), expected)

    def test_fastapi_and_fallback_error_contracts_match_for_auth_and_bad_payloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            fallback_db = Path(tmp) / "fallback.sqlite"
            fastapi_db = Path(tmp) / "fastapi.sqlite"
            with patch("mai_cli.api.app.FastAPI", None):
                fallback_app = create_app(fallback_db)
            with patch("mai_cli.api.app.FastAPI", FakeFastAPI):
                fastapi_app = create_app(fastapi_db)

            product_without_token = {
                "merchant_id": "seller-a",
                "sku": "tea-a",
                "title": "Longjing Gift Box",
                "price": 88,
                "stock": 5,
            }
            fallback_auth = self.request(fallback_app, "POST", "/products", product_without_token)
            fastapi_auth = self.fastapi_request(fastapi_app, "POST", "/products", product_without_token)
            self.assertEqual(fastapi_auth, fallback_auth)
            self.assertEqual(fallback_auth[0], 403)
            self.assertEqual(fallback_auth[1]["ok"], False)

            merchant_payload = {"id": "seller-a", "name": "West Lake Tea"}
            fallback_merchant = self.request(fallback_app, "POST", "/merchants", merchant_payload)
            fastapi_merchant = self.fastapi_request(fastapi_app, "POST", "/merchants", merchant_payload)
            self.assertEqual(fallback_merchant[0], 200)
            self.assertEqual(fastapi_merchant[0], 200)

            malformed_product = {
                "merchant_id": "seller-a",
                "title": "Longjing Gift Box",
                "price": 88,
                "stock": 5,
                "merchant_token": fallback_merchant[1]["merchant_token"],
            }
            malformed_fastapi_product = dict(malformed_product)
            malformed_fastapi_product["merchant_token"] = fastapi_merchant[1]["merchant_token"]
            fallback_bad = self.request(fallback_app, "POST", "/products", malformed_product)
            fastapi_bad = self.fastapi_request(fastapi_app, "POST", "/products", malformed_fastapi_product)

            self.assertEqual(fastapi_bad, fallback_bad)
            self.assertEqual(fallback_bad[0], 400)
            self.assertEqual(fallback_bad[1], {"ok": False, "error": "'sku'"})
            for db_file in (fallback_db, fastapi_db):
                with db_session(db_file) as conn:
                    count = conn.execute("select count(*) as count from products").fetchone()["count"]
                self.assertEqual(count, 0)

    def test_fastapi_and_fallback_accept_bearer_tokens_for_protected_routes(self):
        with tempfile.TemporaryDirectory() as tmp:
            fallback_db = Path(tmp) / "fallback.sqlite"
            fastapi_db = Path(tmp) / "fastapi.sqlite"
            with patch("mai_cli.api.app.FastAPI", None):
                fallback_app = create_app(fallback_db)
            with patch("mai_cli.api.app.FastAPI", FakeFastAPI):
                fastapi_app = create_app(fastapi_db)

            merchant_payload = {"id": "seller-a", "name": "West Lake Tea"}
            fallback_merchant = self.request(fallback_app, "POST", "/merchants", merchant_payload)
            fastapi_merchant = self.fastapi_request(fastapi_app, "POST", "/merchants", merchant_payload)
            product_payload = {
                "merchant_id": "seller-a",
                "sku": "tea-a",
                "title": "Longjing Gift Box",
                "price": 88,
                "stock": 5,
            }

            fallback_product = self.request(
                fallback_app,
                "POST",
                "/products",
                product_payload,
                headers={"authorization": f"Bearer {fallback_merchant[1]['merchant_token']}"},
            )
            fastapi_product = self.fastapi_request(
                fastapi_app,
                "POST",
                "/products",
                product_payload,
                f"Bearer {fastapi_merchant[1]['merchant_token']}",
            )

            self.assertEqual(fallback_product[0], 200)
            self.assertEqual(fastapi_product[0], 200)
            self.assertEqual(fallback_product[1]["product"]["sku"], "tea-a")
            self.assertEqual(fastapi_product[1]["product"]["sku"], "tea-a")

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

            status, conversation = self.request(
                app,
                "GET",
                "/conversations/CONV-0001",
                headers={"authorization": f"Bearer {merchant_token}"},
            )
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
                headers={"authorization": f"Bearer {merchant_token}"},
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

    def test_append_message_normalizes_human_review_reason_for_routing(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "marketplace.sqlite"
            app = create_app(db_file)

            status, merchant = self.request(app, "POST", "/merchants", {"id": "seller-a", "name": "West Lake Tea"})
            self.assertEqual(status, 200)
            merchant_token = merchant["merchant_token"]
            status, created = self.request(
                app,
                "POST",
                "/conversations",
                {
                    "buyer_id": "alice",
                    "merchant_id": "seller-a",
                    "text": "Can I get a private discount?",
                },
            )
            self.assertEqual(status, 200)
            self.assertEqual(created["conversation"]["id"], "CONV-0001")

            status, message = self.request(
                app,
                "POST",
                "/conversations/CONV-0001/messages",
                {
                    "sender": "merchant",
                    "intent": "support",
                    "text": "This needs an operator review.",
                    "status": "human_required",
                    "structured_payload": {"reason": " suspicious_content "},
                    "merchant_token": merchant_token,
                },
            )

            self.assertEqual(status, 200)
            self.assertEqual(message["message"]["structured_payload"]["reason"], "suspicious_content")
            self.assertEqual(message["conversation"]["status"], "human_required")
            self.assertEqual(message["conversation"]["next_actor"], "operator")

    def test_append_message_rejects_unknown_conversation_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "marketplace.sqlite"
            app = create_app(db_file)

            status, merchant = self.request(app, "POST", "/merchants", {"id": "seller-a", "name": "West Lake Tea"})
            self.assertEqual(status, 200)
            merchant_token = merchant["merchant_token"]
            status, created = self.request(
                app,
                "POST",
                "/conversations",
                {
                    "buyer_id": "alice",
                    "merchant_id": "seller-a",
                    "text": "Can I get a private discount?",
                },
            )
            self.assertEqual(status, 200)
            self.assertEqual(created["conversation"]["status"], "waiting_merchant")

            status, invalid = self.request(
                app,
                "POST",
                "/conversations/CONV-0001/messages",
                {
                    "sender": "merchant",
                    "intent": "support",
                    "text": "Invalid state should not be persisted.",
                    "status": "shipping_now",
                    "merchant_token": merchant_token,
                },
            )

            self.assertEqual(status, 400)
            self.assertIn("Unknown conversation status", invalid["error"])
            status, conversation = self.request(
                app,
                "GET",
                "/conversations/CONV-0001",
                headers={"authorization": f"Bearer {merchant_token}"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(conversation["conversation"]["status"], "waiting_merchant")

    def test_create_conversation_rejects_blank_buyer_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "marketplace.sqlite"
            app = create_app(db_file)

            status, merchant = self.request(app, "POST", "/merchants", {"id": "seller-a", "name": "West Lake Tea"})
            self.assertEqual(status, 200)
            status, created = self.request(
                app,
                "POST",
                "/conversations",
                {
                    "buyer_id": "   ",
                    "merchant_id": "seller-a",
                    "text": "Can I get a private discount?",
                },
            )

            self.assertEqual(status, 400)
            self.assertIn("buyer id is required", created["error"])

    def test_buyer_ask_trims_buyer_id_for_tokens_and_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "marketplace.sqlite"
            app = create_app(db_file)

            status, merchant = self.request(app, "POST", "/merchants", {"id": "seller-a", "name": "West Lake Tea"})
            self.assertEqual(status, 200)
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
                    "tags": ["longjing"],
                    "merchant_token": merchant["merchant_token"],
                },
            )
            self.assertEqual(status, 200)

            status, ask = self.request(
                app,
                "POST",
                "/buyer/ask",
                {"buyer_id": " alice ", "text": "longjing delivery today"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(ask["buyer_id"], "alice")
            self.assertEqual(ask["conversation"]["buyer_id"], "alice")

            status, history = self.request(
                app,
                "GET",
                "/buyers/alice/conversations",
                headers={"authorization": f"Bearer {ask['buyer_token']}"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(history["conversations"][0]["buyer_id"], "alice")

    def test_conversation_reads_and_review_queues_require_owner_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "marketplace.sqlite"
            app = create_app(db_file)

            status, merchant_a = self.request(app, "POST", "/merchants", {"id": "seller-a", "name": "West Lake Tea"})
            self.assertEqual(status, 200)
            status, merchant_b = self.request(app, "POST", "/merchants", {"id": "seller-b", "name": "Other Tea"})
            self.assertEqual(status, 200)
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
                    "tags": ["longjing"],
                    "merchant_token": merchant_a["merchant_token"],
                },
            )
            self.assertEqual(status, 200)
            status, ask = self.request(
                app,
                "POST",
                "/buyer/ask",
                {"buyer_id": "alice", "text": "longjing delivery today"},
            )
            self.assertEqual(status, 200)
            self.assertIn("buyer_token", ask)

            status, anonymous = self.request(app, "GET", "/conversations/CONV-0001")
            self.assertEqual(status, 403)
            status, buyer_view = self.request(
                app,
                "GET",
                "/conversations/CONV-0001",
                headers={"authorization": f"Bearer {ask['buyer_token']}"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(buyer_view["conversation"]["id"], "CONV-0001")
            status, merchant_view = self.request(
                app,
                "GET",
                "/conversations/CONV-0001",
                headers={"authorization": f"Bearer {merchant_a['merchant_token']}"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(merchant_view["conversation"]["merchant_id"], "seller-a")
            status, issued = self.request(
                app,
                "POST",
                "/agents/tokens",
                {"merchant_id": "seller-a", "merchant_token": merchant_a["merchant_token"]},
            )
            self.assertEqual(status, 200)
            status, agent_view = self.request(
                app,
                "GET",
                "/conversations/CONV-0001",
                headers={"authorization": f"Bearer {issued['agent_token']}"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(agent_view["conversation"]["merchant_id"], "seller-a")
            status, cross_merchant = self.request(
                app,
                "GET",
                "/conversations/CONV-0001",
                headers={"authorization": f"Bearer {merchant_b['merchant_token']}"},
            )
            self.assertEqual(status, 403)

            status, second_conversation = self.request(
                app,
                "POST",
                "/conversations",
                {
                    "buyer_id": "alice",
                    "merchant_id": "seller-b",
                    "text": "Second conversation should not unlock the first.",
                },
            )
            self.assertEqual(status, 200)
            status, forged_buyer = self.request(
                app,
                "GET",
                "/conversations/CONV-0001",
                headers={"authorization": f"Bearer {second_conversation['buyer_token']}"},
            )
            self.assertEqual(status, 403)

            status, buyer_list = self.request(
                app,
                "GET",
                "/buyers/alice/conversations",
                headers={"authorization": f"Bearer {ask['buyer_token']}"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(len(buyer_list["conversations"]), 1)
            self.assertEqual(buyer_list["conversations"][0]["id"], "CONV-0001")
            status, merchant_list = self.request(
                app,
                "GET",
                "/merchants/seller-a/conversations",
                headers={"authorization": f"Bearer {merchant_a['merchant_token']}"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(merchant_list["conversations"][0]["id"], "CONV-0001")

            status, review = self.request(
                app,
                "POST",
                "/conversations/CONV-0001/human-review",
                {
                    "reason": "unclear_delivery",
                    "merchant_token": merchant_a["merchant_token"],
                },
            )
            self.assertEqual(status, 200)
            status, anonymous_queue = self.request(app, "GET", "/human-review/queue", query_string="merchant_id=seller-a")
            self.assertEqual(status, 403)
            status, global_queue = self.request(
                app,
                "GET",
                "/human-review/queue",
                headers={"authorization": f"Bearer {merchant_a['merchant_token']}"},
            )
            self.assertEqual(status, 403)
            status, queue = self.request(
                app,
                "GET",
                "/human-review/queue",
                query_string="merchant_id=seller-a",
                headers={"authorization": f"Bearer {merchant_a['merchant_token']}"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(queue["reviews"][0]["conversation_id"], "CONV-0001")
            status, agent_queue = self.request(
                app,
                "GET",
                "/human-review/queue",
                query_string="merchant_id=seller-a",
                headers={"authorization": f"Bearer {issued['agent_token']}"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(agent_queue["reviews"][0]["conversation_id"], "CONV-0001")

    def test_agent_status_reads_require_owner_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "marketplace.sqlite"
            app = create_app(db_file)

            status, merchant_a = self.request(app, "POST", "/merchants", {"id": "seller-a", "name": "West Lake Tea"})
            self.assertEqual(status, 200)
            status, merchant_b = self.request(app, "POST", "/merchants", {"id": "seller-b", "name": "Other Tea"})
            self.assertEqual(status, 200)
            status, heartbeat = self.request(
                app,
                "POST",
                "/agents/heartbeat",
                {
                    "merchant_id": "seller-a",
                    "status": "online",
                    "pid": 1234,
                    "last_error": "last private error",
                    "merchant_token": merchant_a["merchant_token"],
                },
            )
            self.assertEqual(status, 200)
            agent_id = heartbeat["agent"]["id"]
            status, issued = self.request(
                app,
                "POST",
                "/agents/tokens",
                {"merchant_id": "seller-a", "merchant_token": merchant_a["merchant_token"]},
            )
            self.assertEqual(status, 200)

            status, anonymous_list = self.request(app, "GET", "/agents")
            self.assertEqual(status, 403)
            self.assertNotIn("last private error", json.dumps(anonymous_list))
            status, owner_list = self.request(
                app,
                "GET",
                "/agents",
                headers={"authorization": f"Bearer {merchant_a['merchant_token']}"},
            )
            self.assertEqual(status, 200)
            self.assertEqual([agent["id"] for agent in owner_list["agents"]], [agent_id])
            status, other_list = self.request(
                app,
                "GET",
                "/agents",
                headers={"authorization": f"Bearer {merchant_b['merchant_token']}"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(other_list["agents"], [])

            status, anonymous_detail = self.request(app, "GET", f"/agents/{agent_id}")
            self.assertEqual(status, 403)
            status, cross_detail = self.request(
                app,
                "GET",
                f"/agents/{agent_id}",
                headers={"authorization": f"Bearer {merchant_b['merchant_token']}"},
            )
            self.assertEqual(status, 403)
            status, agent_detail = self.request(
                app,
                "GET",
                f"/agents/{agent_id}",
                headers={"authorization": f"Bearer {issued['agent_token']}"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(agent_detail["agent"]["owner_id"], "seller-a")

            status, anonymous_merchant_agents = self.request(app, "GET", "/merchants/seller-a/agents")
            self.assertEqual(status, 403)
            status, cross_merchant_agents = self.request(
                app,
                "GET",
                "/merchants/seller-a/agents",
                headers={"authorization": f"Bearer {merchant_b['merchant_token']}"},
            )
            self.assertEqual(status, 403)
            status, merchant_agents = self.request(
                app,
                "GET",
                "/merchants/seller-a/agents",
                headers={"authorization": f"Bearer {merchant_a['merchant_token']}"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(merchant_agents["agents"][0]["id"], agent_id)

    def test_tool_call_audit_api_requires_owner_token_and_records_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "marketplace.sqlite"
            app = create_app(db_file)

            status, merchant = self.request(app, "POST", "/merchants", {"id": "seller-a", "name": "West Lake Tea"})
            self.assertEqual(status, 200)
            status, created = self.request(
                app,
                "POST",
                "/conversations",
                {"buyer_id": "alice", "merchant_id": "seller-a", "text": "Can I get this delivered today?"},
            )
            self.assertEqual(status, 200)
            status, other = self.request(
                app,
                "POST",
                "/conversations",
                {"buyer_id": "bob", "merchant_id": "seller-a", "text": "Wrong token source."},
            )
            self.assertEqual(status, 200)

            payload = {
                "conversation_id": "CONV-0001",
                "tool": "conversation_send",
                "status": "ok",
                "host": "hermes",
                "session_id": "sess-buyer",
                "actor": "alice",
                "source_id": "hermes-buyer",
                "token_scope": "buyer",
                "error": "",
            }
            status, anonymous = self.request(app, "POST", "/audit/tool-calls", payload)
            self.assertEqual(status, 403)
            status, wrong_buyer = self.request(
                app,
                "POST",
                "/audit/tool-calls",
                {**payload, "buyer_token": other["buyer_token"]},
            )
            self.assertEqual(status, 403)
            status, audited = self.request(
                app,
                "POST",
                "/audit/tool-calls",
                {**payload, "buyer_token": created["buyer_token"]},
            )
            self.assertEqual(status, 200)
            self.assertEqual(audited["event"]["event"], "llm_tool_call")
            self.assertEqual(audited["event"]["details"]["host"], "hermes")
            self.assertEqual(audited["event"]["details"]["token_scope"], "buyer")

            status, conversation = self.request(
                app,
                "GET",
                "/conversations/CONV-0001",
                headers={"authorization": f"Bearer {merchant['merchant_token']}"},
            )
            self.assertEqual(status, 200)
            tool_events = [
                event for event in conversation["conversation"]["audit_events"] if event["event"] == "llm_tool_call"
            ]
            self.assertEqual(tool_events[-1]["details"]["tool"], "conversation_send")
            self.assertEqual(tool_events[-1]["details"]["status"], "ok")

    def test_product_search_api_honors_price_and_stock_filters(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "marketplace.sqlite"
            app = create_app(db_file)

            status, merchant = self.request(app, "POST", "/merchants", {"id": "seller-a", "name": "West Lake Tea"})
            self.assertEqual(status, 200)
            for sku, price, stock in (
                ("tea-cheap", 88, 5),
                ("tea-expensive", 188, 5),
                ("tea-soldout", 66, 0),
            ):
                status, product = self.request(
                    app,
                    "POST",
                    "/products",
                    {
                        "merchant_id": "seller-a",
                        "sku": sku,
                        "title": f"Longjing {sku}",
                        "price": price,
                        "stock": stock,
                        "tags": ["longjing"],
                        "merchant_token": merchant["merchant_token"],
                    },
                )
                self.assertEqual(status, 200)

            status, under_budget = self.request(
                app,
                "GET",
                "/search/products",
                query_string="query=longjing&max_price=100",
            )
            self.assertEqual(status, 200)
            self.assertEqual([product["sku"] for product in under_budget["results"]], ["tea-cheap"])

            status, with_sold_out = self.request(
                app,
                "GET",
                "/search/products",
                query_string="query=longjing&max_price=100&include_out_of_stock=true",
            )
            self.assertEqual(status, 200)
            self.assertEqual({product["sku"] for product in with_sold_out["results"]}, {"tea-cheap", "tea-soldout"})

    def test_api_integer_fields_reject_fractional_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "marketplace.sqlite"
            app = create_app(db_file)

            status, fractional_eta = self.request(
                app,
                "POST",
                "/merchants",
                {
                    "id": "seller-fractional-eta",
                    "name": "West Lake Tea",
                    "delivery_eta_minutes": 12.5,
                },
            )
            self.assertEqual(status, 400)
            self.assertIn("delivery eta minutes must be a whole number", fractional_eta["error"])

            status, merchant = self.request(app, "POST", "/merchants", {"id": "seller-a", "name": "West Lake Tea"})
            self.assertEqual(status, 200)
            status, fractional_eta_update = self.request(
                app,
                "PATCH",
                "/merchants/seller-a",
                {
                    "delivery_eta_minutes": 12.5,
                    "merchant_token": merchant["merchant_token"],
                },
            )
            self.assertEqual(status, 400)
            self.assertIn("delivery eta minutes must be a whole number", fractional_eta_update["error"])

            status, fractional_stock = self.request(
                app,
                "POST",
                "/products",
                {
                    "merchant_id": "seller-a",
                    "sku": "tea-a",
                    "title": "Longjing",
                    "price": 88,
                    "stock": 1.5,
                    "merchant_token": merchant["merchant_token"],
                },
            )
            self.assertEqual(status, 400)
            self.assertIn("--stock must be a whole number", fractional_stock["error"])

            status, product = self.request(
                app,
                "POST",
                "/products",
                {
                    "merchant_id": "seller-a",
                    "sku": "tea-b",
                    "title": "Longjing",
                    "price": 88,
                    "stock": 1,
                    "merchant_token": merchant["merchant_token"],
                },
            )
            self.assertEqual(status, 200)
            status, fractional_stock_update = self.request(
                app,
                "PATCH",
                "/products/tea-b",
                {"merchant_id": "seller-a", "stock": 2.5, "merchant_token": merchant["merchant_token"]},
            )
            self.assertEqual(status, 400)
            self.assertIn("--stock must be a whole number", fractional_stock_update["error"])

    def test_api_numeric_float_fields_reject_boolean_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "marketplace.sqlite"
            app = create_app(db_file)

            status, boolean_fee = self.request(
                app,
                "POST",
                "/merchants",
                {
                    "id": "seller-boolean-fee",
                    "name": "West Lake Tea",
                    "delivery_fee": True,
                },
            )
            self.assertEqual(status, 400)
            self.assertIn("delivery fee must be finite", boolean_fee["error"])

            status, merchant = self.request(app, "POST", "/merchants", {"id": "seller-a", "name": "West Lake Tea"})
            self.assertEqual(status, 200)
            merchant_token = merchant["merchant_token"]

            status, boolean_radius = self.request(
                app,
                "PATCH",
                "/merchants/seller-a",
                {
                    "delivery_radius_km": True,
                    "merchant_token": merchant_token,
                },
            )
            self.assertEqual(status, 400)
            self.assertIn("delivery radius must be finite", boolean_radius["error"])

            status, boolean_price = self.request(
                app,
                "POST",
                "/products",
                {
                    "merchant_id": "seller-a",
                    "sku": "tea-a",
                    "title": "Longjing",
                    "price": True,
                    "stock": 1,
                    "merchant_token": merchant_token,
                },
            )
            self.assertEqual(status, 400)
            self.assertIn("--price must be finite", boolean_price["error"])

            status, product = self.request(
                app,
                "POST",
                "/products",
                {
                    "merchant_id": "seller-a",
                    "sku": "tea-b",
                    "title": "Longjing",
                    "price": 88,
                    "stock": 1,
                    "merchant_token": merchant_token,
                },
            )
            self.assertEqual(status, 200)
            status, boolean_price_update = self.request(
                app,
                "PATCH",
                "/products/tea-b",
                {"merchant_id": "seller-a", "price": True, "merchant_token": merchant_token},
            )
            self.assertEqual(status, 400)
            self.assertIn("--price must be finite", boolean_price_update["error"])

    def test_agent_heartbeat_integer_fields_reject_fractional_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "marketplace.sqlite"
            app = create_app(db_file)

            status, merchant = self.request(app, "POST", "/merchants", {"id": "seller-a", "name": "West Lake Tea"})
            self.assertEqual(status, 200)
            merchant_token = merchant["merchant_token"]

            for field in ("pid", "checked_count", "replied_count"):
                status, response = self.request(
                    app,
                    "POST",
                    "/agents/heartbeat",
                    {"merchant_id": "seller-a", "merchant_token": merchant_token, field: 1.5},
                )
                self.assertEqual(status, 400)
                self.assertIn(f"{field} must be a whole number", response["error"])

                status, response = self.request(
                    app,
                    "POST",
                    "/agents/heartbeat",
                    {"merchant_id": "seller-a", "merchant_token": merchant_token, field: -1},
                )
                self.assertEqual(status, 400)
                self.assertIn(f"{field} must be non-negative", response["error"])

    def test_agent_heartbeat_rejects_unknown_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "marketplace.sqlite"
            app = create_app(db_file)

            status, merchant = self.request(app, "POST", "/merchants", {"id": "seller-a", "name": "West Lake Tea"})
            self.assertEqual(status, 200)
            status, heartbeat = self.request(
                app,
                "POST",
                "/agents/heartbeat",
                {
                    "merchant_id": "seller-a",
                    "status": "sleeping",
                    "merchant_token": merchant["merchant_token"],
                },
            )

            self.assertEqual(status, 400)
            self.assertIn("Unknown agent status", heartbeat["error"])

    def test_agent_heartbeat_rejects_invalid_capabilities(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "marketplace.sqlite"
            app = create_app(db_file)

            status, merchant = self.request(app, "POST", "/merchants", {"id": "seller-a", "name": "West Lake Tea"})
            self.assertEqual(status, 200)
            merchant_token = merchant["merchant_token"]

            for capabilities in ({"catalog": True}, ["catalog", {"bad": True}]):
                status, heartbeat = self.request(
                    app,
                    "POST",
                    "/agents/heartbeat",
                    {
                        "merchant_id": "seller-a",
                        "capabilities": capabilities,
                        "merchant_token": merchant_token,
                    },
                )
                self.assertEqual(status, 400)
                self.assertIn("agent capabilities must be a list of strings", heartbeat["error"])

    def test_conversation_message_and_close_writes_require_owner_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "marketplace.sqlite"
            app = create_app(db_file)

            status, merchant_a = self.request(app, "POST", "/merchants", {"id": "seller-a", "name": "West Lake Tea"})
            self.assertEqual(status, 200)
            status, merchant_b = self.request(app, "POST", "/merchants", {"id": "seller-b", "name": "Other Tea"})
            self.assertEqual(status, 200)
            status, created = self.request(
                app,
                "POST",
                "/conversations",
                {
                    "buyer_id": "alice",
                    "merchant_id": "seller-a",
                    "text": "Can I get this delivered today?",
                },
            )
            self.assertEqual(status, 200)
            buyer_token = created["buyer_token"]
            status, other = self.request(
                app,
                "POST",
                "/conversations",
                {
                    "buyer_id": "bob",
                    "merchant_id": "seller-b",
                    "text": "Wrong buyer token source.",
                },
            )
            self.assertEqual(status, 200)

            status, anonymous_buyer = self.request(
                app,
                "POST",
                "/conversations/CONV-0001/messages",
                {
                    "sender": "buyer",
                    "intent": "ask_delivery",
                    "text": "Anonymous write should fail.",
                },
            )
            self.assertEqual(status, 403)
            status, wrong_buyer = self.request(
                app,
                "POST",
                "/conversations/CONV-0001/messages",
                {
                    "sender": "buyer",
                    "intent": "ask_delivery",
                    "text": "Wrong buyer token should fail.",
                    "buyer_token": other["buyer_token"],
                },
            )
            self.assertEqual(status, 403)
            status, merchant_impersonates_buyer = self.request(
                app,
                "POST",
                "/conversations/CONV-0001/messages",
                {
                    "sender": "buyer",
                    "intent": "ask_delivery",
                    "text": "Merchant token should not impersonate buyer.",
                    "merchant_token": merchant_a["merchant_token"],
                },
            )
            self.assertEqual(status, 403)
            status, buyer_message = self.request(
                app,
                "POST",
                "/conversations/CONV-0001/messages",
                {
                    "sender": "buyer",
                    "intent": "ask_delivery",
                    "text": "Can you confirm delivery?",
                    "buyer_token": buyer_token,
                },
            )
            self.assertEqual(status, 200)
            self.assertEqual(buyer_message["message"]["sender"], "buyer")

            status, anonymous_close = self.request(
                app,
                "POST",
                "/conversations/CONV-0001/close",
                {"sender": "operator", "text": "Anonymous close should fail."},
            )
            self.assertEqual(status, 403)
            status, wrong_close = self.request(
                app,
                "POST",
                "/conversations/CONV-0001/close",
                {
                    "sender": "buyer",
                    "text": "Wrong buyer token should not close.",
                    "buyer_token": other["buyer_token"],
                },
            )
            self.assertEqual(status, 403)
            status, buyer_close = self.request(
                app,
                "POST",
                "/conversations/CONV-0001/close",
                {
                    "sender": "buyer",
                    "text": "Thanks, close this consultation.",
                    "buyer_token": buyer_token,
                },
            )
            self.assertEqual(status, 200)
            self.assertEqual(buyer_close["conversation"]["status"], "closed")

            status, merchant_close_conversation = self.request(
                app,
                "POST",
                "/conversations",
                {
                    "buyer_id": "carol",
                    "merchant_id": "seller-a",
                    "text": "Merchant close target.",
                },
            )
            self.assertEqual(status, 200)
            status, operator_close = self.request(
                app,
                "POST",
                "/conversations/CONV-0003/close",
                {
                    "sender": "operator",
                    "text": "Merchant-authorized operator close.",
                    "merchant_token": merchant_a["merchant_token"],
                },
            )
            self.assertEqual(status, 200)
            self.assertEqual(operator_close["conversation"]["status"], "closed")

    def test_human_review_api_shows_and_resolves_one_review_by_id_with_owner_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "marketplace.sqlite"
            app = create_app(db_file)

            status, merchant_a = self.request(app, "POST", "/merchants", {"id": "seller-a", "name": "West Lake Tea"})
            self.assertEqual(status, 200)
            status, merchant_b = self.request(app, "POST", "/merchants", {"id": "seller-b", "name": "Other Tea"})
            self.assertEqual(status, 200)
            status, created = self.request(
                app,
                "POST",
                "/conversations",
                {
                    "buyer_id": "alice",
                    "merchant_id": "seller-a",
                    "text": "Can I get a private discount?",
                },
            )
            self.assertEqual(status, 200)
            self.assertEqual(created["conversation"]["id"], "CONV-0001")

            status, first = self.request(
                app,
                "POST",
                "/conversations/CONV-0001/human-review",
                {
                    "reason": "low_confidence",
                    "merchant_token": merchant_a["merchant_token"],
                },
            )
            self.assertEqual(status, 200)
            status, second = self.request(
                app,
                "POST",
                "/conversations/CONV-0001/human-review",
                {
                    "reason": "suspicious_content",
                    "merchant_token": merchant_a["merchant_token"],
                },
            )
            self.assertEqual(status, 200)
            first_review_id = first["review"]["id"]
            second_review_id = second["review"]["id"]

            status, anonymous = self.request(app, "GET", f"/human-review/{first_review_id}")
            self.assertEqual(status, 403)
            status, cross_merchant = self.request(
                app,
                "GET",
                f"/human-review/{first_review_id}",
                headers={"authorization": f"Bearer {merchant_b['merchant_token']}"},
            )
            self.assertEqual(status, 403)
            status, shown = self.request(
                app,
                "GET",
                f"/human-review/{first_review_id}",
                headers={"authorization": f"Bearer {merchant_a['merchant_token']}"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(shown["review"]["reason"], "low_confidence")
            self.assertEqual(shown["conversation"]["id"], "CONV-0001")

            status, invalid_action = self.request(
                app,
                "POST",
                f"/human-review/{first_review_id}/resolve",
                {
                    "action": "ship_order",
                    "merchant_token": merchant_a["merchant_token"],
                },
            )
            self.assertEqual(status, 400)
            self.assertIn("Unknown human-review action", invalid_action["error"])

            status, invalid_sender = self.request(
                app,
                "POST",
                f"/human-review/{first_review_id}/resolve",
                {
                    "action": "reply",
                    "sender": "buyer",
                    "text": "Merchant token must not spoof buyer.",
                    "merchant_token": merchant_a["merchant_token"],
                },
            )
            self.assertEqual(status, 400)
            self.assertIn("Unknown human-review sender", invalid_sender["error"])

            status, anonymous_resolve = self.request(
                app,
                "POST",
                f"/human-review/{first_review_id}/resolve",
                {"action": "reply", "text": "No token should fail."},
            )
            self.assertEqual(status, 403)
            status, cross_resolve = self.request(
                app,
                "POST",
                f"/human-review/{first_review_id}/resolve",
                {
                    "action": "reply",
                    "text": "Wrong merchant should fail.",
                    "merchant_token": merchant_b["merchant_token"],
                },
            )
            self.assertEqual(status, 403)
            status, resolved = self.request(
                app,
                "POST",
                f"/human-review/{first_review_id}/resolve",
                {
                    "action": "reply",
                    "sender": "merchant",
                    "text": "Human checked the low-confidence answer.",
                    "merchant_token": merchant_a["merchant_token"],
                },
            )
            self.assertEqual(status, 200)
            self.assertIsNotNone(resolved["review"]["resolved_at"])
            self.assertEqual(resolved["conversation"]["status"], "human_required")
            self.assertEqual(resolved["conversation"]["next_actor"], "operator")

            status, queue = self.request(
                app,
                "GET",
                "/human-review/queue",
                query_string="merchant_id=seller-a",
                headers={"authorization": f"Bearer {merchant_a['merchant_token']}"},
            )
            self.assertEqual(status, 200)
            self.assertEqual([review["id"] for review in queue["reviews"]], [second_review_id])

            status, final = self.request(
                app,
                "POST",
                f"/human-review/{second_review_id}/resolve",
                {
                    "action": "reply",
                    "sender": "merchant",
                    "text": "Human checked the suspicious content review.",
                    "merchant_token": merchant_a["merchant_token"],
                },
            )
            self.assertEqual(status, 200)
            self.assertEqual(final["conversation"]["status"], "waiting_buyer")
            self.assertEqual(final["conversation"]["messages"][-1]["structured_payload"]["review_id"], second_review_id)

    def test_human_review_api_normalizes_blank_reason_and_severity(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "marketplace.sqlite"
            app = create_app(db_file)

            status, merchant = self.request(app, "POST", "/merchants", {"id": "seller-a", "name": "West Lake Tea"})
            self.assertEqual(status, 200)
            status, created = self.request(
                app,
                "POST",
                "/conversations",
                {
                    "buyer_id": "alice",
                    "merchant_id": "seller-a",
                    "text": "Can I get a private discount?",
                },
            )
            self.assertEqual(status, 200)
            self.assertEqual(created["conversation"]["id"], "CONV-0001")

            status, review = self.request(
                app,
                "POST",
                "/conversations/CONV-0001/human-review",
                {
                    "reason": "   ",
                    "severity": "   ",
                    "merchant_token": merchant["merchant_token"],
                },
            )

            self.assertEqual(status, 200)
            self.assertEqual(review["review"]["reason"], "human_required")
            self.assertEqual(review["review"]["severity"], "review")

    def test_channel_message_api_ingests_external_buyer_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "marketplace.sqlite"
            app = create_app(db_file)
            _, merchant = self.request(
                app,
                "POST",
                "/merchants",
                {
                    "id": "seller-a",
                    "name": "West Lake Tea",
                    "city": "Hangzhou",
                    "service_area": "West Lake",
                },
            )
            self.request(
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
                    "merchant_token": merchant["merchant_token"],
                },
            )

            status, opened = self.request(
                app,
                "POST",
                "/channels/messages",
                {
                    "channel": "telegram",
                    "external_user_id": "@alice",
                    "external_message_id": "tg-msg-1",
                    "text": "longjing gift delivery today",
                    "city": "Hangzhou",
                },
            )
            self.assertEqual(status, 200)
            self.assertEqual(opened["buyer_id"], "telegram:@alice")
            self.assertEqual(opened["conversation"]["id"], "CONV-0001")
            self.assertEqual(opened["message"]["structured_payload"]["source_id"], "channel:telegram")
            self.assertEqual(opened["message"]["structured_payload"]["channel"], "telegram")

            status, retried_open = self.request(
                app,
                "POST",
                "/channels/messages",
                {
                    "channel": "telegram",
                    "external_user_id": "@alice",
                    "external_message_id": "tg-msg-1",
                    "text": "longjing gift delivery today",
                    "city": "Hangzhou",
                },
            )
            self.assertEqual(status, 200)
            self.assertTrue(retried_open["idempotent"])
            self.assertEqual(retried_open["message"]["id"], opened["message"]["id"])
            self.assertEqual(len(retried_open["conversation"]["messages"]), 1)

            status, continued = self.request(
                app,
                "POST",
                "/channels/messages",
                {
                    "channel": "telegram",
                    "external_user_id": "@alice",
                    "conversation_id": "CONV-0001",
                    "text": "Any stock left?",
                },
            )
            self.assertEqual(status, 200)
            self.assertEqual([message["sender"] for message in continued["conversation"]["messages"]], ["buyer", "buyer"])

            status, first_delivery = self.request(
                app,
                "POST",
                "/channels/messages",
                {
                    "channel": "telegram",
                    "external_user_id": "@alice",
                    "conversation_id": "CONV-0001",
                    "external_message_id": "tg-msg-2",
                    "text": "Delivery today?",
                },
            )
            self.assertEqual(status, 200)

            status, retried_delivery = self.request(
                app,
                "POST",
                "/channels/messages",
                {
                    "channel": "telegram",
                    "external_user_id": "@alice",
                    "conversation_id": "CONV-0001",
                    "external_message_id": "tg-msg-2",
                    "text": "Delivery today?",
                },
            )
            self.assertEqual(status, 200)
            self.assertTrue(retried_delivery["idempotent"])
            self.assertEqual(retried_delivery["message"]["id"], first_delivery["message"]["id"])
            self.assertEqual(len(retried_delivery["conversation"]["messages"]), 3)
            replay_events = [
                event
                for event in retried_delivery["conversation"]["audit_events"]
                if event["event"] == "channel_message_replayed"
            ]
            self.assertEqual(
                [event["details"]["external_message_id"] for event in replay_events],
                ["tg-msg-1", "tg-msg-2"],
            )
            self.assertEqual(
                [event["details"]["message_id"] for event in replay_events],
                [opened["message"]["id"], first_delivery["message"]["id"]],
            )

            status, denied = self.request(
                app,
                "POST",
                "/channels/messages",
                {
                    "channel": "telegram",
                    "external_user_id": "@bob",
                    "conversation_id": "CONV-0001",
                    "external_message_id": "tg-msg-bob-denied",
                    "text": "I should not enter alice's channel conversation.",
                },
            )
            self.assertEqual(status, 400)
            self.assertIn("cannot write", denied["error"])

            status, spoofed = self.request(
                app,
                "POST",
                "/channels/messages",
                {
                    "channel": "telegram",
                    "external_user_id": "@bob",
                    "buyer_id": "telegram:@alice",
                    "conversation_id": "CONV-0001",
                    "text": "Forged buyer_id should not enter alice's channel conversation.",
                },
            )
            self.assertEqual(status, 400)
            self.assertIn("buyer_id override", spoofed["error"])

            status, conversation = self.request(
                app,
                "GET",
                "/conversations/CONV-0001",
                headers={"authorization": f"Bearer {merchant['merchant_token']}"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(len(conversation["conversation"]["messages"]), 3)
            with db_session(db_file) as conn:
                poisoned = conn.execute(
                    """
                    select count(*) as count from channel_message_ingresses
                    where channel = 'telegram'
                      and external_user_id = '@bob'
                      and external_message_id = 'tg-msg-bob-denied'
                    """
                ).fetchone()["count"]
            self.assertEqual(poisoned, 0)

    def test_agent_message_process_api_enforces_merchant_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "marketplace.sqlite"
            app = create_app(db_file)

            status, merchant_a = self.request(app, "POST", "/merchants", {"id": "seller-a", "name": "West Lake Tea"})
            self.assertEqual(status, 200)
            status, merchant_b = self.request(app, "POST", "/merchants", {"id": "seller-b", "name": "Other Tea"})
            self.assertEqual(status, 200)
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
                    "tags": ["longjing"],
                    "merchant_token": merchant_a["merchant_token"],
                },
            )
            self.assertEqual(status, 200)
            status, ask = self.request(
                app,
                "POST",
                "/buyer/ask",
                {"buyer_id": "alice", "text": "longjing delivery today"},
            )
            self.assertEqual(status, 200)
            buyer_message_id = ask["conversation"]["messages"][0]["id"]

            claim_payload = {
                "merchant_id": "seller-a",
                "agent_id": "mai-cli-merchant-agent:seller-a",
                "conversation_id": "CONV-0001",
                "message_id": buyer_message_id,
                "idempotency_key": "mai-cli-merchant-agent:seller-a:1",
                "merchant_token": merchant_a["merchant_token"],
            }
            status, fractional_claim = self.request(
                app,
                "POST",
                "/agents/messages/claim",
                {**claim_payload, "message_id": buyer_message_id + 0.5},
            )
            self.assertEqual(status, 400)
            self.assertIn("message_id must be a whole number", fractional_claim["error"])

            status, claim = self.request(app, "POST", "/agents/messages/claim", claim_payload)
            self.assertEqual(status, 200)
            self.assertTrue(claim["claim"]["claimed"])

            status, fractional_stale_ttl = self.request(
                app,
                "POST",
                "/agents/messages/abandon-stale",
                {
                    "merchant_id": "seller-a",
                    "agent_id": "mai-cli-merchant-agent:seller-a",
                    "stale_after_seconds": 0.5,
                    "merchant_token": merchant_a["merchant_token"],
                },
            )
            self.assertEqual(status, 400)
            self.assertIn("stale_after_seconds must be a whole number", fractional_stale_ttl["error"])

            status, denied = self.request(
                app,
                "POST",
                "/agents/messages/claim",
                {
                    **claim_payload,
                    "merchant_id": "seller-b",
                    "agent_id": "mai-cli-merchant-agent:seller-b",
                    "merchant_token": merchant_b["merchant_token"],
                },
            )
            self.assertEqual(status, 403)
            self.assertIn("cannot access", denied["error"])

            status, fractional_complete = self.request(
                app,
                "POST",
                "/agents/messages/complete",
                {
                    "merchant_id": "seller-a",
                    "agent_id": "mai-cli-merchant-agent:seller-a",
                    "message_id": buyer_message_id + 0.5,
                    "merchant_token": merchant_a["merchant_token"],
                },
            )
            self.assertEqual(status, 400)
            self.assertIn("message_id must be a whole number", fractional_complete["error"])

            status, completed = self.request(
                app,
                "POST",
                "/agents/messages/complete",
                {
                    "merchant_id": "seller-a",
                    "agent_id": "mai-cli-merchant-agent:seller-a",
                    "message_id": buyer_message_id,
                    "merchant_token": merchant_a["merchant_token"],
                },
            )
            self.assertEqual(status, 200)
            self.assertEqual(completed["process"]["status"], "processed")

            status, conversation = self.request(
                app,
                "GET",
                "/conversations/CONV-0001",
                headers={"authorization": f"Bearer {merchant_a['merchant_token']}"},
            )
            self.assertEqual(status, 200)
            events = [event["event"] for event in conversation["conversation"]["audit_events"]]
            self.assertIn("agent_message_claimed", events)
            self.assertIn("agent_message_processed", events)

    def test_agent_token_is_scoped_to_agent_routes(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "marketplace.sqlite"
            app = create_app(db_file)

            status, merchant = self.request(app, "POST", "/merchants", {"id": "seller-a", "name": "West Lake Tea"})
            self.assertEqual(status, 200)
            merchant_token = merchant["merchant_token"]

            status, issued = self.request(
                app,
                "POST",
                "/agents/tokens",
                {"merchant_id": "seller-a", "merchant_token": merchant_token},
            )
            self.assertEqual(status, 200)
            self.assertEqual(issued["agent_id"], "mai-cli-merchant-agent:seller-a")
            agent_token = issued["agent_token"]

            status, heartbeat = self.request(
                app,
                "POST",
                "/agents/heartbeat",
                {"merchant_id": "seller-a", "_auth_token": agent_token},
            )
            self.assertEqual(status, 200)
            self.assertEqual(heartbeat["agent"]["owner_id"], "seller-a")

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
                    "_auth_token": agent_token,
                },
            )
            self.assertEqual(status, 403)
            self.assertIn("merchant token", product["error"])

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
                    "merchant_token": merchant_token,
                },
            )
            self.assertEqual(status, 200)
            status, ask = self.request(app, "POST", "/buyer/ask", {"buyer_id": "alice", "text": "longjing delivery today"})
            self.assertEqual(status, 200)
            buyer_message_id = ask["conversation"]["messages"][0]["id"]

            status, claim = self.request(
                app,
                "POST",
                "/agents/messages/claim",
                {
                    "merchant_id": "seller-a",
                    "agent_id": issued["agent_id"],
                    "conversation_id": "CONV-0001",
                    "message_id": buyer_message_id,
                    "idempotency_key": f"{issued['agent_id']}:{buyer_message_id}",
                    "_auth_token": agent_token,
                },
            )
            self.assertEqual(status, 200)
            self.assertTrue(claim["claim"]["claimed"])

            status, message = self.request(
                app,
                "POST",
                "/conversations/CONV-0001/messages",
                {
                    "sender": "merchant_agent",
                    "intent": "ask_delivery",
                    "text": "Stock is 5.",
                    "status": "waiting_buyer",
                    "structured_payload": {"source_id": issued["agent_id"]},
                    "_auth_token": agent_token,
                },
            )
            self.assertEqual(status, 200)
            self.assertEqual(message["message"]["sender"], "merchant_agent")

            status, review = self.request(
                app,
                "POST",
                "/conversations/CONV-0001/human-review",
                {
                    "reason": "low_stock",
                    "source_id": issued["agent_id"],
                    "_auth_token": agent_token,
                },
            )
            self.assertEqual(status, 200)
            self.assertEqual(review["review"]["reason"], "low_stock")

            status, spoofed_message = self.request(
                app,
                "POST",
                "/conversations/CONV-0001/messages",
                {
                    "sender": "merchant_agent",
                    "intent": "ask_delivery",
                    "text": "Spoofed source.",
                    "status": "waiting_buyer",
                    "structured_payload": {"source_id": "mai-cli-merchant-agent:seller-b"},
                    "merchant_token": merchant_token,
                },
            )
            self.assertEqual(status, 403)
            self.assertIn("cannot act", spoofed_message["error"])

            status, spoofed_review = self.request(
                app,
                "POST",
                "/conversations/CONV-0001/human-review",
                {
                    "reason": "spoofed",
                    "source_id": "mai-cli-merchant-agent:seller-b",
                    "merchant_token": merchant_token,
                },
            )
            self.assertEqual(status, 403)
            self.assertIn("cannot act", spoofed_review["error"])

    def test_agent_token_revoke_api_blocks_future_agent_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "marketplace.sqlite"
            app = create_app(db_file)

            status, merchant = self.request(app, "POST", "/merchants", {"id": "seller-a", "name": "West Lake Tea"})
            self.assertEqual(status, 200)
            merchant_token = merchant["merchant_token"]
            status, issued = self.request(
                app,
                "POST",
                "/agents/tokens",
                {"merchant_id": "seller-a", "merchant_token": merchant_token},
            )
            self.assertEqual(status, 200)
            agent_token = issued["agent_token"]

            status, heartbeat = self.request(
                app,
                "POST",
                "/agents/heartbeat",
                {"merchant_id": "seller-a", "_auth_token": agent_token},
            )
            self.assertEqual(status, 200)

            status, other_merchant = self.request(app, "POST", "/merchants", {"id": "seller-b", "name": "Other Tea"})
            self.assertEqual(status, 200)
            status, cross_merchant = self.request(
                app,
                "POST",
                "/agents/tokens/revoke",
                {"merchant_id": "seller-b", "token": agent_token, "merchant_token": other_merchant["merchant_token"]},
            )
            self.assertEqual(status, 403)
            status, still_active = self.request(
                app,
                "POST",
                "/agents/heartbeat",
                {"merchant_id": "seller-a", "_auth_token": agent_token},
            )
            self.assertEqual(status, 200)

            status, anonymous = self.request(
                app,
                "POST",
                "/agents/tokens/revoke",
                {"merchant_id": "seller-a", "token": agent_token},
            )
            self.assertEqual(status, 403)
            status, revoked = self.request(
                app,
                "POST",
                "/agents/tokens/revoke",
                {"merchant_id": "seller-a", "token": agent_token, "merchant_token": merchant_token},
            )
            self.assertEqual(status, 200)
            self.assertTrue(revoked["revoked"])
            self.assertEqual(revoked["token_role"], "agent")

            status, denied = self.request(
                app,
                "POST",
                "/agents/heartbeat",
                {"merchant_id": "seller-a", "_auth_token": agent_token},
            )
            self.assertEqual(status, 403)
            self.assertIn("revoked", denied["error"])

    def test_agent_token_ttl_api_blocks_expired_agent_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "marketplace.sqlite"
            app = create_app(db_file)

            status, merchant = self.request(app, "POST", "/merchants", {"id": "seller-a", "name": "West Lake Tea"})
            self.assertEqual(status, 200)
            status, issued = self.request(
                app,
                "POST",
                "/agents/tokens",
                {"merchant_id": "seller-a", "merchant_token": merchant["merchant_token"], "ttl_seconds": 3600},
            )
            self.assertEqual(status, 200)
            self.assertTrue(issued["expires_at"])
            agent_token = issued["agent_token"]

            status, heartbeat = self.request(
                app,
                "POST",
                "/agents/heartbeat",
                {"merchant_id": "seller-a", "_auth_token": agent_token},
            )
            self.assertEqual(status, 200)

            conn = sqlite3.connect(db_file)
            try:
                conn.execute("update api_tokens set expires_at = ? where token = ?", ("2000-01-01T00:00:00", agent_token))
                conn.commit()
            finally:
                conn.close()

            status, denied = self.request(
                app,
                "POST",
                "/agents/heartbeat",
                {"merchant_id": "seller-a", "_auth_token": agent_token},
            )
            self.assertEqual(status, 403)
            self.assertIn("expired", denied["error"])

    def test_agent_token_ttl_api_rejects_fractional_seconds(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "marketplace.sqlite"
            app = create_app(db_file)

            status, merchant = self.request(app, "POST", "/merchants", {"id": "seller-a", "name": "West Lake Tea"})
            self.assertEqual(status, 200)
            merchant_token = merchant["merchant_token"]
            status, fractional_issue = self.request(
                app,
                "POST",
                "/agents/tokens",
                {"merchant_id": "seller-a", "merchant_token": merchant_token, "ttl_seconds": 3600.5},
            )
            self.assertEqual(status, 400)
            self.assertIn("ttl_seconds must be a whole number", fractional_issue["error"])

            status, issued = self.request(
                app,
                "POST",
                "/agents/tokens",
                {"merchant_id": "seller-a", "merchant_token": merchant_token},
            )
            self.assertEqual(status, 200)
            status, fractional_rotate = self.request(
                app,
                "POST",
                "/agents/tokens/rotate",
                {
                    "merchant_id": "seller-a",
                    "merchant_token": merchant_token,
                    "token": issued["agent_token"],
                    "ttl_seconds": 7200.5,
                },
            )
            self.assertEqual(status, 400)
            self.assertIn("ttl_seconds must be a whole number", fractional_rotate["error"])

    def test_agent_token_list_api_reports_status_without_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "marketplace.sqlite"
            app = create_app(db_file)

            status, merchant = self.request(app, "POST", "/merchants", {"id": "seller-a", "name": "West Lake Tea"})
            self.assertEqual(status, 200)
            merchant_token = merchant["merchant_token"]
            status, expiring = self.request(
                app,
                "POST",
                "/agents/tokens",
                {"merchant_id": "seller-a", "merchant_token": merchant_token, "ttl_seconds": 3600},
            )
            self.assertEqual(status, 200)
            status, revocable = self.request(
                app,
                "POST",
                "/agents/tokens",
                {"merchant_id": "seller-a", "merchant_token": merchant_token},
            )
            self.assertEqual(status, 200)
            status, revoked = self.request(
                app,
                "POST",
                "/agents/tokens/revoke",
                {"merchant_id": "seller-a", "merchant_token": merchant_token, "token": revocable["agent_token"]},
            )
            self.assertEqual(status, 200)

            conn = sqlite3.connect(db_file)
            try:
                conn.execute(
                    "update api_tokens set expires_at = ? where token = ?",
                    ("2000-01-01T00:00:00", expiring["agent_token"]),
                )
                conn.commit()
            finally:
                conn.close()

            status, anonymous = self.request(app, "GET", "/agents/tokens", query_string="merchant_id=seller-a")
            self.assertEqual(status, 403)
            status, listed = self.request(
                app,
                "GET",
                "/agents/tokens",
                query_string="merchant_id=seller-a",
                headers={"authorization": f"Bearer {merchant_token}"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(len(listed["tokens"]), 2)
            serialized = json.dumps(listed, sort_keys=True)
            self.assertNotIn(expiring["agent_token"], serialized)
            self.assertNotIn(revocable["agent_token"], serialized)
            by_prefix = {token["token_prefix"]: token for token in listed["tokens"]}
            expired_item = by_prefix[expiring["agent_token"][:24]]
            revoked_item = by_prefix[revocable["agent_token"][:24]]
            self.assertTrue(expired_item["expired"])
            self.assertFalse(expired_item["active"])
            self.assertTrue(revoked_item["revoked"])
            self.assertFalse(revoked_item["active"])
            self.assertEqual(revoked_item["revoked_at"], revoked["revoked_at"])

    def test_agent_token_rotate_api_revokes_old_token_and_returns_new_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "marketplace.sqlite"
            app = create_app(db_file)

            status, merchant = self.request(app, "POST", "/merchants", {"id": "seller-a", "name": "West Lake Tea"})
            self.assertEqual(status, 200)
            merchant_token = merchant["merchant_token"]
            status, issued = self.request(
                app,
                "POST",
                "/agents/tokens",
                {"merchant_id": "seller-a", "merchant_token": merchant_token},
            )
            self.assertEqual(status, 200)
            old_token = issued["agent_token"]

            status, rotated = self.request(
                app,
                "POST",
                "/agents/tokens/rotate",
                {"merchant_id": "seller-a", "merchant_token": merchant_token, "token": old_token, "ttl_seconds": 3600},
            )
            self.assertEqual(status, 200)
            new_token = rotated["agent_token"]
            self.assertNotEqual(new_token, old_token)
            self.assertTrue(rotated["expires_at"])
            self.assertEqual(rotated["previous_token"]["token_prefix"], old_token[:24])
            self.assertNotIn(old_token, json.dumps(rotated, sort_keys=True))

            status, old_denied = self.request(
                app,
                "POST",
                "/agents/heartbeat",
                {"merchant_id": "seller-a", "_auth_token": old_token},
            )
            self.assertEqual(status, 403)
            self.assertIn("revoked", old_denied["error"])
            status, new_heartbeat = self.request(
                app,
                "POST",
                "/agents/heartbeat",
                {"merchant_id": "seller-a", "_auth_token": new_token},
            )
            self.assertEqual(status, 200)

    def test_agent_token_rotate_api_accepts_unique_token_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "marketplace.sqlite"
            app = create_app(db_file)

            status, merchant = self.request(app, "POST", "/merchants", {"id": "seller-a", "name": "West Lake Tea"})
            self.assertEqual(status, 200)
            merchant_token = merchant["merchant_token"]
            status, issued = self.request(
                app,
                "POST",
                "/agents/tokens",
                {"merchant_id": "seller-a", "merchant_token": merchant_token},
            )
            self.assertEqual(status, 200)
            old_token = issued["agent_token"]
            status, listed = self.request(
                app,
                "GET",
                "/agents/tokens",
                query_string="merchant_id=seller-a",
                headers={"authorization": f"Bearer {merchant_token}"},
            )
            self.assertEqual(status, 200)
            token_prefix = listed["tokens"][0]["token_prefix"]

            status, rotated = self.request(
                app,
                "POST",
                "/agents/tokens/rotate",
                {"merchant_id": "seller-a", "merchant_token": merchant_token, "token_prefix": token_prefix},
            )
            self.assertEqual(status, 200)
            self.assertNotEqual(rotated["agent_token"], old_token)
            status, old_denied = self.request(
                app,
                "POST",
                "/agents/heartbeat",
                {"merchant_id": "seller-a", "_auth_token": old_token},
            )
            self.assertEqual(status, 403)

    def test_agent_token_prefix_api_rejects_ambiguous_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "marketplace.sqlite"
            app = create_app(db_file)

            status, merchant = self.request(app, "POST", "/merchants", {"id": "seller-a", "name": "West Lake Tea"})
            self.assertEqual(status, 200)
            merchant_token = merchant["merchant_token"]
            for _ in range(2):
                status, _issued = self.request(
                    app,
                    "POST",
                    "/agents/tokens",
                    {"merchant_id": "seller-a", "merchant_token": merchant_token},
                )
                self.assertEqual(status, 200)

            status, ambiguous = self.request(
                app,
                "POST",
                "/agents/tokens/revoke",
                {"merchant_id": "seller-a", "merchant_token": merchant_token, "token_prefix": "mai_agent_seller-a_"},
            )
            self.assertEqual(status, 400)
            self.assertIn("ambiguous", ambiguous["error"])

    def test_agent_token_lifecycle_api_records_audit_without_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "marketplace.sqlite"
            app = create_app(db_file)

            status, merchant = self.request(app, "POST", "/merchants", {"id": "seller-a", "name": "West Lake Tea"})
            self.assertEqual(status, 200)
            merchant_token = merchant["merchant_token"]
            status, issued = self.request(
                app,
                "POST",
                "/agents/tokens",
                {"merchant_id": "seller-a", "merchant_token": merchant_token, "ttl_seconds": 3600},
            )
            self.assertEqual(status, 200)
            old_token = issued["agent_token"]
            status, rotated = self.request(
                app,
                "POST",
                "/agents/tokens/rotate",
                {"merchant_id": "seller-a", "merchant_token": merchant_token, "token": old_token, "ttl_seconds": 7200},
            )
            self.assertEqual(status, 200)
            new_token = rotated["agent_token"]
            status, revoked = self.request(
                app,
                "POST",
                "/agents/tokens/revoke",
                {"merchant_id": "seller-a", "merchant_token": merchant_token, "token": new_token},
            )
            self.assertEqual(status, 200)

            conn = sqlite3.connect(db_file)
            try:
                rows = conn.execute(
                    "select actor, event, details_json from audit_events where conversation_id = '' order by id"
                ).fetchall()
            finally:
                conn.close()
            self.assertEqual([row[1] for row in rows], ["agent_token_issued", "agent_token_rotated", "agent_token_revoked"])
            self.assertTrue(all(row[0] == "seller-a" for row in rows))
            serialized = json.dumps([json.loads(row[2]) for row in rows], sort_keys=True)
            self.assertNotIn(old_token, serialized)
            self.assertNotIn(new_token, serialized)
            self.assertIn(issued["agent_id"], serialized)
            self.assertIn(revoked["revoked_at"], serialized)

    def test_audit_events_api_requires_merchant_token_and_filters_without_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "marketplace.sqlite"
            app = create_app(db_file)

            status, merchant = self.request(app, "POST", "/merchants", {"id": "seller-a", "name": "West Lake Tea"})
            self.assertEqual(status, 200)
            merchant_token = merchant["merchant_token"]
            status, issued = self.request(
                app,
                "POST",
                "/agents/tokens",
                {"merchant_id": "seller-a", "merchant_token": merchant_token},
            )
            self.assertEqual(status, 200)

            status, anonymous = self.request(
                app,
                "GET",
                "/audit/events",
                query_string="merchant_id=seller-a&event=agent_token_issued",
            )
            self.assertEqual(status, 403)
            status, listed = self.request(
                app,
                "GET",
                "/audit/events",
                query_string="merchant_id=seller-a&event=agent_token_issued&limit=10",
                headers={"authorization": f"Bearer {merchant_token}"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(len(listed["events"]), 1)
            event = listed["events"][0]
            self.assertEqual(event["actor"], "seller-a")
            self.assertEqual(event["event"], "agent_token_issued")
            serialized = json.dumps(listed, sort_keys=True)
            self.assertNotIn(issued["agent_token"], serialized)
            self.assertEqual(event["details"]["token"]["token_prefix"], issued["agent_token"][:24])

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
                headers={"authorization": f"Bearer {created['buyer_token']}"},
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

            status, agent_list = self.request(
                app,
                "GET",
                "/agents",
                headers={"authorization": f"Bearer {merchant_token}"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(agent_list["agents"][0]["id"], agent_id)

            status, agent_detail = self.request(
                app,
                "GET",
                f"/agents/{agent_id}",
                headers={"authorization": f"Bearer {merchant_token}"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(agent_detail["agent"]["replied_count"], 1)

            status, merchant_agents = self.request(
                app,
                "GET",
                "/merchants/seller-a/agents",
                headers={"authorization": f"Bearer {merchant_token}"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(merchant_agents["agents"][0]["id"], agent_id)

            conn = sqlite3.connect(db_file)
            try:
                conn.execute("update agents set last_seen_at = '2000-01-01T00:00:00' where id = ?", (agent_id,))
                conn.commit()
            finally:
                conn.close()
            status, stale_agent = self.request(
                app,
                "GET",
                f"/agents/{agent_id}",
                headers={"authorization": f"Bearer {merchant_token}"},
            )
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

            status, queue = self.request(
                app,
                "GET",
                "/human-review/queue",
                query_string="merchant_id=seller-a",
                headers={"authorization": f"Bearer {merchant_token}"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(queue["reviews"][0]["conversation_id"], "CONV-0001")
            self.assertEqual(queue["reviews"][0]["merchant_id"], "seller-a")
            self.assertEqual(queue["reviews"][0]["buyer_id"], "alice")

            status, invalid_sender = self.request(
                app,
                "POST",
                "/conversations/CONV-0001/human-review/resolve",
                {
                    "action": "reply",
                    "sender": "buyer",
                    "text": "Merchant token must not spoof buyer.",
                    "merchant_token": merchant_token,
                },
            )
            self.assertEqual(status, 400)
            self.assertIn("Unknown human-review sender", invalid_sender["error"])

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

            status, duplicate_resolve = self.request(
                app,
                "POST",
                "/conversations/CONV-0001/human-review/resolve",
                {
                    "action": "reply",
                    "text": "There is no pending review left.",
                    "sender": "merchant",
                    "merchant_token": merchant_token,
                },
            )
            self.assertEqual(status, 400)
            self.assertIn("No unresolved human reviews", duplicate_resolve["error"])

            status, closed = self.request(
                app,
                "POST",
                "/conversations/CONV-0001/close",
                {"sender": "operator", "text": "Closed after confirmation.", "merchant_token": merchant_token},
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
                agents = _list_agents(db_file, {"merchant_token": merchant_token})

            self.assertFalse(agents["agents"][0]["stale"])
            self.assertEqual(agents["agents"][0]["stale_ttl_seconds"], 9999999999)


if __name__ == "__main__":
    unittest.main()
