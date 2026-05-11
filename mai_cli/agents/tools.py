"""Typed marketplace tools used by resident merchant agents."""

from __future__ import annotations

import sqlite3
from typing import Any, Protocol

from mai_cli import VERSION
from mai_cli.core.catalog import product_summary, require_merchant
from mai_cli.core.conversations import add_flag, append_message, waiting_merchant_conversations
from mai_cli.core.harness import claim_agent_message, complete_agent_message, fail_agent_message
from mai_cli.db.session import encode_json, now_iso

DEFAULT_CAPABILITIES = ["catalog", "inventory", "delivery", "consultation"]


class MerchantAgentTools(Protocol):
    def heartbeat(
        self,
        merchant_id: str,
        status: str = "online",
        last_error: str = "",
        checked_count: int = 0,
        replied_count: int = 0,
    ) -> dict[str, Any]:
        ...

    def waiting_merchant_conversations(self, merchant_id: str) -> list[dict[str, Any]]:
        ...

    def product_summary(self, sku: str) -> dict[str, Any]:
        ...

    def append_message(
        self,
        conversation_id: str,
        sender: str,
        intent: str,
        text: str,
        structured_payload: dict[str, Any],
        status: str,
    ) -> dict[str, Any]:
        ...

    def add_flag(self, conversation_id: str, reason: str, sku: str = "") -> dict[str, Any]:
        ...

    def claim_message(self, agent_id: str, conversation_id: str, message_id: int, idempotency_key: str) -> dict[str, Any]:
        ...

    def complete_message(self, agent_id: str, message_id: int) -> dict[str, Any]:
        ...

    def fail_message(self, agent_id: str, message_id: int, error: str) -> dict[str, Any]:
        ...


def record_heartbeat(
    conn: sqlite3.Connection,
    merchant_id: str,
    status: str = "online",
    capabilities: list[str] | None = None,
    pid: int = 0,
    version: str = VERSION,
    last_error: str = "",
    checked_count: int = 0,
    replied_count: int = 0,
) -> dict[str, Any]:
    require_merchant(conn, merchant_id)
    agent_id = f"mai-cli-merchant-agent:{merchant_id}"
    now = now_iso()
    conn.execute(
        """
        insert into agents(
            id, type, owner_id, status, capabilities_json, last_seen_at,
            pid, version, last_error, checked_count, replied_count
        )
        values (?, 'merchant', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(id) do update set
            status = excluded.status,
            capabilities_json = excluded.capabilities_json,
            last_seen_at = excluded.last_seen_at,
            pid = excluded.pid,
            version = excluded.version,
            last_error = excluded.last_error,
            checked_count = excluded.checked_count,
            replied_count = excluded.replied_count
        """,
        (
            agent_id,
            merchant_id,
            status,
            encode_json(capabilities or DEFAULT_CAPABILITIES),
            now,
            int(pid or 0),
            version or VERSION,
            last_error or "",
            int(checked_count or 0),
            int(replied_count or 0),
        ),
    )
    return {
        "id": agent_id,
        "type": "merchant",
        "owner_id": merchant_id,
        "status": status,
        "capabilities": capabilities or DEFAULT_CAPABILITIES,
        "last_seen_at": now,
        "pid": int(pid or 0),
        "version": version or VERSION,
        "last_error": last_error or "",
        "checked_count": int(checked_count or 0),
        "replied_count": int(replied_count or 0),
    }


class SQLiteMerchantAgentTools:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def heartbeat(
        self,
        merchant_id: str,
        status: str = "online",
        last_error: str = "",
        checked_count: int = 0,
        replied_count: int = 0,
    ) -> dict[str, Any]:
        return record_heartbeat(
            self.conn,
            merchant_id,
            status=status,
            last_error=last_error,
            checked_count=checked_count,
            replied_count=replied_count,
        )

    def waiting_merchant_conversations(self, merchant_id: str) -> list[dict[str, Any]]:
        return waiting_merchant_conversations(self.conn, merchant_id)

    def product_summary(self, sku: str) -> dict[str, Any]:
        return product_summary(self.conn, sku)

    def append_message(
        self,
        conversation_id: str,
        sender: str,
        intent: str,
        text: str,
        structured_payload: dict[str, Any],
        status: str,
    ) -> dict[str, Any]:
        return append_message(
            self.conn,
            conversation_id,
            sender,
            intent,
            text,
            structured_payload=structured_payload,
            status=status,
        )

    def add_flag(self, conversation_id: str, reason: str, sku: str = "") -> dict[str, Any]:
        return add_flag(self.conn, conversation_id, reason, sku=sku)

    def claim_message(self, agent_id: str, conversation_id: str, message_id: int, idempotency_key: str) -> dict[str, Any]:
        return claim_agent_message(self.conn, agent_id, conversation_id, message_id, idempotency_key)

    def complete_message(self, agent_id: str, message_id: int) -> dict[str, Any]:
        return complete_agent_message(self.conn, agent_id, message_id)

    def fail_message(self, agent_id: str, message_id: int, error: str) -> dict[str, Any]:
        return fail_agent_message(self.conn, agent_id, message_id, error)
