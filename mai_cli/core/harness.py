"""MVP orchestration helpers for routing, idempotency, and audit events."""

from __future__ import annotations

import sqlite3
from typing import Any

from mai_cli.db.session import decode_json, encode_json, now_iso


def next_actor_for_review_reason(reason: str) -> str:
    if reason == "suspicious_content":
        return "operator"
    return "merchant_human"


def next_actor_for_status(status: str, reason: str = "") -> str:
    if status == "human_required":
        return next_actor_for_review_reason(reason)
    return {
        "open": "buyer",
        "waiting_merchant": "merchant_agent",
        "waiting_buyer": "buyer",
        "closed": "",
    }.get(status, "")


def append_audit_event(
    conn: sqlite3.Connection,
    conversation_id: str,
    actor: str,
    event: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cursor = conn.execute(
        """
        insert into audit_events(conversation_id, actor, event, details_json, created_at)
        values (?, ?, ?, ?, ?)
        """,
        (conversation_id, actor, event, encode_json(details or {}), now_iso()),
    )
    return audit_event_summary(conn, int(cursor.lastrowid))


def audit_event_summary(conn: sqlite3.Connection, event_id: int) -> dict[str, Any]:
    row = conn.execute("select * from audit_events where id = ?", (event_id,)).fetchone()
    if row is None:
        raise SystemExit(f"Unknown audit event: {event_id}")
    return {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        "actor": row["actor"],
        "event": row["event"],
        "details": decode_json(row["details_json"], {}),
        "created_at": row["created_at"],
    }


def conversation_audit_events(conn: sqlite3.Connection, conversation_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "select id from audit_events where conversation_id = ? order by id",
        (conversation_id,),
    ).fetchall()
    return [audit_event_summary(conn, row["id"]) for row in rows]


def message_idempotency_key(agent_id: str, message_id: int) -> str:
    return f"{agent_id}:{message_id}"


def claim_agent_message(
    conn: sqlite3.Connection,
    agent_id: str,
    conversation_id: str,
    message_id: int,
    idempotency_key: str,
) -> dict[str, Any]:
    now = now_iso()
    row = conn.execute(
        "select * from agent_message_processes where agent_id = ? and message_id = ?",
        (agent_id, message_id),
    ).fetchone()
    if row is not None and row["status"] != "failed":
        return {
            "claimed": False,
            "status": row["status"],
            "attempts": int(row["attempts"] or 0),
            "idempotency_key": row["idempotency_key"],
        }

    if row is None:
        attempts = 1
        try:
            conn.execute(
                """
                insert into agent_message_processes(
                    agent_id, message_id, conversation_id, idempotency_key, status,
                    attempts, last_error, created_at, updated_at, processed_at
                )
                values (?, ?, ?, ?, 'processing', ?, '', ?, ?, '')
                """,
                (agent_id, message_id, conversation_id, idempotency_key, attempts, now, now),
            )
        except sqlite3.IntegrityError:
            current = agent_message_process_summary(conn, agent_id, message_id)
            return {
                "claimed": False,
                "status": current["status"],
                "attempts": current["attempts"],
                "idempotency_key": current["idempotency_key"],
            }
    else:
        attempts = int(row["attempts"] or 0) + 1
        cursor = conn.execute(
            """
            update agent_message_processes
            set conversation_id = ?,
                idempotency_key = ?,
                status = 'processing',
                attempts = attempts + 1,
                last_error = '',
                updated_at = ?,
                processed_at = ''
            where agent_id = ? and message_id = ? and status = 'failed'
            """,
            (conversation_id, idempotency_key, now, agent_id, message_id),
        )
        if cursor.rowcount != 1:
            current = agent_message_process_summary(conn, agent_id, message_id)
            return {
                "claimed": False,
                "status": current["status"],
                "attempts": current["attempts"],
                "idempotency_key": current["idempotency_key"],
            }
    append_audit_event(
        conn,
        conversation_id,
        agent_id,
        "agent_message_claimed",
        {"message_id": message_id, "idempotency_key": idempotency_key, "attempts": attempts},
    )
    return {"claimed": True, "status": "processing", "attempts": attempts, "idempotency_key": idempotency_key}


def complete_agent_message(conn: sqlite3.Connection, agent_id: str, message_id: int) -> dict[str, Any]:
    now = now_iso()
    conn.execute(
        """
        update agent_message_processes
        set status = 'processed', last_error = '', updated_at = ?, processed_at = ?
        where agent_id = ? and message_id = ?
        """,
        (now, now, agent_id, message_id),
    )
    process = agent_message_process_summary(conn, agent_id, message_id)
    append_audit_event(
        conn,
        process["conversation_id"],
        agent_id,
        "agent_message_processed",
        {"message_id": message_id, "idempotency_key": process["idempotency_key"], "attempts": process["attempts"]},
    )
    return process


def fail_agent_message(conn: sqlite3.Connection, agent_id: str, message_id: int, error: str) -> dict[str, Any]:
    now = now_iso()
    conn.execute(
        """
        update agent_message_processes
        set status = 'failed', last_error = ?, updated_at = ?
        where agent_id = ? and message_id = ?
        """,
        (error, now, agent_id, message_id),
    )
    process = agent_message_process_summary(conn, agent_id, message_id)
    append_audit_event(
        conn,
        process["conversation_id"],
        agent_id,
        "agent_message_failed",
        {"message_id": message_id, "idempotency_key": process["idempotency_key"], "attempts": process["attempts"], "error": error},
    )
    return process


def agent_message_process_summary(conn: sqlite3.Connection, agent_id: str, message_id: int) -> dict[str, Any]:
    row = conn.execute(
        "select * from agent_message_processes where agent_id = ? and message_id = ?",
        (agent_id, message_id),
    ).fetchone()
    if row is None:
        raise SystemExit(f"Unknown agent message process: {agent_id} {message_id}")
    return {
        "agent_id": row["agent_id"],
        "message_id": row["message_id"],
        "conversation_id": row["conversation_id"],
        "idempotency_key": row["idempotency_key"],
        "status": row["status"],
        "attempts": int(row["attempts"] or 0),
        "last_error": row["last_error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "processed_at": row["processed_at"],
    }
