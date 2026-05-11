"""Channel ingress helpers for external buyer messages."""

from __future__ import annotations

import sqlite3
from typing import Any

from mai_cli.core.catalog import search_products
from mai_cli.core.conversations import append_message, conversation_summary, ensure_conversation
from mai_cli.core.harness import append_audit_event
from mai_cli.core.risk import infer_intent

MVP_WARNINGS = [
    "MVP records consultation only; no order is created.",
    "No stock is reserved by mai-cli.",
    "Payment, refund, escrow, and delivery-success handling are outside this version.",
]


def channel_buyer_id(channel: str, external_user_id: str) -> str:
    channel = str(channel or "").strip()
    external_user_id = str(external_user_id or "").strip()
    if not channel:
        raise SystemExit("channel is required")
    if not external_user_id:
        raise SystemExit("external_user_id is required")
    return f"{channel}:{external_user_id}"


def _channel_payload(
    channel: str,
    external_user_id: str,
    source_id: str,
    city: str = "",
    area: str = "",
    selected_sku: str = "",
    external_message_id: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "source_id": source_id,
        "channel": channel,
        "external_user_id": external_user_id,
    }
    if city:
        payload["city"] = city
    if area:
        payload["area"] = area
    if selected_sku:
        payload["selected_sku"] = selected_sku
    if external_message_id:
        payload["external_message_id"] = external_message_id
    return payload


def ingest_buyer_message(
    conn: sqlite3.Connection,
    channel: str,
    external_user_id: str,
    text: str,
    city: str = "",
    area: str = "",
    conversation_id: str = "",
    external_message_id: str = "",
    limit: int = 3,
) -> dict[str, Any]:
    channel = str(channel or "").strip()
    external_user_id = str(external_user_id or "").strip()
    text = str(text or "")
    if not text.strip():
        raise SystemExit("text is required")

    resolved_buyer_id = channel_buyer_id(channel, external_user_id)
    source_id = f"channel:{channel}"
    if conversation_id:
        conversation = conversation_summary(conn, conversation_id)
        if conversation["buyer_id"] != resolved_buyer_id:
            raise SystemExit(f"Channel buyer {resolved_buyer_id} cannot write to conversation {conversation_id}")
        message = append_message(
            conn,
            conversation_id,
            "buyer",
            infer_intent(text),
            text,
            structured_payload=_channel_payload(channel, external_user_id, source_id, external_message_id=external_message_id),
        )
        append_audit_event(
            conn,
            conversation_id,
            source_id,
            "channel_message_ingested",
            {"channel": channel, "external_user_id": external_user_id, "message_id": message["id"]},
        )
        return {
            "ok": True,
            "buyer_id": resolved_buyer_id,
            "channel": channel,
            "conversation": conversation_summary(conn, conversation_id),
            "message": message,
            "warnings": MVP_WARNINGS,
        }

    candidates = search_products(conn, query=text, city=city, area=area, limit=limit)
    if not candidates:
        return {
            "ok": True,
            "buyer_id": resolved_buyer_id,
            "channel": channel,
            "candidates": [],
            "conversation": None,
            "warnings": ["No matching merchant or product found.", *MVP_WARNINGS],
            "missing_facts": ["merchant", "product"],
        }

    selected = candidates[0]
    conversation = ensure_conversation(conn, resolved_buyer_id, selected["merchant_id"], selected["sku"])
    message = append_message(
        conn,
        conversation["id"],
        "buyer",
        infer_intent(text),
        text,
        structured_payload=_channel_payload(
            channel,
            external_user_id,
            source_id,
            city=city,
            area=area,
            selected_sku=selected["sku"],
            external_message_id=external_message_id,
        ),
    )
    append_audit_event(
        conn,
        conversation["id"],
        source_id,
        "channel_message_ingested",
        {"channel": channel, "external_user_id": external_user_id, "message_id": message["id"]},
    )
    return {
        "ok": True,
        "buyer_id": resolved_buyer_id,
        "channel": channel,
        "candidates": candidates,
        "selected": selected,
        "conversation": conversation_summary(conn, conversation["id"]),
        "message": message,
        "warnings": MVP_WARNINGS,
    }
