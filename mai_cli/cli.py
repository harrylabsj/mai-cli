"""Argparse CLI for the standalone mai-cli MVP."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from mai_cli import VERSION
from mai_cli.adapters.mai_legacy import import_json_store
from mai_cli.agents import buyer_cli, merchant_agent, merchant_daemon
from mai_cli.api.app import create_app
from mai_cli.config import DEFAULT_DB_PATH
from mai_cli.core.catalog import (
    create_merchant,
    create_product,
    merchant_summary,
    search_merchants,
    search_products,
    set_stock,
    update_merchant,
    update_product,
    upsert_delivery_rule,
)
from mai_cli.core.channels import ingest_buyer_message
from mai_cli.core.conversations import merchant_conversations
from mai_cli.core.conversations import add_flag, append_message, conversation_summary, ensure_conversation
from mai_cli.core.harness import append_audit_event, next_actor_for_status
from mai_cli.core.risk import infer_intent
from mai_cli.db.session import db_session, decode_json, now_iso


def emit_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def emit(value: Any, fmt: str) -> None:
    if fmt == "json":
        emit_json(value)
    else:
        if isinstance(value, dict) and "message" in value:
            print(value["message"])
        else:
            print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def db_path_from_args(args: argparse.Namespace) -> Path:
    return Path(getattr(args, "agent_db", None) or args.db or args.data or DEFAULT_DB_PATH).expanduser()


def cmd_merchant_create(args: argparse.Namespace) -> None:
    with db_session(db_path_from_args(args)) as conn:
        merchant = create_merchant(
            conn,
            merchant_id=args.id,
            name=args.name,
            city=args.city or "",
            service_area=args.service_area or "",
            contact=args.contact or "",
            hours=args.hours or "",
            automation_boundaries=args.automation_boundaries or "",
            tags=args.tags or "",
            delivery_fee=args.delivery_fee,
            delivery_eta_minutes=args.delivery_eta_minutes,
            delivery_radius_km=args.delivery_radius_km,
        )
    emit({"ok": True, "merchant": merchant, "message": f"Merchant created: {args.id}"}, args.format)


def cmd_merchant_list(args: argparse.Namespace) -> None:
    with db_session(db_path_from_args(args)) as conn:
        rows = conn.execute("select id from merchants order by name, id").fetchall()
        merchants = [merchant_summary(conn, row["id"]) for row in rows]
    emit({"ok": True, "results": merchants}, args.format)


def cmd_merchant_update(args: argparse.Namespace) -> None:
    with db_session(db_path_from_args(args)) as conn:
        merchant = update_merchant(
            conn,
            merchant_id=args.id,
            name=args.name,
            city=args.city,
            service_area=args.service_area,
            contact=args.contact,
            hours=args.hours,
            automation_boundaries=args.automation_boundaries,
            tags=args.tags,
            delivery_fee=args.delivery_fee,
            delivery_eta_minutes=args.delivery_eta_minutes,
            delivery_radius_km=args.delivery_radius_km,
        )
    emit({"ok": True, "merchant": merchant, "message": f"Merchant updated: {args.id}"}, args.format)


def cmd_merchant_human_review(args: argparse.Namespace) -> None:
    with db_session(db_path_from_args(args)) as conn:
        conversations = merchant_conversations(conn, args.merchant, "human_required")
    emit({"ok": True, "merchant_id": args.merchant, "conversations": conversations}, args.format)


def cmd_delivery_set(args: argparse.Namespace) -> None:
    with db_session(db_path_from_args(args)) as conn:
        delivery = upsert_delivery_rule(
            conn,
            args.merchant,
            service_area=args.service_area or "",
            fee=args.fee,
            eta_minutes=args.eta_minutes,
            radius_km=args.radius_km,
            notes=args.notes or "",
        )
    emit({"ok": True, "merchant_id": args.merchant, "delivery": delivery}, args.format)


def cmd_product_add(args: argparse.Namespace) -> None:
    with db_session(db_path_from_args(args)) as conn:
        product = create_product(
            conn,
            merchant_id=args.merchant,
            sku=args.sku,
            title=args.title,
            price=args.price,
            stock=args.stock,
            currency=args.currency,
            category=args.category or "",
            tags=args.tags or "",
            description=args.description or "",
            delivery_attributes=args.delivery_attributes or "",
        )
    emit({"ok": True, "product": product, "message": f"Product added: {args.sku}"}, args.format)


def cmd_product_stock(args: argparse.Namespace) -> None:
    with db_session(db_path_from_args(args)) as conn:
        product = set_stock(conn, args.sku, args.stock, args.merchant or "")
    emit({"ok": True, "product": product, "message": f"Stock set: {args.sku} -> {args.stock}"}, args.format)


def cmd_product_update(args: argparse.Namespace) -> None:
    with db_session(db_path_from_args(args)) as conn:
        product = update_product(
            conn,
            sku=args.sku,
            merchant_id=args.merchant or "",
            title=args.title,
            price=args.price,
            stock=args.stock,
            currency=args.currency,
            category=args.category,
            tags=args.tags,
            description=args.description,
            delivery_attributes=args.delivery_attributes,
        )
    emit({"ok": True, "product": product, "message": f"Product updated: {args.sku}"}, args.format)


def cmd_search_products(args: argparse.Namespace) -> None:
    with db_session(db_path_from_args(args)) as conn:
        results = search_products(
            conn,
            query=args.query or "",
            city=args.city or "",
            area=args.area or "",
            max_price=args.max_price,
            include_out_of_stock=args.include_out_of_stock,
        )
    emit({"ok": True, "query": args.query or "", "results": results}, args.format)


def cmd_search_merchants(args: argparse.Namespace) -> None:
    with db_session(db_path_from_args(args)) as conn:
        results = search_merchants(conn, query=args.query or "", city=args.city or "")
    emit({"ok": True, "query": args.query or "", "results": results}, args.format)


def cmd_buyer_ask(args: argparse.Namespace) -> None:
    with db_session(db_path_from_args(args)) as conn:
        result = buyer_cli.ask(conn, args.buyer, args.text, city=args.city or "", area=args.area or "")
    emit(result, args.format)


def cmd_channel_ingest(args: argparse.Namespace) -> None:
    with db_session(db_path_from_args(args)) as conn:
        result = ingest_buyer_message(
            conn,
            channel=args.channel,
            external_user_id=args.external_user,
            text=args.text,
            city=args.city or "",
            area=args.area or "",
            conversation_id=args.conversation or "",
            external_message_id=args.external_message_id or "",
        )
    emit(result, args.format)


def cmd_buyer_summarize(args: argparse.Namespace) -> None:
    with db_session(db_path_from_args(args)) as conn:
        result = buyer_cli.summarize(conn, args.conversation)
    emit(result, args.format)


def cmd_buyer_intent(args: argparse.Namespace) -> None:
    with db_session(db_path_from_args(args)) as conn:
        result = buyer_cli.record_intent(conn, args.conversation, args.intent, args.text)
    emit(result, args.format)


def emit_chat_event(payload: dict[str, Any], fmt: str) -> None:
    if fmt == "json":
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    if payload.get("ok") is False:
        print(f"error: {payload.get('error')}")
        return
    event = payload.get("event")
    conversation = payload.get("conversation") or payload.get("summary", {}).get("conversation") or {}
    conversation_id = conversation.get("id", "")
    status = conversation.get("status", "")
    next_actor = conversation.get("next_actor", "")
    detail = f" {conversation_id}" if conversation_id else ""
    state = f" status={status} next_actor={next_actor}" if status else ""
    print(f"{event}{detail}{state}".strip())


def cmd_buyer_chat(args: argparse.Namespace) -> None:
    db_path = db_path_from_args(args)
    conversation_id = args.conversation or ""
    for raw_line in sys.stdin:
        text = raw_line.strip()
        if not text:
            continue
        if text in {"/quit", "/exit"}:
            emit_chat_event({"ok": True, "event": "quit"}, args.format)
            break
        if text == "/summary":
            if not conversation_id:
                emit_chat_event({"ok": False, "event": "error", "error": "No active conversation."}, args.format)
                continue
            with db_session(db_path) as conn:
                summary = buyer_cli.summarize(conn, conversation_id)
            emit_chat_event({"ok": True, "event": "summary", "summary": summary}, args.format)
            continue
        if text == "/history":
            if not conversation_id:
                emit_chat_event({"ok": False, "event": "error", "error": "No active conversation."}, args.format)
                continue
            with db_session(db_path) as conn:
                conversation = conversation_summary(conn, conversation_id)
            emit_chat_event(
                {"ok": True, "event": "history", "conversation": conversation, "messages": conversation["messages"]},
                args.format,
            )
            continue
        if text.startswith("/intent "):
            if not conversation_id:
                emit_chat_event({"ok": False, "event": "error", "error": "No active conversation."}, args.format)
                continue
            parts = text.split(" ", 2)
            if len(parts) < 3 or parts[1] not in {"purchase_intent", "quote_request"}:
                emit_chat_event(
                    {"ok": False, "event": "error", "error": "Use /intent purchase_intent <text> or /intent quote_request <text>."},
                    args.format,
                )
                continue
            with db_session(db_path) as conn:
                message = append_message(
                    conn,
                    conversation_id,
                    "buyer",
                    parts[1],
                    parts[2],
                    structured_payload={"source_id": "buyer-chat"},
                )
                conversation = conversation_summary(conn, conversation_id)
            emit_chat_event({"ok": True, "event": "intent", "message": message, "conversation": conversation}, args.format)
            continue
        if conversation_id:
            with db_session(db_path) as conn:
                message = append_message(
                    conn,
                    conversation_id,
                    "buyer",
                    infer_intent(text),
                    text,
                    structured_payload={"source_id": "buyer-chat", "city": args.city or "", "area": args.area or ""},
                )
                conversation = conversation_summary(conn, conversation_id)
            emit_chat_event({"ok": True, "event": "message", "message": message, "conversation": conversation}, args.format)
            continue
        with db_session(db_path) as conn:
            result = buyer_cli.ask(conn, args.buyer, text, city=args.city or "", area=args.area or "")
        if result.get("conversation"):
            conversation_id = result["conversation"]["id"]
        result = dict(result)
        result["event"] = "ask"
        emit_chat_event(result, args.format)


def cmd_conversation_create(args: argparse.Namespace) -> None:
    with db_session(db_path_from_args(args)) as conn:
        conversation = ensure_conversation(conn, args.buyer, args.merchant, args.sku or "")
        if args.text:
            append_message(
                conn,
                conversation["id"],
                "buyer",
                args.intent,
                args.text,
                structured_payload={"source_id": args.source_id or "buyer-cli"},
            )
            conversation = conversation_summary(conn, conversation["id"])
    emit({"ok": True, "conversation": conversation}, args.format)


def cmd_conversation_show(args: argparse.Namespace) -> None:
    with db_session(db_path_from_args(args)) as conn:
        conversation = conversation_summary(conn, args.conversation)
    emit({"ok": True, "conversation": conversation}, args.format)


def cmd_conversation_list(args: argparse.Namespace) -> None:
    clauses: list[str] = []
    values: list[Any] = []
    for column, value in (
        ("status", args.status),
        ("merchant_id", args.merchant),
        ("buyer_id", args.buyer),
        ("sku", args.sku),
    ):
        if value:
            clauses.append(f"{column} = ?")
            values.append(value)
    sql = "select id from conversations"
    if clauses:
        sql += " where " + " and ".join(clauses)
    if args.updated_since:
        clauses.append("updated_at >= ?")
        values.append(args.updated_since)
        sql = "select id from conversations where " + " and ".join(clauses)
    sql += " order by updated_at desc"
    with db_session(db_path_from_args(args)) as conn:
        rows = conn.execute(sql, values).fetchall()
        conversations = [conversation_summary(conn, row["id"]) for row in rows]
    emit({"ok": True, "conversations": conversations}, args.format)


def cmd_conversation_message(args: argparse.Namespace) -> None:
    structured_payload = {"source_id": args.source_id or args.sender}
    with db_session(db_path_from_args(args)) as conn:
        message = append_message(
            conn,
            args.conversation,
            args.sender,
            args.intent,
            args.text,
            structured_payload=structured_payload,
            status=args.status,
        )
        conversation = conversation_summary(conn, args.conversation)
    emit({"ok": True, "message": message, "conversation": conversation}, args.format)


def cmd_conversation_close(args: argparse.Namespace) -> None:
    with db_session(db_path_from_args(args)) as conn:
        if args.text:
            append_message(
                conn,
                args.conversation,
                args.sender,
                args.intent,
                args.text,
                structured_payload={"source_id": args.source_id or args.sender},
                status="closed",
            )
        else:
            next_actor = next_actor_for_status("closed")
            conn.execute(
                "update conversations set status = 'closed', next_actor = ?, updated_at = ?, last_sender = ? where id = ?",
                (next_actor, now_iso(), args.sender, args.conversation),
            )
            append_audit_event(conn, args.conversation, args.sender, "conversation_closed", {"next_actor": next_actor})
        conversation = conversation_summary(conn, args.conversation)
    emit({"ok": True, "conversation": conversation}, args.format)


def _review_summary(conn: Any, flag_id: int) -> dict[str, Any]:
    row = conn.execute("select * from moderation_flags where id = ?", (flag_id,)).fetchone()
    conversation = conversation_summary(conn, row["conversation_id"])
    return {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        "merchant_id": conversation["merchant_id"],
        "buyer_id": conversation["buyer_id"],
        "sku": row["sku"],
        "reason": row["reason"],
        "severity": row["severity"],
        "created_at": row["created_at"],
        "resolved_at": row["resolved_at"] or None,
        "resolution": row["resolution"],
        "resolved_by": row["resolved_by"],
    }


def cmd_conversation_human_review(args: argparse.Namespace) -> None:
    with db_session(db_path_from_args(args)) as conn:
        conversation = conversation_summary(conn, args.conversation)
        flag = add_flag(conn, args.conversation, args.reason, severity=args.severity, sku=conversation.get("sku") or "")
        next_actor = next_actor_for_status("human_required", args.reason)
        conn.execute(
            "update conversations set status = 'human_required', next_actor = ?, updated_at = ?, last_sender = ? where id = ?",
            (next_actor, now_iso(), args.source_id or "operator", args.conversation),
        )
        append_audit_event(
            conn,
            args.conversation,
            args.source_id or "operator",
            "conversation_routed",
            {"status": "human_required", "next_actor": next_actor, "reason": args.reason},
        )
        review = _review_summary(conn, flag["id"])
        conversation = conversation_summary(conn, args.conversation)
    emit({"ok": True, "review": review, "conversation": conversation}, args.format)


def cmd_conversation_resolve_review(args: argparse.Namespace) -> None:
    status = "closed" if args.action == "close" else "waiting_buyer"
    with db_session(db_path_from_args(args)) as conn:
        now = now_iso()
        conn.execute(
            """
            update moderation_flags
            set resolved_at = ?, resolution = ?, resolved_by = ?
            where conversation_id = ? and resolved_at = ''
            """,
            (now, args.action, args.sender, args.conversation),
        )
        if args.text:
            append_message(
                conn,
                args.conversation,
                args.sender,
                args.intent,
                args.text,
                structured_payload={"source_id": args.source_id or args.sender, "resolution": args.action},
                status=status,
            )
        else:
            next_actor = next_actor_for_status(status)
            conn.execute(
                "update conversations set status = ?, next_actor = ?, updated_at = ?, last_sender = ? where id = ?",
                (status, next_actor, now, args.sender, args.conversation),
            )
            append_audit_event(
                conn,
                args.conversation,
                args.sender,
                "human_review_resolved",
                {"resolution": args.action, "status": status, "next_actor": next_actor},
            )
        rows = conn.execute("select id from moderation_flags where conversation_id = ? order by id", (args.conversation,)).fetchall()
        reviews = [_review_summary(conn, row["id"]) for row in rows]
        conversation = conversation_summary(conn, args.conversation)
    emit({"ok": True, "reviews": reviews, "conversation": conversation}, args.format)


def cmd_agent_run(args: argparse.Namespace) -> None:
    if args.once:
        with db_session(db_path_from_args(args)) as conn:
            result = merchant_agent.process_once(conn, args.merchant)
        emit(result, args.format)
        return
    merchant_daemon.run_forever(
        db_path_from_args(args),
        args.merchant,
        interval=args.interval,
        state_file=args.state_file,
        stop_file=args.stop_file,
    )


def cmd_agent_start(args: argparse.Namespace) -> None:
    result = merchant_daemon.start_agent(
        db_path_from_args(args),
        args.merchant,
        interval=args.interval,
        state_dir=args.state_dir,
    )
    emit(result, args.format)


def cmd_agent_stop(args: argparse.Namespace) -> None:
    result = merchant_daemon.stop_agent(
        db_path_from_args(args),
        args.merchant,
        state_dir=args.state_dir,
        timeout=args.timeout,
    )
    emit(result, args.format)


def cmd_agent_status(args: argparse.Namespace) -> None:
    result = merchant_daemon.status_agent(db_path_from_args(args), args.merchant, state_dir=args.state_dir)
    emit(result, args.format)


def cmd_agent_logs(args: argparse.Namespace) -> None:
    result = merchant_daemon.logs_agent(args.merchant, tail=args.tail, state_dir=args.state_dir)
    emit(result, args.format)


def cmd_agent_heartbeat(args: argparse.Namespace) -> None:
    with db_session(db_path_from_args(args)) as conn:
        result = merchant_agent.heartbeat(conn, args.merchant, args.status)
    emit({"ok": True, "agent": result}, args.format)


def _agent_summary(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "type": row["type"],
        "owner_id": row["owner_id"],
        "status": row["status"],
        "capabilities": decode_json(row["capabilities_json"], []),
        "last_seen_at": row["last_seen_at"],
        "pid": int(row["pid"] or 0),
        "version": row["version"],
        "last_error": row["last_error"],
        "checked_count": int(row["checked_count"] or 0),
        "replied_count": int(row["replied_count"] or 0),
    }


def cmd_agent_list(args: argparse.Namespace) -> None:
    with db_session(db_path_from_args(args)) as conn:
        if args.merchant:
            rows = conn.execute("select * from agents where owner_id = ? order by id", (args.merchant,)).fetchall()
        else:
            rows = conn.execute("select * from agents order by id").fetchall()
        agents = [_agent_summary(row) for row in rows]
    emit({"ok": True, "agents": agents}, args.format)


def cmd_agent_show(args: argparse.Namespace) -> None:
    with db_session(db_path_from_args(args)) as conn:
        row = conn.execute("select * from agents where id = ?", (args.agent,)).fetchone()
        if row is None:
            raise SystemExit(f"Unknown agent: {args.agent}")
        agent = _agent_summary(row)
    emit({"ok": True, "agent": agent}, args.format)


def cmd_human_review_queue(args: argparse.Namespace) -> None:
    sql = """
        select f.id from moderation_flags f
        join conversations c on c.id = f.conversation_id
        where f.resolved_at = ''
    """
    values: list[Any] = []
    if args.merchant:
        sql += " and c.merchant_id = ?"
        values.append(args.merchant)
    sql += " order by f.created_at desc, f.id desc"
    with db_session(db_path_from_args(args)) as conn:
        rows = conn.execute(sql, values).fetchall()
        reviews = [_review_summary(conn, row["id"]) for row in rows]
    emit({"ok": True, "reviews": reviews}, args.format)


def cmd_legacy_import(args: argparse.Namespace) -> None:
    with db_session(db_path_from_args(args)) as conn:
        result = import_json_store(conn, args.from_json)
    emit(result, args.format)


def cmd_api_routes(args: argparse.Namespace) -> None:
    app = create_app(db_path_from_args(args))
    routes = sorted({route.path for route in getattr(app, "routes", []) if hasattr(route, "path")})
    emit(
        {
            "ok": True,
            "title": getattr(app, "title", "mai-cli Marketplace API"),
            "fastapi_available": bool(getattr(getattr(app, "state", None), "fastapi_available", False)),
            "routes": routes,
        },
        args.format,
    )


def cmd_api_serve(args: argparse.Namespace) -> None:
    try:
        import uvicorn
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency environment specific
        raise SystemExit("uvicorn is required to serve the FastAPI app. Install mai-cli[api].") from exc
    app = create_app(db_path_from_args(args))
    uvicorn.run(app, host=args.host, port=args.port)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="mai-cli local commerce consultation runtime.", add_help=True)
    parser.add_argument("--db", help=f"SQLite database path. Default: {DEFAULT_DB_PATH}")
    parser.add_argument("--data", help=argparse.SUPPRESS)
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_agent_runtime_options(command_parser: argparse.ArgumentParser, include_db: bool = True) -> None:
        if include_db:
            command_parser.add_argument("--db", dest="agent_db", help="SQLite database path")
        command_parser.add_argument("--state-dir", default=None, help=argparse.SUPPRESS)

    merchant = subparsers.add_parser("merchant", help="Manage merchant profiles and review queues")
    merchant_sub = merchant.add_subparsers(dest="merchant_command", required=True)
    merchant_create = merchant_sub.add_parser("create", help="Create a merchant profile and delivery rule")
    merchant_create.add_argument("--id", required=True)
    merchant_create.add_argument("--name", required=True)
    merchant_create.add_argument("--city", default="")
    merchant_create.add_argument("--service-area", default="")
    merchant_create.add_argument("--contact", default="")
    merchant_create.add_argument("--hours", default="")
    merchant_create.add_argument("--automation-boundaries", default="")
    merchant_create.add_argument("--tags", default="")
    merchant_create.add_argument("--delivery-fee", type=float, default=0)
    merchant_create.add_argument("--delivery-eta-minutes", type=int, default=0)
    merchant_create.add_argument("--delivery-radius-km", type=float, default=0)
    merchant_create.add_argument("--format", choices=["text", "json"], default="text")
    merchant_create.set_defaults(func=cmd_merchant_create)
    merchant_list = merchant_sub.add_parser("list", help="List merchants")
    merchant_list.add_argument("--format", choices=["text", "json"], default="text")
    merchant_list.set_defaults(func=cmd_merchant_list)
    merchant_update = merchant_sub.add_parser("update", help="Update a merchant profile and delivery rule")
    merchant_update.add_argument("--id", required=True)
    merchant_update.add_argument("--name")
    merchant_update.add_argument("--city")
    merchant_update.add_argument("--service-area")
    merchant_update.add_argument("--contact")
    merchant_update.add_argument("--hours")
    merchant_update.add_argument("--automation-boundaries")
    merchant_update.add_argument("--tags")
    merchant_update.add_argument("--delivery-fee", type=float)
    merchant_update.add_argument("--delivery-eta-minutes", type=int)
    merchant_update.add_argument("--delivery-radius-km", type=float)
    merchant_update.add_argument("--format", choices=["text", "json"], default="text")
    merchant_update.set_defaults(func=cmd_merchant_update)
    human_review = merchant_sub.add_parser("human-review", help="View conversations requiring merchant human review")
    human_review.add_argument("--merchant", required=True)
    human_review.add_argument("--format", choices=["text", "json"], default="text")
    human_review.set_defaults(func=cmd_merchant_human_review)

    delivery = subparsers.add_parser("delivery", help="Configure merchant delivery rules")
    delivery_sub = delivery.add_subparsers(dest="delivery_command", required=True)
    delivery_set = delivery_sub.add_parser("set", help="Create or update a delivery rule")
    delivery_set.add_argument("--merchant", required=True)
    delivery_set.add_argument("--service-area", default="")
    delivery_set.add_argument("--fee", type=float, default=0)
    delivery_set.add_argument("--eta-minutes", type=int, default=0)
    delivery_set.add_argument("--radius-km", type=float, default=0)
    delivery_set.add_argument("--notes", default="")
    delivery_set.add_argument("--format", choices=["text", "json"], default="text")
    delivery_set.set_defaults(func=cmd_delivery_set)

    product = subparsers.add_parser("product", help="Manage products and stock")
    product_sub = product.add_subparsers(dest="product_command", required=True)
    product_add = product_sub.add_parser("add", help="Publish a product")
    product_add.add_argument("--merchant", required=True)
    product_add.add_argument("--sku", required=True)
    product_add.add_argument("--title", required=True)
    product_add.add_argument("--price", required=True, type=float)
    product_add.add_argument("--stock", required=True, type=int)
    product_add.add_argument("--currency", default="CNY")
    product_add.add_argument("--category", default="")
    product_add.add_argument("--tags", default="")
    product_add.add_argument("--description", default="")
    product_add.add_argument("--delivery-attributes", default="")
    product_add.add_argument("--format", choices=["text", "json"], default="text")
    product_add.set_defaults(func=cmd_product_add)
    product_stock = product_sub.add_parser("stock", help="Set product stock")
    product_stock.add_argument("--sku", required=True)
    product_stock.add_argument("--merchant", default="")
    product_stock.add_argument("--stock", required=True, type=int)
    product_stock.add_argument("--format", choices=["text", "json"], default="text")
    product_stock.set_defaults(func=cmd_product_stock)
    product_update = product_sub.add_parser("update", help="Update product catalog fields or stock")
    product_update.add_argument("--sku", required=True)
    product_update.add_argument("--merchant", default="")
    product_update.add_argument("--title")
    product_update.add_argument("--price", type=float)
    product_update.add_argument("--stock", type=int)
    product_update.add_argument("--currency")
    product_update.add_argument("--category")
    product_update.add_argument("--tags")
    product_update.add_argument("--description")
    product_update.add_argument("--delivery-attributes")
    product_update.add_argument("--format", choices=["text", "json"], default="text")
    product_update.set_defaults(func=cmd_product_update)

    search = subparsers.add_parser("search", help="Search marketplace inventory")
    search_sub = search.add_subparsers(dest="search_command", required=True)
    search_products_parser = search_sub.add_parser("products", help="Search products")
    search_products_parser.add_argument("--query", default="")
    search_products_parser.add_argument("--city", default="")
    search_products_parser.add_argument("--area", default="")
    search_products_parser.add_argument("--max-price", type=float)
    search_products_parser.add_argument("--include-out-of-stock", action="store_true")
    search_products_parser.add_argument("--format", choices=["text", "json"], default="text")
    search_products_parser.set_defaults(func=cmd_search_products)
    search_merchants_parser = search_sub.add_parser("merchants", help="Search merchants")
    search_merchants_parser.add_argument("--query", default="")
    search_merchants_parser.add_argument("--city", default="")
    search_merchants_parser.add_argument("--format", choices=["text", "json"], default="text")
    search_merchants_parser.set_defaults(func=cmd_search_merchants)

    channel = subparsers.add_parser("channel", help="Ingest external channel messages")
    channel_sub = channel.add_subparsers(dest="channel_command", required=True)
    channel_ingest = channel_sub.add_parser("ingest", help="Ingest an external buyer message")
    channel_ingest.add_argument("--channel", required=True)
    channel_ingest.add_argument("--external-user", required=True)
    channel_ingest.add_argument("--text", required=True)
    channel_ingest.add_argument("--conversation", default="")
    channel_ingest.add_argument("--city", default="")
    channel_ingest.add_argument("--area", default="")
    channel_ingest.add_argument("--external-message-id", default="")
    channel_ingest.add_argument("--format", choices=["text", "json"], default="text")
    channel_ingest.set_defaults(func=cmd_channel_ingest)

    buyer = subparsers.add_parser("buyer", help="Buyer consultation commands")
    buyer_sub = buyer.add_subparsers(dest="buyer_command", required=True)
    buyer_ask = buyer_sub.add_parser("ask", help="Search and open a merchant consultation")
    buyer_ask.add_argument("--buyer", required=True)
    buyer_ask.add_argument("--text", required=True)
    buyer_ask.add_argument("--city", default="")
    buyer_ask.add_argument("--area", default="")
    buyer_ask.add_argument("--format", choices=["text", "json"], default="text")
    buyer_ask.set_defaults(func=cmd_buyer_ask)
    buyer_summary = buyer_sub.add_parser("summarize", help="Summarize a consultation")
    buyer_summary.add_argument("--conversation", required=True)
    buyer_summary.add_argument("--format", choices=["text", "json"], default="text")
    buyer_summary.set_defaults(func=cmd_buyer_summarize)
    buyer_intent = buyer_sub.add_parser("intent", help="Record quote_request or purchase_intent as a message")
    buyer_intent.add_argument("--conversation", required=True)
    buyer_intent.add_argument("--intent", required=True, choices=["quote_request", "purchase_intent"])
    buyer_intent.add_argument("--text", required=True)
    buyer_intent.add_argument("--format", choices=["text", "json"], default="text")
    buyer_intent.set_defaults(func=cmd_buyer_intent)
    buyer_chat = buyer_sub.add_parser(
        "chat",
        help="Run a lightweight buyer chat REPL from stdin",
        description="Run a lightweight buyer chat REPL from stdin",
    )
    buyer_chat.add_argument("--buyer", required=True)
    buyer_chat.add_argument("--conversation", default="")
    buyer_chat.add_argument("--city", default="")
    buyer_chat.add_argument("--area", default="")
    buyer_chat.add_argument("--format", choices=["text", "json"], default="text")
    buyer_chat.set_defaults(func=cmd_buyer_chat)

    conversation = subparsers.add_parser("conversation", help="Manage consultations and messages")
    conversation_sub = conversation.add_subparsers(dest="conversation_command", required=True)
    conversation_create = conversation_sub.add_parser("create", help="Create a conversation and optional buyer message")
    conversation_create.add_argument("--buyer", required=True)
    conversation_create.add_argument("--merchant", required=True)
    conversation_create.add_argument("--sku", default="")
    conversation_create.add_argument("--intent", default="ask_product")
    conversation_create.add_argument("--text", default="")
    conversation_create.add_argument("--source-id", default="buyer-cli")
    conversation_create.add_argument("--format", choices=["text", "json"], default="text")
    conversation_create.set_defaults(func=cmd_conversation_create)
    conversation_show = conversation_sub.add_parser("show", help="Show one conversation")
    conversation_show.add_argument("--conversation", required=True)
    conversation_show.add_argument("--format", choices=["text", "json"], default="text")
    conversation_show.set_defaults(func=cmd_conversation_show)
    conversation_list = conversation_sub.add_parser("list", help="List conversations with simple filters")
    conversation_list.add_argument("--buyer", default="")
    conversation_list.add_argument("--merchant", default="")
    conversation_list.add_argument("--status", default="")
    conversation_list.add_argument("--sku", default="")
    conversation_list.add_argument("--updated-since", default="")
    conversation_list.add_argument("--format", choices=["text", "json"], default="text")
    conversation_list.set_defaults(func=cmd_conversation_list)
    conversation_message = conversation_sub.add_parser("message", help="Append a message to a conversation")
    conversation_message.add_argument("--conversation", required=True)
    conversation_message.add_argument("--sender", required=True, choices=["buyer", "buyer_cli", "merchant_agent", "merchant", "operator"])
    conversation_message.add_argument("--intent", required=True)
    conversation_message.add_argument("--text", required=True)
    conversation_message.add_argument("--status")
    conversation_message.add_argument("--source-id", default="")
    conversation_message.add_argument("--format", choices=["text", "json"], default="text")
    conversation_message.set_defaults(func=cmd_conversation_message)
    conversation_close = conversation_sub.add_parser("close", help="Close a conversation")
    conversation_close.add_argument("--conversation", required=True)
    conversation_close.add_argument("--sender", default="operator")
    conversation_close.add_argument("--intent", default="support")
    conversation_close.add_argument("--text", default="")
    conversation_close.add_argument("--source-id", default="")
    conversation_close.add_argument("--format", choices=["text", "json"], default="text")
    conversation_close.set_defaults(func=cmd_conversation_close)
    conversation_review = conversation_sub.add_parser("human-review", help="Mark a conversation for human review")
    conversation_review.add_argument("--conversation", required=True)
    conversation_review.add_argument("--reason", required=True)
    conversation_review.add_argument("--severity", default="review")
    conversation_review.add_argument("--source-id", default="operator")
    conversation_review.add_argument("--format", choices=["text", "json"], default="text")
    conversation_review.set_defaults(func=cmd_conversation_human_review)
    conversation_resolve = conversation_sub.add_parser("resolve-review", help="Resolve human-review flags")
    conversation_resolve.add_argument("--conversation", required=True)
    conversation_resolve.add_argument("--action", required=True, choices=["reply", "approve_public_answer", "reject", "close"])
    conversation_resolve.add_argument("--sender", default="merchant")
    conversation_resolve.add_argument("--intent", default="support")
    conversation_resolve.add_argument("--text", default="")
    conversation_resolve.add_argument("--source-id", default="")
    conversation_resolve.add_argument("--format", choices=["text", "json"], default="text")
    conversation_resolve.set_defaults(func=cmd_conversation_resolve_review)

    agent = subparsers.add_parser("agent", help="Run resident merchant agents")
    agent_sub = agent.add_subparsers(dest="agent_command", required=True)
    agent_start = agent_sub.add_parser("start", help="Start a background merchant agent daemon")
    agent_start.add_argument("--merchant", required=True)
    agent_start.add_argument("--interval", type=float, default=3.0)
    agent_start.add_argument("--format", choices=["text", "json"], default="text")
    add_agent_runtime_options(agent_start)
    agent_start.set_defaults(func=cmd_agent_start)
    agent_stop = agent_sub.add_parser("stop", help="Stop a background merchant agent daemon")
    agent_stop.add_argument("--merchant", required=True)
    agent_stop.add_argument("--timeout", type=float, default=5.0)
    agent_stop.add_argument("--format", choices=["text", "json"], default="text")
    add_agent_runtime_options(agent_stop)
    agent_stop.set_defaults(func=cmd_agent_stop)
    agent_status = agent_sub.add_parser("status", help="Show merchant agent daemon status")
    agent_status.add_argument("--merchant", required=True)
    agent_status.add_argument("--format", choices=["text", "json"], default="text")
    add_agent_runtime_options(agent_status)
    agent_status.set_defaults(func=cmd_agent_status)
    agent_logs = agent_sub.add_parser("logs", help="Show merchant agent daemon logs")
    agent_logs.add_argument("--merchant", required=True)
    agent_logs.add_argument("--tail", type=int, default=20)
    agent_logs.add_argument("--format", choices=["text", "json"], default="text")
    add_agent_runtime_options(agent_logs, include_db=False)
    agent_logs.set_defaults(func=cmd_agent_logs)
    agent_list = agent_sub.add_parser("list", help="List marketplace agent heartbeats")
    agent_list.add_argument("--merchant", default="")
    agent_list.add_argument("--format", choices=["text", "json"], default="text")
    agent_list.set_defaults(func=cmd_agent_list)
    agent_show = agent_sub.add_parser("show", help="Show one marketplace agent heartbeat")
    agent_show.add_argument("--agent", required=True)
    agent_show.add_argument("--format", choices=["text", "json"], default="text")
    agent_show.set_defaults(func=cmd_agent_show)
    agent_run = agent_sub.add_parser("run", help="Poll and answer waiting merchant conversations")
    agent_run.add_argument("--merchant", required=True)
    agent_run.add_argument("--once", action="store_true")
    agent_run.add_argument("--interval", type=float, default=3.0)
    agent_run.add_argument("--format", choices=["text", "json"], default="text")
    agent_run.add_argument("--state-file", default=None, help=argparse.SUPPRESS)
    agent_run.add_argument("--stop-file", default=None, help=argparse.SUPPRESS)
    add_agent_runtime_options(agent_run)
    agent_run.set_defaults(func=cmd_agent_run)
    agent_heartbeat = agent_sub.add_parser("heartbeat", help="Record merchant agent health")
    agent_heartbeat.add_argument("--merchant", required=True)
    agent_heartbeat.add_argument("--status", choices=["online", "away", "human_required"], default="online")
    agent_heartbeat.add_argument("--format", choices=["text", "json"], default="text")
    add_agent_runtime_options(agent_heartbeat)
    agent_heartbeat.set_defaults(func=cmd_agent_heartbeat)

    human_review_cli = subparsers.add_parser("human-review", help="Review flagged conversations")
    human_review_sub = human_review_cli.add_subparsers(dest="human_review_command", required=True)
    human_review_queue = human_review_sub.add_parser("queue", help="List unresolved human-review flags")
    human_review_queue.add_argument("--merchant", default="")
    human_review_queue.add_argument("--format", choices=["text", "json"], default="text")
    human_review_queue.set_defaults(func=cmd_human_review_queue)

    legacy = subparsers.add_parser("legacy", help="Import existing Mai catalog data")
    legacy_sub = legacy.add_subparsers(dest="legacy_command", required=True)
    legacy_import = legacy_sub.add_parser("import", help="Import merchants and products from a legacy JSON store")
    legacy_import.add_argument("--from-json", required=True)
    legacy_import.add_argument("--format", choices=["text", "json"], default="text")
    legacy_import.set_defaults(func=cmd_legacy_import)

    api = subparsers.add_parser("api", help="Inspect or run the marketplace API")
    api_sub = api.add_subparsers(dest="api_command", required=True)
    api_routes = api_sub.add_parser("routes", help="List marketplace API routes")
    api_routes.add_argument("--format", choices=["text", "json"], default="text")
    api_routes.set_defaults(func=cmd_api_routes)
    api_serve = api_sub.add_parser("serve", help="Serve the FastAPI marketplace API")
    api_serve.add_argument("--host", default="127.0.0.1")
    api_serve.add_argument("--port", type=int, default=8765)
    api_serve.set_defaults(func=cmd_api_serve)
    return parser


def _is_top_level_help(args_list: list[str]) -> bool:
    if not any(arg in {"-h", "--help"} for arg in args_list):
        return False
    remaining: list[str] = []
    skip_next = False
    for arg in args_list:
        if skip_next:
            skip_next = False
            continue
        if arg == "--db":
            skip_next = True
            continue
        if arg.startswith("--db=") or arg in {"-h", "--help"}:
            continue
        remaining.append(arg)
    return not remaining


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args_list = list(sys.argv[1:] if argv is None else argv)
    if _is_top_level_help(args_list):
        parser.print_help()
        return
    args = parser.parse_args(args_list)
    args.func(args)


if __name__ == "__main__":
    main()
