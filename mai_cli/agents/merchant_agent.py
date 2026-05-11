"""Resident deterministic merchant agent."""

from __future__ import annotations

import sqlite3
from typing import Any

from mai_cli import VERSION
from mai_cli.agents.buyer_cli import MVP_WARNINGS
from mai_cli.agents.tools import DEFAULT_CAPABILITIES, MerchantAgentTools, SQLiteMerchantAgentTools, record_heartbeat
from mai_cli.core.harness import message_idempotency_key
from mai_cli.core.risk import human_review_reason


def heartbeat(
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
    return record_heartbeat(
        conn,
        merchant_id,
        status=status,
        capabilities=capabilities,
        pid=pid,
        version=version,
        last_error=last_error,
        checked_count=checked_count,
        replied_count=replied_count,
    )


def latest_buyer_message(conversation: dict[str, Any]) -> dict[str, Any] | None:
    for message in reversed(conversation.get("messages", [])):
        if message["sender"] == "buyer":
            return message
    return None


def has_agent_reply_after(conversation: dict[str, Any], buyer_message: dict[str, Any]) -> bool:
    for message in conversation.get("messages", []):
        if message["id"] <= buyer_message["id"]:
            continue
        if message["sender"] in {"merchant_agent", "merchant"}:
            return True
    return False


def generate_reply(
    tools: MerchantAgentTools,
    conversation: dict[str, Any],
    buyer_message: dict[str, Any],
) -> tuple[str, bool, str]:
    product = tools.product_summary(conversation["sku"]) if conversation.get("sku") else None
    reason = human_review_reason(buyer_message["text"], product_found=product is not None)
    disclaimer = " ".join(MVP_WARNINGS)
    if product is None:
        return f"I need a merchant human to confirm which product this consultation refers to. {disclaimer}", True, reason
    delivery = product["delivery"]
    if not reason and int(product["stock"]) <= 2:
        reason = "low_stock"
    if not reason and buyer_message["intent"] == "ask_delivery" and not delivery.get("service_area"):
        reason = "unclear_delivery"
    if reason:
        return (
            f"{product['title']} is listed at {product['price']:.2f} {product['currency']} with "
            f"{product['stock']} in stock. This request needs merchant human review because: {reason}. {disclaimer}",
            True,
            reason,
        )
    delivery_text = "delivery rule is missing"
    if delivery.get("service_area"):
        delivery_text = (
            f"delivery area {delivery['service_area']}, ETA {delivery['eta_minutes']} minutes, "
            f"fee {delivery['fee']:.2f} {delivery['currency']}"
        )
    return (
        f"{product['title']} has stock {product['stock']} and current price "
        f"{product['price']:.2f} {product['currency']}; {delivery_text}. {disclaimer}",
        False,
        "",
    )


def process_once_with_tools(tools: MerchantAgentTools, merchant_id: str) -> dict[str, Any]:
    agent = tools.heartbeat(merchant_id)
    conversations = tools.waiting_merchant_conversations(merchant_id)
    replied: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for conversation in conversations:
        buyer_message = latest_buyer_message(conversation)
        if buyer_message is None or has_agent_reply_after(conversation, buyer_message):
            continue
        idempotency_key = message_idempotency_key(agent["id"], int(buyer_message["id"]))
        claim = tools.claim_message(agent["id"], conversation["id"], int(buyer_message["id"]), idempotency_key)
        if not claim.get("claimed"):
            continue
        try:
            reply, needs_human, reason = generate_reply(tools, conversation, buyer_message)
            status = "human_required" if needs_human else "waiting_buyer"
            message = tools.append_message(
                conversation["id"],
                "merchant_agent",
                buyer_message["intent"],
                reply,
                structured_payload={
                    "human_required": needs_human,
                    "reason": reason,
                    "source_id": agent["id"],
                    "processed_message_id": int(buyer_message["id"]),
                    "idempotency_key": idempotency_key,
                },
                status=status,
            )
            tools.complete_message(agent["id"], int(buyer_message["id"]))
            flags = []
            if needs_human:
                flags.append(tools.add_flag(conversation["id"], reason or "human_required", sku=conversation.get("sku", "")))
                tools.heartbeat(merchant_id, status="human_required")
            replied.append(
                {
                    "conversation_id": conversation["id"],
                    "message_id": message["id"],
                    "human_required": needs_human,
                    "reason": reason,
                    "flags": flags,
                }
            )
        except Exception as exc:  # pragma: no cover - exercised through fake tools
            error = f"{type(exc).__name__}: {exc}"
            tools.fail_message(agent["id"], int(buyer_message["id"]), error)
            tools.heartbeat(
                merchant_id,
                status="online",
                last_error=error,
                checked_count=len(conversations),
                replied_count=len(replied),
            )
            failed.append({"conversation_id": conversation["id"], "message_id": int(buyer_message["id"]), "error": error})
    return {
        "ok": True,
        "merchant_id": merchant_id,
        "agent": agent,
        "checked": len(conversations),
        "replied": replied,
        "failed": failed,
    }


def process_once(conn: sqlite3.Connection, merchant_id: str) -> dict[str, Any]:
    return process_once_with_tools(SQLiteMerchantAgentTools(conn), merchant_id)
