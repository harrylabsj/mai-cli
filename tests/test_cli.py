import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
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
