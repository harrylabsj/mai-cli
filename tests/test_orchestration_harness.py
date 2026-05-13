import sqlite3
import tempfile
import unittest
from pathlib import Path

from mai_cli.agents import buyer_cli, merchant_agent
from mai_cli.core.catalog import create_merchant, create_product
from mai_cli.core.conversations import conversation_summary
from mai_cli.core.harness import abandon_agent_message, abandon_stale_agent_messages, claim_agent_message, complete_agent_message, fail_agent_message
from mai_cli.db.session import db_session, decode_json


class OrchestrationHarnessTest(unittest.TestCase):
    def seed_conversation(self, db_file: Path) -> None:
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
            buyer_cli.ask(conn, "alice", "longjing gift delivery today", city="Hangzhou")

    def test_harness_records_next_actor_idempotency_and_audit_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            self.seed_conversation(db_file)

            with db_session(db_file) as conn:
                conversation = conversation_summary(conn, "CONV-0001")
                self.assertEqual(conversation["status"], "waiting_merchant")
                self.assertEqual(conversation["next_actor"], "merchant_agent")
                self.assertTrue(
                    any(event["event"] == "message_appended" and event["actor"] == "buyer" for event in conversation["audit_events"])
                )

                first = merchant_agent.process_once(conn, "seller-a")
                self.assertEqual(first["replied"][0]["conversation_id"], "CONV-0001")

                updated = conversation_summary(conn, "CONV-0001")
                self.assertEqual(updated["status"], "waiting_buyer")
                self.assertEqual(updated["next_actor"], "buyer")

                process = conn.execute(
                    "select * from agent_message_processes where agent_id = ? and message_id = ?",
                    ("mai-cli-merchant-agent:seller-a", 1),
                ).fetchone()
                self.assertIsNotNone(process)
                self.assertEqual(process["status"], "processed")
                self.assertEqual(process["attempts"], 1)
                self.assertEqual(process["idempotency_key"], "mai-cli-merchant-agent:seller-a:1")

                agent_reply = updated["messages"][-1]
                self.assertEqual(agent_reply["structured_payload"]["processed_message_id"], 1)
                self.assertEqual(agent_reply["structured_payload"]["idempotency_key"], "mai-cli-merchant-agent:seller-a:1")
                self.assertTrue(
                    any(event["event"] == "agent_message_processed" for event in updated["audit_events"])
                )

    def test_processing_claim_is_not_reclaimed(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            self.seed_conversation(db_file)

            with db_session(db_file) as conn:
                first = claim_agent_message(conn, "merchant-agent", "CONV-0001", 1, "merchant-agent:1")
                second = claim_agent_message(conn, "merchant-agent", "CONV-0001", 1, "merchant-agent:1")

                self.assertTrue(first["claimed"])
                self.assertFalse(second["claimed"])
                self.assertEqual(second["status"], "processing")

                process = conn.execute(
                    """
                    select attempts, status from agent_message_processes
                    where agent_id = ? and message_id = ?
                    """,
                    ("merchant-agent", 1),
                ).fetchone()
                self.assertEqual(int(process["attempts"]), 1)
                self.assertEqual(process["status"], "processing")

    def test_processing_claim_tolerates_corrupt_attempts_counter(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            self.seed_conversation(db_file)

            with db_session(db_file) as conn:
                claim_agent_message(conn, "merchant-agent", "CONV-0001", 1, "merchant-agent:1")
                conn.execute(
                    """
                    update agent_message_processes
                    set attempts = 'bad'
                    where agent_id = ? and message_id = ?
                    """,
                    ("merchant-agent", 1),
                )

                second = claim_agent_message(conn, "merchant-agent", "CONV-0001", 1, "merchant-agent:1")

                self.assertFalse(second["claimed"])
                self.assertEqual(second["status"], "processing")
                self.assertEqual(second["attempts"], 0)

    def test_processing_claim_tolerates_non_finite_attempts_counter(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            self.seed_conversation(db_file)

            with db_session(db_file) as conn:
                claim_agent_message(conn, "merchant-agent", "CONV-0001", 1, "merchant-agent:1")
                conn.execute(
                    """
                    update agent_message_processes
                    set attempts = ?
                    where agent_id = ? and message_id = ?
                    """,
                    (float("inf"), "merchant-agent", 1),
                )

                second = claim_agent_message(conn, "merchant-agent", "CONV-0001", 1, "merchant-agent:1")

                self.assertFalse(second["claimed"])
                self.assertEqual(second["status"], "processing")
                self.assertEqual(second["attempts"], 0)

    def test_abandoned_processing_claim_can_be_retried_explicitly(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            self.seed_conversation(db_file)

            with db_session(db_file) as conn:
                first = claim_agent_message(conn, "merchant-agent", "CONV-0001", 1, "merchant-agent:1")
                abandoned = abandon_agent_message(conn, "merchant-agent", 1, "worker stopped before reply")
                second = claim_agent_message(conn, "merchant-agent", "CONV-0001", 1, "merchant-agent:1")

                self.assertTrue(first["claimed"])
                self.assertEqual(abandoned["status"], "abandoned")
                self.assertEqual(abandoned["last_error"], "worker stopped before reply")
                self.assertTrue(second["claimed"])
                self.assertEqual(second["attempts"], 2)

                process = conn.execute(
                    """
                    select attempts, status, last_error from agent_message_processes
                    where agent_id = ? and message_id = ?
                    """,
                    ("merchant-agent", 1),
                ).fetchone()
                self.assertEqual(int(process["attempts"]), 2)
                self.assertEqual(process["status"], "processing")
                self.assertEqual(process["last_error"], "")

                events = conversation_summary(conn, "CONV-0001")["audit_events"]
                self.assertTrue(any(event["event"] == "agent_message_abandoned" for event in events))

    def test_completed_or_failed_claims_are_not_rewritten_by_invalid_transitions(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            self.seed_conversation(db_file)

            with db_session(db_file) as conn:
                claim_agent_message(conn, "merchant-agent", "CONV-0001", 1, "merchant-agent:1")
                fail_agent_message(conn, "merchant-agent", 1, "temporary failure")
                complete_after_failed = complete_agent_message(conn, "merchant-agent", 1)
                retry = claim_agent_message(conn, "merchant-agent", "CONV-0001", 1, "merchant-agent:1")
                complete_agent_message(conn, "merchant-agent", 1)
                failed_after_processed = fail_agent_message(conn, "merchant-agent", 1, "late failure")

                self.assertEqual(complete_after_failed["status"], "failed")
                self.assertTrue(retry["claimed"])
                self.assertEqual(failed_after_processed["status"], "processed")

                process = conn.execute(
                    """
                    select attempts, status, last_error from agent_message_processes
                    where agent_id = ? and message_id = ?
                    """,
                    ("merchant-agent", 1),
                ).fetchone()
                self.assertEqual(int(process["attempts"]), 2)
                self.assertEqual(process["status"], "processed")
                self.assertEqual(process["last_error"], "")

    def test_stale_processing_claims_are_abandoned_and_retryable(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            self.seed_conversation(db_file)

            with db_session(db_file) as conn:
                claim_agent_message(conn, "merchant-agent", "CONV-0001", 1, "merchant-agent:1")
                conn.execute(
                    """
                    update agent_message_processes
                    set updated_at = '2026-05-11T00:00:00'
                    where agent_id = ? and message_id = ?
                    """,
                    ("merchant-agent", 1),
                )
                abandoned = abandon_stale_agent_messages(
                    conn,
                    "merchant-agent",
                    stale_after_seconds=60,
                    now="2026-05-11T00:02:01",
                )
                retry = claim_agent_message(conn, "merchant-agent", "CONV-0001", 1, "merchant-agent:1")

                self.assertEqual(len(abandoned), 1)
                self.assertEqual(abandoned[0]["status"], "abandoned")
                self.assertIn("stale processing claim", abandoned[0]["last_error"])
                self.assertTrue(retry["claimed"])
                self.assertEqual(retry["attempts"], 2)

                events = conversation_summary(conn, "CONV-0001")["audit_events"]
                self.assertTrue(
                    any(
                        event["event"] == "agent_message_abandoned"
                        and event["details"]["reason"] == "stale_processing_claim"
                        for event in events
                    )
                )

    def test_fresh_processing_claims_are_not_abandoned_by_ttl_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            self.seed_conversation(db_file)

            with db_session(db_file) as conn:
                claim_agent_message(conn, "merchant-agent", "CONV-0001", 1, "merchant-agent:1")
                conn.execute(
                    """
                    update agent_message_processes
                    set updated_at = '2026-05-11T00:01:30'
                    where agent_id = ? and message_id = ?
                    """,
                    ("merchant-agent", 1),
                )
                abandoned = abandon_stale_agent_messages(
                    conn,
                    "merchant-agent",
                    stale_after_seconds=60,
                    now="2026-05-11T00:02:01",
                )

                self.assertEqual(abandoned, [])
                process = conn.execute(
                    """
                    select attempts, status from agent_message_processes
                    where agent_id = ? and message_id = ?
                    """,
                    ("merchant-agent", 1),
                ).fetchone()
                self.assertEqual(int(process["attempts"]), 1)
                self.assertEqual(process["status"], "processing")

    def test_stale_processing_claim_recovery_tolerates_invalid_ttl(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            self.seed_conversation(db_file)

            with db_session(db_file) as conn:
                claim_agent_message(conn, "merchant-agent", "CONV-0001", 1, "merchant-agent:1")
                conn.execute(
                    """
                    update agent_message_processes
                    set updated_at = '2026-05-11T00:00:00'
                    where agent_id = ? and message_id = ?
                    """,
                    ("merchant-agent", 1),
                )

                try:
                    abandoned = abandon_stale_agent_messages(
                        conn,
                        "merchant-agent",
                        stale_after_seconds="bad",
                        now="2026-05-11T00:10:01",
                    )
                except ValueError as exc:
                    self.fail(f"stale claim recovery should tolerate invalid ttl values: {exc}")

                self.assertEqual(len(abandoned), 1)
                self.assertIn("300 seconds", abandoned[0]["last_error"])

    def test_schema_migration_adds_harness_tables_to_existing_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "old.sqlite"
            conn = sqlite3.connect(db_file)
            try:
                conn.execute(
                    """
                    create table conversations (
                        id text primary key,
                        buyer_id text not null,
                        merchant_id text not null,
                        sku text not null default '',
                        status text not null,
                        created_at text not null,
                        updated_at text not null,
                        last_sender text not null default ''
                    )
                    """
                )
                conn.commit()
            finally:
                conn.close()

            with db_session(db_file) as conn:
                conversation_columns = {row["name"] for row in conn.execute("pragma table_info(conversations)").fetchall()}
                tables = {row["name"] for row in conn.execute("select name from sqlite_master where type = 'table'")}

            self.assertIn("next_actor", conversation_columns)
            self.assertIn("audit_events", tables)
            self.assertIn("agent_message_processes", tables)

    def test_suspicious_conversation_routes_to_operator_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            with db_session(db_file) as conn:
                create_merchant(conn, merchant_id="seller-a", name="West Lake Tea", city="Hangzhou", service_area="West Lake")
                create_product(
                    conn,
                    merchant_id="seller-a",
                    sku="tea-a",
                    title="Longjing",
                    price=88,
                    stock=5,
                    tags=["longjing"],
                )
                buyer_cli.ask(conn, "alice", "Can you help with fake id and longjing?", city="Hangzhou")

                result = merchant_agent.process_once(conn, "seller-a")
                self.assertEqual(result["replied"][0]["reason"], "suspicious_content")

                conversation = conversation_summary(conn, "CONV-0001")
                self.assertEqual(conversation["status"], "human_required")
                self.assertEqual(conversation["next_actor"], "operator")
                self.assertTrue(
                    any(
                        event["event"] == "human_review_flagged"
                        and event["details"]["reason"] == "suspicious_content"
                        and event["details"]["next_actor"] == "operator"
                        for event in conversation["audit_events"]
                    )
                )
