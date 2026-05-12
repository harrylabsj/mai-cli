import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import mai  # noqa: E402


class MaiCliTest(unittest.TestCase):
    def run_cli(self, db_file, *args):
        output = StringIO()
        with redirect_stdout(output):
            mai.main(["--data", str(db_file), *args])
        return output.getvalue()

    def test_catalog_search_and_stock_management_use_sqlite(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"

            self.run_cli(
                db_file,
                "merchant",
                "create",
                "--id",
                "seller-a",
                "--name",
                "West Lake Tea",
                "--city",
                "Hangzhou",
                "--service-area",
                "West Lake",
                "--delivery-fee",
                "12",
                "--delivery-eta-minutes",
                "45",
            )
            self.run_cli(
                db_file,
                "product",
                "add",
                "--merchant",
                "seller-a",
                "--sku",
                "tea-a",
                "--title",
                "Longjing Gift Box",
                "--price",
                "88",
                "--stock",
                "5",
                "--tags",
                "longjing,gift",
            )
            search = json.loads(
                self.run_cli(db_file, "search", "products", "--query", "longjing", "--format", "json")
            )
            self.assertEqual(search["results"][0]["sku"], "tea-a")
            self.assertEqual(search["results"][0]["delivery"]["eta_minutes"], 45)

            merchants = json.loads(
                self.run_cli(db_file, "search", "merchants", "--query", "west lake", "--city", "Hangzhou", "--format", "json")
            )
            self.assertEqual(merchants["results"][0]["id"], "seller-a")

            self.run_cli(db_file, "product", "stock", "--sku", "tea-a", "--stock", "3")
            updated = json.loads(
                self.run_cli(db_file, "search", "products", "--query", "longjing", "--format", "json")
            )
            self.assertEqual(updated["results"][0]["stock"], 3)

            conn = sqlite3.connect(db_file)
            try:
                tables = {row[0] for row in conn.execute("select name from sqlite_master where type = 'table'")}
            finally:
                conn.close()
            self.assertIn("merchants", tables)
            self.assertIn("products", tables)
            self.assertIn("delivery_rules", tables)
            self.assertNotIn("orders", tables)

    def test_merchant_and_product_update_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            self.run_cli(db_file, "merchant", "create", "--id", "seller-a", "--name", "West Lake Tea")
            self.run_cli(
                db_file,
                "merchant",
                "update",
                "--id",
                "seller-a",
                "--city",
                "Hangzhou",
                "--service-area",
                "West Lake",
                "--contact",
                "wechat:new",
                "--hours",
                "10:00-20:00",
                "--automation-boundaries",
                "Catalog and delivery only.",
                "--delivery-fee",
                "10",
                "--delivery-eta-minutes",
                "30",
            )
            self.run_cli(
                db_file,
                "product",
                "add",
                "--merchant",
                "seller-a",
                "--sku",
                "tea-a",
                "--title",
                "Longjing Gift Box",
                "--price",
                "88",
                "--stock",
                "5",
                "--tags",
                "longjing",
            )
            self.run_cli(
                db_file,
                "product",
                "update",
                "--merchant",
                "seller-a",
                "--sku",
                "tea-a",
                "--price",
                "92",
                "--stock",
                "4",
                "--delivery-attributes",
                "same-city",
            )

            search = json.loads(
                self.run_cli(db_file, "search", "products", "--query", "longjing", "--format", "json")
            )
            product = search["results"][0]
            self.assertEqual(product["price"], 92.0)
            self.assertEqual(product["stock"], 4)
            self.assertEqual(product["merchant"]["contact"], "wechat:new")
            self.assertEqual(product["merchant"]["delivery"]["eta_minutes"], 30)

    def test_agent_run_once_can_use_http_marketplace_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            constructed = []

            class FakeHTTPMerchantAgentTools:
                def __init__(self, base_url, merchant_id, merchant_token):
                    constructed.append(
                        {
                            "base_url": base_url,
                            "merchant_id": merchant_id,
                            "merchant_token": merchant_token,
                        }
                    )

            with (
                patch("mai_cli.cli.HTTPMerchantAgentTools", FakeHTTPMerchantAgentTools),
                patch(
                    "mai_cli.cli.merchant_agent.process_once_with_tools",
                    return_value={"ok": True, "merchant_id": "seller-a", "checked": 0, "replied": []},
                ) as process_once,
            ):
                output = self.run_cli(
                    db_file,
                    "agent",
                    "run",
                    "--merchant",
                    "seller-a",
                    "--once",
                    "--api-url",
                    "http://127.0.0.1:8765",
                    "--agent-token",
                    "agent_tok_seller_a",
                    "--format",
                    "json",
                )

            self.assertEqual(json.loads(output)["merchant_id"], "seller-a")
            self.assertEqual(
                constructed,
                [
                    {
                        "base_url": "http://127.0.0.1:8765",
                        "merchant_id": "seller-a",
                        "merchant_token": "agent_tok_seller_a",
                    }
                ],
            )
            self.assertEqual(process_once.call_args.args[1], "seller-a")

    def test_agent_run_once_can_read_api_token_from_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            constructed = []

            class FakeHTTPMerchantAgentTools:
                def __init__(self, base_url, merchant_id, merchant_token):
                    constructed.append(
                        {
                            "base_url": base_url,
                            "merchant_id": merchant_id,
                            "merchant_token": merchant_token,
                        }
                    )

            with (
                patch.dict(os.environ, {"MAI_AGENT_TOKEN": "env_agent_tok_seller_a"}, clear=False),
                patch("mai_cli.cli.HTTPMerchantAgentTools", FakeHTTPMerchantAgentTools),
                patch(
                    "mai_cli.cli.merchant_agent.process_once_with_tools",
                    return_value={"ok": True, "merchant_id": "seller-a", "checked": 0, "replied": []},
                ),
            ):
                self.run_cli(
                    db_file,
                    "agent",
                    "run",
                    "--merchant",
                    "seller-a",
                    "--once",
                    "--api-url",
                    "http://127.0.0.1:8765",
                    "--format",
                    "json",
                )

            self.assertEqual(constructed[0]["merchant_token"], "env_agent_tok_seller_a")

    def test_agent_run_once_can_read_api_url_from_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            constructed = []

            class FakeHTTPMerchantAgentTools:
                def __init__(self, base_url, merchant_id, merchant_token):
                    constructed.append(
                        {
                            "base_url": base_url,
                            "merchant_id": merchant_id,
                            "merchant_token": merchant_token,
                        }
                    )

            with (
                patch.dict(
                    os.environ,
                    {
                        "MAI_MARKETPLACE_API_URL": "http://127.0.0.1:8765",
                        "MAI_AGENT_TOKEN": "env_agent_tok_seller_a",
                    },
                    clear=False,
                ),
                patch("mai_cli.cli.HTTPMerchantAgentTools", FakeHTTPMerchantAgentTools),
                patch(
                    "mai_cli.cli.merchant_agent.process_once_with_tools",
                    return_value={"ok": True, "merchant_id": "seller-a", "checked": 0, "replied": []},
                ),
            ):
                self.run_cli(
                    db_file,
                    "agent",
                    "run",
                    "--merchant",
                    "seller-a",
                    "--once",
                    "--format",
                    "json",
                )

            self.assertEqual(constructed[0]["base_url"], "http://127.0.0.1:8765")

    def test_api_routes_json_includes_route_methods(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"

            output = self.run_cli(db_file, "api", "routes", "--format", "json")

            result = json.loads(output)
            self.assertIn("/agents/tokens", result["routes"])
            routes_by_path = {route["path"]: route["methods"] for route in result["route_details"]}
            self.assertEqual(routes_by_path["/agents/tokens"], ["GET", "POST"])
            self.assertEqual(routes_by_path["/audit/events"], ["GET"])

    def test_api_routes_text_lists_methods_and_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"

            output = self.run_cli(db_file, "api", "routes")

            self.assertIn("GET    /agents/tokens", output)
            self.assertIn("POST   /agents/tokens", output)
            self.assertIn("GET    /audit/events", output)
            self.assertNotIn('"route_details"', output)

    def test_agent_run_can_loop_with_http_marketplace_tools_until_stop_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            stop_file = Path(tmp) / "agent.stop"
            stop_file.write_text("stop", encoding="utf-8")
            calls = []

            class FakeHTTPMerchantAgentTools:
                def __init__(self, base_url, merchant_id, merchant_token):
                    calls.append(("init", base_url, merchant_id, merchant_token))

                def heartbeat(self, merchant_id, status="online", **kwargs):
                    calls.append(("heartbeat", merchant_id, status, kwargs))
                    return {"id": f"mai-cli-merchant-agent:{merchant_id}", "owner_id": merchant_id, "status": status}

            with (
                patch("mai_cli.cli.HTTPMerchantAgentTools", FakeHTTPMerchantAgentTools),
            ):
                self.run_cli(
                    db_file,
                    "agent",
                    "run",
                    "--merchant",
                    "seller-a",
                    "--api-url",
                    "http://127.0.0.1:8765",
                    "--agent-token",
                    "agent_tok_seller_a",
                    "--stop-file",
                    str(stop_file),
                    "--format",
                    "json",
                )

            self.assertIn(("init", "http://127.0.0.1:8765", "seller-a", "agent_tok_seller_a"), calls)
            self.assertIn(("heartbeat", "seller-a", "away", {}), calls)
            self.assertFalse(stop_file.exists())

    def test_agent_start_can_use_api_backed_runtime_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            state_dir = Path(tmp) / "state"
            calls = []

            def fake_start(db_path, merchant_id, **kwargs):
                calls.append({"db_path": db_path, "merchant_id": merchant_id, **kwargs})
                return {"ok": True, "merchant_id": merchant_id, "mode": "api", "message": "started"}

            with patch("mai_cli.cli.merchant_daemon.start_agent", side_effect=fake_start):
                self.run_cli(
                    db_file,
                    "agent",
                    "start",
                    "--merchant",
                    "seller-a",
                    "--api-url",
                    "http://127.0.0.1:8765",
                    "--agent-token",
                    "agent_secret",
                    "--state-dir",
                    str(state_dir),
                    "--format",
                    "json",
                )

            self.assertEqual(calls[0]["merchant_id"], "seller-a")
            self.assertEqual(calls[0]["api_url"], "http://127.0.0.1:8765")
            self.assertEqual(calls[0]["agent_token"], "agent_secret")
            self.assertEqual(calls[0]["merchant_token"], "")

    def test_llm_run_cli_invokes_tool_loop_with_role_prompt_and_budgets(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            dispatcher = object()

            with (
                patch("mai_cli.cli.provider_from_env", return_value="provider", create=True),
                patch("mai_cli.cli.MarketplaceToolDispatcher", return_value=dispatcher, create=True) as dispatcher_cls,
                patch(
                    "mai_cli.cli.run_marketplace_tool_loop",
                    return_value={"ok": True, "content": "LLM answer.", "error": "", "tool_results": []},
                    create=True,
                ) as run_loop,
            ):
                output = self.run_cli(
                    db_file,
                    "llm",
                    "run",
                    "--role",
                    "buyer",
                    "--actor",
                    "alice",
                    "--text",
                    "Find longjing near Hangzhou.",
                    "--max-tool-calls",
                    "2",
                    "--provider-retries",
                    "1",
                    "--format",
                    "json",
                )

            result = json.loads(output)
            self.assertTrue(result["ok"])
            self.assertEqual(result["content"], "LLM answer.")
            dispatcher_cls.assert_called_once()
            self.assertEqual(dispatcher_cls.call_args.kwargs["actor"], "alice")
            self.assertEqual(dispatcher_cls.call_args.kwargs["token_scope"], "buyer")
            self.assertIn("buyer-side assistant", run_loop.call_args.args[2][0]["content"])
            self.assertEqual(run_loop.call_args.kwargs["max_tool_calls"], 2)
            self.assertEqual(run_loop.call_args.kwargs["provider_retries"], 1)

    def test_llm_run_cli_can_include_owned_conversation_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            self.run_cli(db_file, "merchant", "create", "--id", "seller-a", "--name", "West Lake Tea")
            self.run_cli(
                db_file,
                "conversation",
                "create",
                "--buyer",
                "alice",
                "--merchant",
                "seller-a",
                "--text",
                "Can this deliver today?",
            )

            dispatcher = object()
            with (
                patch("mai_cli.cli.provider_from_env", return_value="provider", create=True),
                patch("mai_cli.cli.MarketplaceToolDispatcher", return_value=dispatcher, create=True),
                patch(
                    "mai_cli.cli.run_marketplace_tool_loop",
                    return_value={"ok": True, "content": "LLM answer.", "error": "", "tool_results": []},
                    create=True,
                ) as run_loop,
            ):
                self.run_cli(
                    db_file,
                    "llm",
                    "run",
                    "--role",
                    "buyer",
                    "--actor",
                    "alice",
                    "--conversation",
                    "CONV-0001",
                    "--text",
                    "Continue this consultation.",
                    "--format",
                    "json",
                )

            user_message = run_loop.call_args.args[2][1]["content"]
            self.assertIn("Continue this consultation.", user_message)
            self.assertIn("CONV-0001", user_message)
            self.assertIn("Can this deliver today?", user_message)

            with self.assertRaises(SystemExit):
                self.run_cli(
                    db_file,
                    "llm",
                    "run",
                    "--role",
                    "buyer",
                    "--actor",
                    "bob",
                    "--conversation",
                    "CONV-0001",
                    "--text",
                    "Continue this consultation.",
                )

    def test_llm_run_cli_can_use_api_backed_dispatcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            dispatcher = object()

            with (
                patch("mai_cli.cli.provider_from_env", return_value="provider", create=True),
                patch("mai_cli.cli.HTTPMarketplaceToolDispatcher", return_value=dispatcher, create=True) as dispatcher_cls,
                patch(
                    "mai_cli.cli.run_marketplace_tool_loop",
                    return_value={"ok": True, "content": "API-backed answer.", "error": "", "tool_results": []},
                    create=True,
                ) as run_loop,
            ):
                output = self.run_cli(
                    db_file,
                    "llm",
                    "run",
                    "--role",
                    "buyer",
                    "--actor",
                    "alice",
                    "--api-url",
                    "http://127.0.0.1:8765",
                    "--auth-token",
                    "buyer-token",
                    "--text",
                    "Continue through API.",
                    "--max-tool-calls",
                    "1",
                    "--format",
                    "json",
                )

            result = json.loads(output)
            self.assertTrue(result["ok"])
            dispatcher_cls.assert_called_once()
            self.assertEqual(dispatcher_cls.call_args.args[0], "http://127.0.0.1:8765")
            self.assertEqual(dispatcher_cls.call_args.kwargs["auth_token"], "buyer-token")
            self.assertEqual(dispatcher_cls.call_args.kwargs["actor"], "alice")
            self.assertEqual(dispatcher_cls.call_args.kwargs["token_scope"], "buyer")
            self.assertIs(run_loop.call_args.args[1], dispatcher)
            self.assertEqual(run_loop.call_args.kwargs["max_tool_calls"], 1)

    def test_adapter_cli_exposes_inspect_doctor_and_install_command_helpers(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            skill_root = Path(tmp) / "skill"

            inspect_output = self.run_cli(
                db_file,
                "adapter",
                "inspect",
                "--host",
                "openclaw",
                "--project-root",
                str(ROOT),
                "--skill-root",
                str(skill_root),
                "--format",
                "json",
            )
            doctor_output = self.run_cli(
                db_file,
                "adapter",
                "doctor",
                "--host",
                "openclaw",
                "--project-root",
                str(ROOT),
                "--skill-root",
                str(skill_root),
                "--format",
                "json",
            )
            install_output = self.run_cli(
                db_file,
                "adapter",
                "install-command",
                "--host",
                "openclaw",
                "--project-root",
                str(ROOT),
                "--dry-run",
                "--format",
                "json",
            )

            self.assertEqual(json.loads(inspect_output)["host"], "OpenClaw")
            self.assertIn("issues", json.loads(doctor_output))
            self.assertEqual(json.loads(install_output)["command"][-1], "--dry-run")
            self.assertEqual(json.loads(inspect_output)["project_root"], str(ROOT))
            self.assertEqual(json.loads(inspect_output)["skill_root"], str(skill_root))

    def test_agent_token_command_issues_scoped_agent_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            created = json.loads(
                self.run_cli(db_file, "merchant", "create", "--id", "seller-a", "--name", "West Lake Tea", "--format", "json")
            )

            issued = json.loads(
                self.run_cli(
                    db_file,
                    "agent",
                    "token",
                    "--merchant",
                    "seller-a",
                    "--format",
                    "json",
                )
            )

            self.assertEqual(issued["agent_id"], "mai-cli-merchant-agent:seller-a")
            self.assertTrue(issued["agent_token"].startswith("mai_agent_seller-a_"))

            text_output = self.run_cli(db_file, "agent", "token", "--merchant", "seller-a")
            self.assertIn("Agent token issued for mai-cli-merchant-agent:seller-a", text_output)
            self.assertIn("mai_agent_seller-a_", text_output)
            self.assertNotIn('"agent_token"', text_output)

    def test_agent_revoke_token_command_revokes_scoped_agent_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            self.run_cli(db_file, "merchant", "create", "--id", "seller-a", "--name", "West Lake Tea")
            issued = json.loads(
                self.run_cli(db_file, "agent", "token", "--merchant", "seller-a", "--format", "json")
            )
            revoked = json.loads(
                self.run_cli(
                    db_file,
                    "agent",
                    "revoke-token",
                    "--merchant",
                    "seller-a",
                    "--token",
                    issued["agent_token"],
                    "--format",
                    "json",
                )
            )

            self.assertTrue(revoked["revoked"])
            self.assertEqual(revoked["agent_id"], "mai-cli-merchant-agent:seller-a")
            conn = sqlite3.connect(db_file)
            try:
                row = conn.execute(
                    "select revoked_at from api_tokens where token = ?",
                    (issued["agent_token"],),
                ).fetchone()
            finally:
                conn.close()
            self.assertTrue(row[0])

    def test_agent_revoke_token_command_accepts_unique_token_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            self.run_cli(db_file, "merchant", "create", "--id", "seller-a", "--name", "West Lake Tea")
            issued = json.loads(
                self.run_cli(db_file, "agent", "token", "--merchant", "seller-a", "--format", "json")
            )
            listed = json.loads(self.run_cli(db_file, "agent", "tokens", "--merchant", "seller-a", "--format", "json"))
            revoked = json.loads(
                self.run_cli(
                    db_file,
                    "agent",
                    "revoke-token",
                    "--merchant",
                    "seller-a",
                    "--token-prefix",
                    listed["tokens"][0]["token_prefix"],
                    "--format",
                    "json",
                )
            )

            self.assertTrue(revoked["revoked"])
            conn = sqlite3.connect(db_file)
            try:
                row = conn.execute(
                    "select revoked_at from api_tokens where token = ?",
                    (issued["agent_token"],),
                ).fetchone()
            finally:
                conn.close()
            self.assertTrue(row[0])

    def test_agent_revoke_token_command_rejects_ambiguous_token_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            self.run_cli(db_file, "merchant", "create", "--id", "seller-a", "--name", "West Lake Tea")
            for _ in range(2):
                self.run_cli(db_file, "agent", "token", "--merchant", "seller-a", "--format", "json")

            with self.assertRaises(SystemExit) as raised:
                self.run_cli(
                    db_file,
                    "agent",
                    "revoke-token",
                    "--merchant",
                    "seller-a",
                    "--token-prefix",
                    "mai_agent_seller-a_",
                    "--format",
                    "json",
                )
            self.assertIn("ambiguous", str(raised.exception))

    def test_agent_token_command_accepts_ttl_seconds(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            self.run_cli(db_file, "merchant", "create", "--id", "seller-a", "--name", "West Lake Tea")
            issued = json.loads(
                self.run_cli(
                    db_file,
                    "agent",
                    "token",
                    "--merchant",
                    "seller-a",
                    "--ttl-seconds",
                    "3600",
                    "--format",
                    "json",
                )
            )

            self.assertTrue(issued["expires_at"])
            conn = sqlite3.connect(db_file)
            try:
                row = conn.execute(
                    "select expires_at from api_tokens where token = ?",
                    (issued["agent_token"],),
                ).fetchone()
            finally:
                conn.close()
            self.assertEqual(row[0], issued["expires_at"])

    def test_agent_token_command_rejects_non_positive_ttl_seconds(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            self.run_cli(db_file, "merchant", "create", "--id", "seller-a", "--name", "West Lake Tea")
            with self.assertRaises(SystemExit), redirect_stderr(StringIO()):
                self.run_cli(
                    db_file,
                    "agent",
                    "token",
                    "--merchant",
                    "seller-a",
                    "--ttl-seconds",
                    "0",
                    "--format",
                    "json",
                )

    def test_agent_tokens_command_lists_status_without_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            self.run_cli(db_file, "merchant", "create", "--id", "seller-a", "--name", "West Lake Tea")
            expiring = json.loads(
                self.run_cli(
                    db_file,
                    "agent",
                    "token",
                    "--merchant",
                    "seller-a",
                    "--ttl-seconds",
                    "3600",
                    "--format",
                    "json",
                )
            )
            revocable = json.loads(
                self.run_cli(db_file, "agent", "token", "--merchant", "seller-a", "--format", "json")
            )
            revoked = json.loads(
                self.run_cli(
                    db_file,
                    "agent",
                    "revoke-token",
                    "--merchant",
                    "seller-a",
                    "--token",
                    revocable["agent_token"],
                    "--format",
                    "json",
                )
            )
            conn = sqlite3.connect(db_file)
            try:
                conn.execute(
                    "update api_tokens set expires_at = ? where token = ?",
                    ("2000-01-01T00:00:00", expiring["agent_token"]),
                )
                conn.commit()
            finally:
                conn.close()

            output = self.run_cli(db_file, "agent", "tokens", "--merchant", "seller-a", "--format", "json")
            listed = json.loads(output)

            self.assertEqual(len(listed["tokens"]), 2)
            self.assertNotIn(expiring["agent_token"], output)
            self.assertNotIn(revocable["agent_token"], output)
            by_prefix = {token["token_prefix"]: token for token in listed["tokens"]}
            self.assertTrue(by_prefix[expiring["agent_token"][:24]]["expired"])
            self.assertTrue(by_prefix[revocable["agent_token"][:24]]["revoked"])
            self.assertEqual(by_prefix[revocable["agent_token"][:24]]["revoked_at"], revoked["revoked_at"])

    def test_agent_tokens_text_output_is_readable_without_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            self.run_cli(db_file, "merchant", "create", "--id", "seller-a", "--name", "West Lake Tea")
            expiring = json.loads(
                self.run_cli(
                    db_file,
                    "agent",
                    "token",
                    "--merchant",
                    "seller-a",
                    "--ttl-seconds",
                    "3600",
                    "--format",
                    "json",
                )
            )
            revocable = json.loads(
                self.run_cli(db_file, "agent", "token", "--merchant", "seller-a", "--format", "json")
            )
            self.run_cli(
                db_file,
                "agent",
                "revoke-token",
                "--merchant",
                "seller-a",
                "--token",
                revocable["agent_token"],
                "--format",
                "json",
            )

            output = self.run_cli(db_file, "agent", "tokens", "--merchant", "seller-a")

            self.assertIn("TOKEN_PREFIX", output)
            self.assertIn("STATUS", output)
            self.assertIn(expiring["agent_token"][:24], output)
            self.assertIn(revocable["agent_token"][:24], output)
            self.assertIn("active", output)
            self.assertIn("revoked", output)
            self.assertNotIn(expiring["agent_token"], output)
            self.assertNotIn(revocable["agent_token"], output)
            self.assertNotIn('"tokens"', output)

    def test_agent_rotate_token_command_revokes_old_and_issues_new_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            self.run_cli(db_file, "merchant", "create", "--id", "seller-a", "--name", "West Lake Tea")
            issued = json.loads(
                self.run_cli(db_file, "agent", "token", "--merchant", "seller-a", "--format", "json")
            )
            old_token = issued["agent_token"]

            output = self.run_cli(
                db_file,
                "agent",
                "rotate-token",
                "--merchant",
                "seller-a",
                "--token",
                old_token,
                "--ttl-seconds",
                "3600",
                "--format",
                "json",
            )
            rotated = json.loads(output)

            self.assertNotIn(old_token, output)
            self.assertNotEqual(rotated["agent_token"], old_token)
            self.assertTrue(rotated["expires_at"])
            self.assertEqual(rotated["previous_token"]["token_prefix"], old_token[:24])
            conn = sqlite3.connect(db_file)
            try:
                old_row = conn.execute("select revoked_at from api_tokens where token = ?", (old_token,)).fetchone()
                new_row = conn.execute(
                    "select expires_at, revoked_at from api_tokens where token = ?",
                    (rotated["agent_token"],),
                ).fetchone()
            finally:
                conn.close()
            self.assertTrue(old_row[0])
            self.assertEqual(new_row[0], rotated["expires_at"])
            self.assertEqual(new_row[1], "")

    def test_agent_token_cli_lifecycle_records_audit_without_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            self.run_cli(db_file, "merchant", "create", "--id", "seller-a", "--name", "West Lake Tea")
            issued = json.loads(
                self.run_cli(db_file, "agent", "token", "--merchant", "seller-a", "--ttl-seconds", "3600", "--format", "json")
            )
            old_token = issued["agent_token"]
            rotated = json.loads(
                self.run_cli(
                    db_file,
                    "agent",
                    "rotate-token",
                    "--merchant",
                    "seller-a",
                    "--token",
                    old_token,
                    "--ttl-seconds",
                    "7200",
                    "--format",
                    "json",
                )
            )
            new_token = rotated["agent_token"]
            revoked = json.loads(
                self.run_cli(
                    db_file,
                    "agent",
                    "revoke-token",
                    "--merchant",
                    "seller-a",
                    "--token",
                    new_token,
                    "--format",
                    "json",
                )
            )

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

    def test_audit_events_command_filters_merchant_events_without_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            self.run_cli(db_file, "merchant", "create", "--id", "seller-a", "--name", "West Lake Tea")
            issued = json.loads(
                self.run_cli(db_file, "agent", "token", "--merchant", "seller-a", "--format", "json")
            )

            output = self.run_cli(
                db_file,
                "audit",
                "events",
                "--merchant",
                "seller-a",
                "--event",
                "agent_token_issued",
                "--limit",
                "10",
                "--format",
                "json",
            )
            listed = json.loads(output)

            self.assertEqual(len(listed["events"]), 1)
            event = listed["events"][0]
            self.assertEqual(event["actor"], "seller-a")
            self.assertEqual(event["event"], "agent_token_issued")
            self.assertNotIn(issued["agent_token"], output)
            self.assertEqual(event["details"]["token"]["token_prefix"], issued["agent_token"][:24])

    def test_audit_events_text_output_is_readable_without_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            self.run_cli(db_file, "merchant", "create", "--id", "seller-a", "--name", "West Lake Tea")
            issued = json.loads(
                self.run_cli(db_file, "agent", "token", "--merchant", "seller-a", "--format", "json")
            )

            output = self.run_cli(
                db_file,
                "audit",
                "events",
                "--merchant",
                "seller-a",
                "--event",
                "agent_token_issued",
            )

            self.assertIn("ID", output)
            self.assertIn("EVENT", output)
            self.assertIn("ACTOR", output)
            self.assertIn("DETAILS", output)
            self.assertIn("agent_token_issued", output)
            self.assertIn("seller-a", output)
            self.assertIn(issued["agent_token"][:24], output)
            self.assertNotIn(issued["agent_token"], output)
            self.assertNotIn('"events"', output)

    def test_human_review_queue_text_output_is_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            self.run_cli(db_file, "merchant", "create", "--id", "seller-a", "--name", "West Lake Tea")
            self.run_cli(
                db_file,
                "conversation",
                "create",
                "--buyer",
                "alice",
                "--merchant",
                "seller-a",
                "--text",
                "Can I get a private discount?",
            )
            self.run_cli(
                db_file,
                "conversation",
                "human-review",
                "--conversation",
                "CONV-0001",
                "--reason",
                "low_confidence",
                "--severity",
                "urgent",
            )

            output = self.run_cli(db_file, "human-review", "queue", "--merchant", "seller-a")

            self.assertIn("ID", output)
            self.assertIn("CONVERSATION", output)
            self.assertIn("MERCHANT", output)
            self.assertIn("REASON", output)
            self.assertIn("SEVERITY", output)
            self.assertIn("CONV-0001", output)
            self.assertIn("seller-a", output)
            self.assertIn("low_confidence", output)
            self.assertIn("urgent", output)
            self.assertNotIn('"reviews"', output)

    def test_human_review_show_text_output_includes_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            self.run_cli(db_file, "merchant", "create", "--id", "seller-a", "--name", "West Lake Tea")
            self.run_cli(
                db_file,
                "conversation",
                "create",
                "--buyer",
                "alice",
                "--merchant",
                "seller-a",
                "--text",
                "Can I get a private discount?",
            )
            review = json.loads(
                self.run_cli(
                    db_file,
                    "conversation",
                    "human-review",
                    "--conversation",
                    "CONV-0001",
                    "--reason",
                    "low_confidence",
                    "--severity",
                    "urgent",
                    "--format",
                    "json",
                )
            )["review"]

            output = self.run_cli(db_file, "human-review", "show", "--review", str(review["id"]))

            self.assertIn(f"Review {review['id']}", output)
            self.assertIn("Conversation: CONV-0001", output)
            self.assertIn("Merchant: seller-a", output)
            self.assertIn("Buyer: alice", output)
            self.assertIn("Reason: low_confidence", output)
            self.assertIn("Severity: urgent", output)
            self.assertIn("Latest messages:", output)
            self.assertIn("buyer/ask_product", output)
            self.assertIn("Can I get a private discount?", output)
            self.assertNotIn('"conversation"', output)

    def test_human_review_resolve_text_output_summarizes_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            self.run_cli(db_file, "merchant", "create", "--id", "seller-a", "--name", "West Lake Tea")
            self.run_cli(
                db_file,
                "conversation",
                "create",
                "--buyer",
                "alice",
                "--merchant",
                "seller-a",
                "--text",
                "Can I get a private discount?",
            )
            review = json.loads(
                self.run_cli(
                    db_file,
                    "conversation",
                    "human-review",
                    "--conversation",
                    "CONV-0001",
                    "--reason",
                    "low_confidence",
                    "--format",
                    "json",
                )
            )["review"]

            output = self.run_cli(
                db_file,
                "human-review",
                "resolve",
                "--review",
                str(review["id"]),
                "--action",
                "reply",
                "--sender",
                "merchant",
                "--text",
                "Human checked the answer.",
            )

            self.assertIn(f"Review {review['id']} resolved", output)
            self.assertIn("Resolution: reply", output)
            self.assertIn("Conversation: CONV-0001", output)
            self.assertIn("Status: waiting_buyer", output)
            self.assertIn("Next actor: buyer", output)
            self.assertIn("Remaining unresolved reviews: 0", output)
            self.assertNotIn('"review"', output)

    def test_human_review_workbench_shows_and_resolves_one_review_by_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            self.run_cli(db_file, "merchant", "create", "--id", "seller-a", "--name", "West Lake Tea")
            self.run_cli(
                db_file,
                "conversation",
                "create",
                "--buyer",
                "alice",
                "--merchant",
                "seller-a",
                "--text",
                "Can I get a private discount?",
            )
            first = json.loads(
                self.run_cli(
                    db_file,
                    "conversation",
                    "human-review",
                    "--conversation",
                    "CONV-0001",
                    "--reason",
                    "low_confidence",
                    "--format",
                    "json",
                )
            )
            second = json.loads(
                self.run_cli(
                    db_file,
                    "conversation",
                    "human-review",
                    "--conversation",
                    "CONV-0001",
                    "--reason",
                    "suspicious_content",
                    "--format",
                    "json",
                )
            )
            first_review_id = first["review"]["id"]
            second_review_id = second["review"]["id"]

            shown = json.loads(
                self.run_cli(
                    db_file,
                    "human-review",
                    "show",
                    "--review",
                    str(first_review_id),
                    "--format",
                    "json",
                )
            )
            self.assertEqual(shown["review"]["reason"], "low_confidence")
            self.assertEqual(shown["conversation"]["id"], "CONV-0001")

            resolved = json.loads(
                self.run_cli(
                    db_file,
                    "human-review",
                    "resolve",
                    "--review",
                    str(first_review_id),
                    "--action",
                    "reply",
                    "--sender",
                    "merchant",
                    "--text",
                    "Human checked the low-confidence answer.",
                    "--format",
                    "json",
                )
            )
            self.assertIsNotNone(resolved["review"]["resolved_at"])
            self.assertEqual(resolved["review"]["resolution"], "reply")
            self.assertEqual(resolved["conversation"]["status"], "human_required")
            self.assertEqual(resolved["conversation"]["next_actor"], "operator")

            queue = json.loads(self.run_cli(db_file, "human-review", "queue", "--format", "json"))
            self.assertEqual([review["id"] for review in queue["reviews"]], [second_review_id])
            remaining = next(flag for flag in resolved["conversation"]["flags"] if flag["id"] == second_review_id)
            self.assertIsNone(remaining["resolved_at"])

            final = json.loads(
                self.run_cli(
                    db_file,
                    "human-review",
                    "resolve",
                    "--review",
                    str(second_review_id),
                    "--action",
                    "reply",
                    "--sender",
                    "merchant",
                    "--text",
                    "Human checked the bargaining request.",
                    "--format",
                    "json",
                )
            )
            self.assertEqual(final["conversation"]["status"], "waiting_buyer")
            self.assertEqual(final["conversation"]["messages"][-1]["structured_payload"]["review_id"], second_review_id)


if __name__ == "__main__":
    unittest.main()
