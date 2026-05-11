"""Marketplace API app factory.

FastAPI is used when installed. The lightweight fallback keeps route metadata
available for local tests in environments where optional API dependencies have
not been installed yet.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs

from mai_cli import VERSION
from mai_cli.agents import buyer_cli, merchant_agent
from mai_cli.config import agent_stale_ttl_seconds_from
from mai_cli.core import catalog
from mai_cli.core.conversations import add_flag, append_message, conversation_summary, ensure_conversation, merchant_conversations
from mai_cli.core.harness import append_audit_event, next_actor_for_status
from mai_cli.db.session import db_session, decode_json, now_iso

try:  # pragma: no cover - exercised when optional dependency is installed
    from fastapi import FastAPI
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse
except ModuleNotFoundError:  # pragma: no cover - local CI currently has no fastapi
    FastAPI = None  # type: ignore[assignment]
    JSONResponse = None  # type: ignore[assignment]
    RequestValidationError = None  # type: ignore[assignment]


class AuthError(Exception):
    pass


def _json_error_response(status_code: int, error: str) -> Any:
    payload = {"ok": False, "error": error}
    if JSONResponse is not None:  # pragma: no cover - exercised with fastapi installed
        return JSONResponse(status_code=status_code, content=payload)
    return SimpleNamespace(status_code=status_code, body=json.dumps(payload, ensure_ascii=False).encode("utf-8"))


class RouteInfo:
    def __init__(self, path: str, methods: set[str]):
        self.path = path
        self.methods = methods


class MarketplaceASGIApp:
    title = "mai-cli Marketplace API"

    def __init__(self, db_path: str | Path):
        self.state = SimpleNamespace(db_path=str(db_path), fastapi_available=False)
        self.routes = route_info()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await send({"type": "http.response.start", "status": 404, "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": b'{"ok":false,"error":"unsupported scope"}'})
            return
        chunks: list[bytes] = []
        while True:
            message = await receive()
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        try:
            payload = json.loads(b"".join(chunks).decode("utf-8") or "{}")
        except json.JSONDecodeError:
            payload = {}
        headers = {
            key.decode("latin1").lower(): value.decode("latin1")
            for key, value in scope.get("headers", [])
        }
        authorization = headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            payload["_auth_token"] = authorization.split(" ", 1)[1].strip()
        query = parse_qs(scope.get("query_string", b"").decode("utf-8"), keep_blank_values=True)
        status, response = handle_request(
            self.state.db_path,
            method=str(scope.get("method") or "GET").upper(),
            path=str(scope.get("path") or "/"),
            payload=payload,
            query={key: values[-1] if values else "" for key, values in query.items()},
        )
        body = json.dumps(response, ensure_ascii=False, sort_keys=True).encode("utf-8")
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": body})


def route_info() -> list[RouteInfo]:
    return [
        RouteInfo("/health", {"GET"}),
        RouteInfo("/merchants", {"GET", "POST"}),
        RouteInfo("/merchants/{merchant_id}", {"GET", "PATCH"}),
        RouteInfo("/products", {"POST"}),
        RouteInfo("/products/{sku}", {"GET", "PATCH"}),
        RouteInfo("/search/products", {"GET"}),
        RouteInfo("/search/merchants", {"GET"}),
        RouteInfo("/buyer/ask", {"POST"}),
        RouteInfo("/conversations", {"POST"}),
        RouteInfo("/conversations/{conversation_id}", {"GET"}),
        RouteInfo("/conversations/{conversation_id}/messages", {"POST"}),
        RouteInfo("/conversations/{conversation_id}/close", {"POST"}),
        RouteInfo("/buyers/{buyer_id}/conversations", {"GET"}),
        RouteInfo("/agents/heartbeat", {"POST"}),
        RouteInfo("/agents", {"GET"}),
        RouteInfo("/agents/{agent_id}", {"GET"}),
        RouteInfo("/merchants/{merchant_id}/agents", {"GET"}),
        RouteInfo("/human-review/queue", {"GET"}),
        RouteInfo("/merchants/{merchant_id}/conversations", {"GET"}),
        RouteInfo("/merchants/{merchant_id}/human-review", {"GET"}),
        RouteInfo("/conversations/{conversation_id}/human-review", {"POST"}),
        RouteInfo("/conversations/{conversation_id}/human-review/resolve", {"POST"}),
    ]


def _float_or_none(value: Any) -> float | None:
    return None if value is None else float(value)


def _int_or_none(value: Any) -> int | None:
    return None if value is None else int(value)


def _merchant_list(conn: Any) -> list[dict[str, Any]]:
    rows = conn.execute("select id from merchants order by name, id").fetchall()
    return [catalog.merchant_summary(conn, row["id"]) for row in rows]


def _payload_token(payload: dict[str, Any]) -> str:
    return str(payload.get("merchant_token") or payload.get("_auth_token") or "")


def _issue_merchant_token(conn: Any, merchant_id: str) -> str:
    token = f"mai_{merchant_id}_{secrets.token_urlsafe(18)}"
    conn.execute(
        """
        insert into api_tokens(token, role, merchant_id, buyer_id, created_at)
        values (?, 'merchant', ?, '', ?)
        """,
        (token, merchant_id, now_iso()),
    )
    return token


def _require_merchant_token(conn: Any, merchant_id: str, payload: dict[str, Any]) -> None:
    token = _payload_token(payload)
    if not token:
        raise AuthError("merchant token required")
    row = conn.execute(
        "select role, merchant_id from api_tokens where token = ?",
        (token,),
    ).fetchone()
    if row is None or row["role"] != "merchant" or row["merchant_id"] != merchant_id:
        raise AuthError("invalid merchant token")


def _health(db_path: str | Path) -> dict[str, Any]:
    with db_session(db_path):
        return {"ok": True, "service": "mai-cli-marketplace", "version": VERSION, "storage": "sqlite"}


def _create_merchant(db_path: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    with db_session(db_path) as conn:
        merchant = catalog.create_merchant(
            conn,
            merchant_id=str(payload["id"]),
            name=str(payload["name"]),
            city=str(payload.get("city") or ""),
            service_area=str(payload.get("service_area") or ""),
            contact=str(payload.get("contact") or ""),
            hours=str(payload.get("hours") or ""),
            automation_boundaries=str(payload.get("automation_boundaries") or ""),
            tags=payload.get("tags") or [],
            delivery_fee=float(payload.get("delivery_fee") or 0),
            delivery_eta_minutes=int(payload.get("delivery_eta_minutes") or 0),
            delivery_radius_km=float(payload.get("delivery_radius_km") or 0),
        )
        token = _issue_merchant_token(conn, merchant["id"])
        return {"ok": True, "merchant": merchant, "merchant_token": token}


def _update_merchant(db_path: str | Path, merchant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    with db_session(db_path) as conn:
        _require_merchant_token(conn, merchant_id, payload)
        merchant = catalog.update_merchant(
            conn,
            merchant_id=merchant_id,
            name=payload.get("name"),
            city=payload.get("city"),
            service_area=payload.get("service_area"),
            contact=payload.get("contact"),
            hours=payload.get("hours"),
            automation_boundaries=payload.get("automation_boundaries"),
            tags=payload.get("tags") if "tags" in payload else None,
            delivery_fee=_float_or_none(payload.get("delivery_fee")),
            delivery_eta_minutes=_int_or_none(payload.get("delivery_eta_minutes")),
            delivery_radius_km=_float_or_none(payload.get("delivery_radius_km")),
        )
        return {"ok": True, "merchant": merchant}


def _get_merchant(db_path: str | Path, merchant_id: str) -> dict[str, Any]:
    with db_session(db_path) as conn:
        return {"ok": True, "merchant": catalog.merchant_summary(conn, merchant_id)}


def _list_merchants(db_path: str | Path) -> dict[str, Any]:
    with db_session(db_path) as conn:
        return {"ok": True, "results": _merchant_list(conn)}


def _create_product(db_path: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    with db_session(db_path) as conn:
        merchant_id = str(payload["merchant_id"])
        _require_merchant_token(conn, merchant_id, payload)
        product = catalog.create_product(
            conn,
            merchant_id=merchant_id,
            sku=str(payload["sku"]),
            title=str(payload["title"]),
            price=float(payload["price"]),
            stock=int(payload["stock"]),
            currency=str(payload.get("currency") or "CNY"),
            category=str(payload.get("category") or ""),
            tags=payload.get("tags") or [],
            description=str(payload.get("description") or ""),
            delivery_attributes=payload.get("delivery_attributes") or [],
        )
        return {"ok": True, "product": product}


def _update_product(db_path: str | Path, sku: str, payload: dict[str, Any]) -> dict[str, Any]:
    with db_session(db_path) as conn:
        existing = catalog.product_summary(conn, sku)
        merchant_id = str(payload.get("merchant_id") or existing["merchant_id"])
        _require_merchant_token(conn, merchant_id, payload)
        product = catalog.update_product(
            conn,
            sku=sku,
            merchant_id=merchant_id,
            title=payload.get("title"),
            price=_float_or_none(payload.get("price")),
            stock=_int_or_none(payload.get("stock")),
            currency=payload.get("currency"),
            category=payload.get("category"),
            tags=payload.get("tags") if "tags" in payload else None,
            description=payload.get("description"),
            delivery_attributes=payload.get("delivery_attributes") if "delivery_attributes" in payload else None,
        )
        return {"ok": True, "product": product}


def _get_product(db_path: str | Path, sku: str) -> dict[str, Any]:
    with db_session(db_path) as conn:
        return {"ok": True, "product": catalog.product_summary(conn, sku)}


def _search_products(db_path: str | Path, query: dict[str, Any]) -> dict[str, Any]:
    with db_session(db_path) as conn:
        return {
            "ok": True,
            "results": catalog.search_products(
                conn,
                query=str(query.get("query") or ""),
                city=str(query.get("city") or ""),
                area=str(query.get("area") or ""),
            ),
        }


def _search_merchants(db_path: str | Path, query: dict[str, Any]) -> dict[str, Any]:
    with db_session(db_path) as conn:
        return {
            "ok": True,
            "results": catalog.search_merchants(
                conn,
                query=str(query.get("query") or ""),
                city=str(query.get("city") or ""),
            ),
        }


def _buyer_ask(db_path: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    with db_session(db_path) as conn:
        return buyer_cli.ask(
            conn,
            buyer_id=str(payload["buyer_id"]),
            text=str(payload["text"]),
            city=str(payload.get("city") or ""),
            area=str(payload.get("area") or ""),
        )


def _get_conversation(db_path: str | Path, conversation_id: str) -> dict[str, Any]:
    with db_session(db_path) as conn:
        return {"ok": True, "conversation": conversation_summary(conn, conversation_id)}


def _create_conversation(db_path: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    with db_session(db_path) as conn:
        conversation = ensure_conversation(
            conn,
            buyer_id=str(payload["buyer_id"]),
            merchant_id=str(payload["merchant_id"]),
            sku=str(payload.get("sku") or ""),
        )
        if payload.get("text"):
            append_message(
                conn,
                conversation["id"],
                "buyer",
                str(payload.get("intent") or "ask_product"),
                str(payload["text"]),
                structured_payload={"source_id": payload.get("source_id") or ""},
            )
            conversation = conversation_summary(conn, conversation["id"])
        return {"ok": True, "conversation": conversation}


def _append_conversation_message(db_path: str | Path, conversation_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    with db_session(db_path) as conn:
        conversation = conversation_summary(conn, conversation_id)
        sender = str(payload["sender"])
        if sender in {"merchant", "merchant_agent"}:
            _require_merchant_token(conn, conversation["merchant_id"], payload)
        structured_payload = dict(payload.get("structured_payload") or {})
        if payload.get("source_id"):
            structured_payload["source_id"] = payload.get("source_id")
        message = append_message(
            conn,
            conversation_id,
            sender=sender,
            intent=str(payload["intent"]),
            text=str(payload["text"]),
            structured_payload=structured_payload,
            status=payload.get("status"),
        )
        return {"ok": True, "message": message, "conversation": conversation_summary(conn, conversation_id)}


def _close_conversation(db_path: str | Path, conversation_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    with db_session(db_path) as conn:
        conversation = conversation_summary(conn, conversation_id)
        sender = str(payload.get("sender") or "operator")
        if sender in {"merchant", "merchant_agent"}:
            _require_merchant_token(conn, conversation["merchant_id"], payload)
        if payload.get("text"):
            append_message(
                conn,
                conversation_id,
                sender=sender,
                intent=str(payload.get("intent") or "support"),
                text=str(payload["text"]),
                structured_payload={"source_id": payload.get("source_id") or ""},
                status="closed",
            )
        else:
            next_actor = next_actor_for_status("closed")
            conn.execute(
                "update conversations set status = 'closed', next_actor = ?, updated_at = ?, last_sender = ? where id = ?",
                (next_actor, now_iso(), sender, conversation_id),
            )
            append_audit_event(conn, conversation_id, sender, "conversation_closed", {"next_actor": next_actor})
        return {"ok": True, "conversation": conversation_summary(conn, conversation_id)}


def _agent_heartbeat(db_path: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    with db_session(db_path) as conn:
        merchant_id = str(payload["merchant_id"])
        _require_merchant_token(conn, merchant_id, payload)
        agent = merchant_agent.heartbeat(
            conn,
            merchant_id=merchant_id,
            status=str(payload.get("status") or "online"),
            capabilities=payload.get("capabilities"),
            pid=int(payload.get("pid") or 0),
            version=str(payload.get("version") or ""),
            last_error=str(payload.get("last_error") or ""),
            checked_count=int(payload.get("checked_count") or 0),
            replied_count=int(payload.get("replied_count") or 0),
        )
        return {"ok": True, "agent": agent}


def _agent_summary(row: Any) -> dict[str, Any]:
    stale_ttl = timedelta(seconds=agent_stale_ttl_seconds_from())
    last_seen_at = row["last_seen_at"]
    try:
        stale = datetime.now() - datetime.fromisoformat(last_seen_at) > stale_ttl
    except (TypeError, ValueError):
        stale = True
    return {
        "id": row["id"],
        "type": row["type"],
        "owner_id": row["owner_id"],
        "status": row["status"],
        "capabilities": decode_json(row["capabilities_json"], []),
        "last_seen_at": last_seen_at,
        "stale": stale,
        "stale_ttl_seconds": int(stale_ttl.total_seconds()),
        "pid": int(row["pid"] or 0),
        "version": row["version"],
        "last_error": row["last_error"],
        "checked_count": int(row["checked_count"] or 0),
        "replied_count": int(row["replied_count"] or 0),
    }


def _list_agents(db_path: str | Path, owner_id: str = "") -> dict[str, Any]:
    with db_session(db_path) as conn:
        if owner_id:
            rows = conn.execute("select * from agents where owner_id = ? order by id", (owner_id,)).fetchall()
        else:
            rows = conn.execute("select * from agents order by id").fetchall()
        return {"ok": True, "agents": [_agent_summary(row) for row in rows]}


def _get_agent(db_path: str | Path, agent_id: str) -> dict[str, Any]:
    with db_session(db_path) as conn:
        row = conn.execute("select * from agents where id = ?", (agent_id,)).fetchone()
        if row is None:
            raise SystemExit(f"Unknown agent: {agent_id}")
        return {"ok": True, "agent": _agent_summary(row)}


def _conversation_list(db_path: str | Path, filters: dict[str, Any]) -> dict[str, Any]:
    clauses: list[str] = []
    values: list[Any] = []
    for column in ("status", "merchant_id", "buyer_id", "sku"):
        if filters.get(column):
            clauses.append(f"{column} = ?")
            values.append(str(filters[column]))
    if filters.get("updated_since"):
        clauses.append("updated_at >= ?")
        values.append(str(filters["updated_since"]))
    sql = "select id from conversations"
    if clauses:
        sql += " where " + " and ".join(clauses)
    sql += " order by updated_at desc"
    with db_session(db_path) as conn:
        rows = conn.execute(sql, values).fetchall()
        return {"ok": True, "conversations": [conversation_summary(conn, row["id"]) for row in rows]}


def _merchant_conversations(db_path: str | Path, merchant_id: str, status: str = "") -> dict[str, Any]:
    with db_session(db_path) as conn:
        return {"ok": True, "merchant_id": merchant_id, "conversations": merchant_conversations(conn, merchant_id, status)}


def _review_summary(conn: Any, flag_row: Any) -> dict[str, Any]:
    conversation = conversation_summary(conn, flag_row["conversation_id"])
    return {
        "id": flag_row["id"],
        "conversation_id": flag_row["conversation_id"],
        "merchant_id": conversation["merchant_id"],
        "buyer_id": conversation["buyer_id"],
        "sku": flag_row["sku"],
        "reason": flag_row["reason"],
        "severity": flag_row["severity"],
        "created_at": flag_row["created_at"],
        "resolved_at": flag_row["resolved_at"] or None,
        "resolution": flag_row["resolution"],
        "resolved_by": flag_row["resolved_by"],
    }


def _human_review_queue(db_path: str | Path, merchant_id: str = "") -> dict[str, Any]:
    sql = """
        select f.* from moderation_flags f
        join conversations c on c.id = f.conversation_id
        where f.resolved_at = ''
    """
    values: list[Any] = []
    if merchant_id:
        sql += " and c.merchant_id = ?"
        values.append(merchant_id)
    sql += " order by f.created_at desc, f.id desc"
    with db_session(db_path) as conn:
        rows = conn.execute(sql, values).fetchall()
        return {"ok": True, "reviews": [_review_summary(conn, row) for row in rows]}


def _create_human_review(db_path: str | Path, conversation_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    with db_session(db_path) as conn:
        conversation = conversation_summary(conn, conversation_id)
        _require_merchant_token(conn, conversation["merchant_id"], payload)
        review = add_flag(
            conn,
            conversation_id,
            reason=str(payload.get("reason") or "human_required"),
            severity=str(payload.get("severity") or "review"),
            sku=conversation.get("sku") or "",
        )
        next_actor = next_actor_for_status("human_required", review["reason"])
        actor = str(payload.get("source_id") or "merchant_agent")
        conn.execute(
            "update conversations set status = 'human_required', next_actor = ?, updated_at = ?, last_sender = ? where id = ?",
            (next_actor, now_iso(), actor, conversation_id),
        )
        append_audit_event(
            conn,
            conversation_id,
            actor,
            "conversation_routed",
            {"status": "human_required", "next_actor": next_actor, "reason": review["reason"]},
        )
        row = conn.execute("select * from moderation_flags where id = ?", (review["id"],)).fetchone()
        return {
            "ok": True,
            "review": _review_summary(conn, row),
            "conversation": conversation_summary(conn, conversation_id),
        }


def _resolve_human_review(db_path: str | Path, conversation_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "reply")
    sender = str(payload.get("sender") or "merchant")
    status = "closed" if action == "close" else "waiting_buyer"
    with db_session(db_path) as conn:
        conversation = conversation_summary(conn, conversation_id)
        _require_merchant_token(conn, conversation["merchant_id"], payload)
        now = now_iso()
        conn.execute(
            """
            update moderation_flags
            set resolved_at = ?, resolution = ?, resolved_by = ?
            where conversation_id = ? and resolved_at = ''
            """,
            (now, action, sender, conversation_id),
        )
        if payload.get("text"):
            append_message(
                conn,
                conversation_id,
                sender=sender,
                intent=str(payload.get("intent") or "support"),
                text=str(payload["text"]),
                structured_payload={"resolution": action, "source_id": payload.get("source_id") or ""},
                status=status,
            )
        else:
            next_actor = next_actor_for_status(status)
            conn.execute(
                "update conversations set status = ?, next_actor = ?, updated_at = ?, last_sender = ? where id = ?",
                (status, next_actor, now, sender, conversation_id),
            )
            append_audit_event(
                conn,
                conversation_id,
                sender,
                "human_review_resolved",
                {"resolution": action, "status": status, "next_actor": next_actor},
            )
        rows = conn.execute(
            "select * from moderation_flags where conversation_id = ? order by id",
            (conversation_id,),
        ).fetchall()
        return {
            "ok": True,
            "reviews": [_review_summary(conn, row) for row in rows],
            "conversation": conversation_summary(conn, conversation_id),
        }


def handle_request(
    db_path: str | Path,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    query: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    payload = payload or {}
    query = query or {}
    parts = [part for part in path.strip("/").split("/") if part]
    try:
        if method == "GET" and path == "/health":
            return 200, _health(db_path)
        if path == "/merchants" and method == "GET":
            return 200, _list_merchants(db_path)
        if path == "/merchants" and method == "POST":
            return 200, _create_merchant(db_path, payload)
        if len(parts) == 2 and parts[0] == "merchants" and method == "GET":
            return 200, _get_merchant(db_path, parts[1])
        if len(parts) == 2 and parts[0] == "merchants" and method == "PATCH":
            return 200, _update_merchant(db_path, parts[1], payload)
        if path == "/products" and method == "POST":
            return 200, _create_product(db_path, payload)
        if len(parts) == 2 and parts[0] == "products" and method == "GET":
            return 200, _get_product(db_path, parts[1])
        if len(parts) == 2 and parts[0] == "products" and method == "PATCH":
            return 200, _update_product(db_path, parts[1], payload)
        if path == "/search/products" and method == "GET":
            return 200, _search_products(db_path, query)
        if path == "/search/merchants" and method == "GET":
            return 200, _search_merchants(db_path, query)
        if path == "/buyer/ask" and method == "POST":
            return 200, _buyer_ask(db_path, payload)
        if path == "/conversations" and method == "POST":
            return 200, _create_conversation(db_path, payload)
        if len(parts) == 3 and parts[0] == "buyers" and parts[2] == "conversations" and method == "GET":
            filters = dict(query)
            filters["buyer_id"] = parts[1]
            return 200, _conversation_list(db_path, filters)
        if len(parts) == 2 and parts[0] == "conversations" and method == "GET":
            return 200, _get_conversation(db_path, parts[1])
        if len(parts) == 3 and parts[0] == "conversations" and parts[2] == "messages" and method == "POST":
            return 200, _append_conversation_message(db_path, parts[1], payload)
        if len(parts) == 3 and parts[0] == "conversations" and parts[2] == "close" and method == "POST":
            return 200, _close_conversation(db_path, parts[1], payload)
        if path == "/agents/heartbeat" and method == "POST":
            return 200, _agent_heartbeat(db_path, payload)
        if path == "/agents" and method == "GET":
            return 200, _list_agents(db_path)
        if len(parts) == 2 and parts[0] == "agents" and method == "GET":
            return 200, _get_agent(db_path, parts[1])
        if len(parts) == 3 and parts[0] == "merchants" and parts[2] == "agents" and method == "GET":
            return 200, _list_agents(db_path, owner_id=parts[1])
        if path == "/human-review/queue" and method == "GET":
            return 200, _human_review_queue(db_path)
        if len(parts) == 3 and parts[0] == "merchants" and parts[2] == "conversations" and method == "GET":
            filters = dict(query)
            filters["merchant_id"] = parts[1]
            return 200, _conversation_list(db_path, filters)
        if len(parts) == 3 and parts[0] == "merchants" and parts[2] == "human-review" and method == "GET":
            return 200, _merchant_conversations(db_path, parts[1], status="human_required")
        if len(parts) == 3 and parts[0] == "conversations" and parts[2] == "human-review" and method == "POST":
            return 200, _create_human_review(db_path, parts[1], payload)
        if len(parts) == 4 and parts[0] == "conversations" and parts[2] == "human-review" and parts[3] == "resolve" and method == "POST":
            return 200, _resolve_human_review(db_path, parts[1], payload)
    except AuthError as exc:
        return 403, {"ok": False, "error": str(exc)}
    except (KeyError, ValueError, SystemExit) as exc:
        return 400, {"ok": False, "error": str(exc)}
    return 404, {"ok": False, "error": f"No route for {method} {path}"}


def create_app(db_path: str | Path = "mai-cli.sqlite") -> Any:
    if FastAPI is None:
        return MarketplaceASGIApp(db_path)

    app = FastAPI(title="mai-cli Marketplace API", version=VERSION)
    app.state.db_path = str(db_path)
    app.state.fastapi_available = True

    @app.exception_handler(AuthError)
    def auth_error_handler(_request: Any, exc: AuthError) -> Any:
        return _json_error_response(403, str(exc))

    @app.exception_handler(KeyError)
    def key_error_handler(_request: Any, exc: KeyError) -> Any:
        return _json_error_response(400, str(exc))

    @app.exception_handler(ValueError)
    def value_error_handler(_request: Any, exc: ValueError) -> Any:
        return _json_error_response(400, str(exc))

    @app.exception_handler(SystemExit)
    def system_exit_handler(_request: Any, exc: SystemExit) -> Any:
        return _json_error_response(400, str(exc))

    if RequestValidationError is not None:  # pragma: no cover - exercised with fastapi installed
        @app.exception_handler(RequestValidationError)
        def request_validation_error_handler(_request: Any, exc: Exception) -> Any:
            return _json_error_response(400, str(exc))

    @app.get("/health")
    def health() -> dict[str, Any]:
        return _health(db_path)

    @app.get("/merchants")
    def list_merchants() -> dict[str, Any]:
        return _list_merchants(db_path)

    @app.post("/merchants")
    def create_merchant(payload: dict[str, Any]) -> dict[str, Any]:
        return _create_merchant(db_path, payload)

    @app.get("/merchants/{merchant_id}")
    def get_merchant(merchant_id: str) -> dict[str, Any]:
        return _get_merchant(db_path, merchant_id)

    @app.patch("/merchants/{merchant_id}")
    def update_merchant(merchant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return _update_merchant(db_path, merchant_id, payload)

    @app.post("/products")
    def create_product(payload: dict[str, Any]) -> dict[str, Any]:
        return _create_product(db_path, payload)

    @app.get("/products/{sku}")
    def get_product(sku: str) -> dict[str, Any]:
        return _get_product(db_path, sku)

    @app.patch("/products/{sku}")
    def update_product(sku: str, payload: dict[str, Any]) -> dict[str, Any]:
        return _update_product(db_path, sku, payload)

    @app.get("/search/products")
    def search_products(query: str = "", city: str = "", area: str = "") -> dict[str, Any]:
        return _search_products(db_path, {"query": query, "city": city, "area": area})

    @app.get("/search/merchants")
    def search_merchants(query: str = "", city: str = "") -> dict[str, Any]:
        return _search_merchants(db_path, {"query": query, "city": city})

    @app.post("/buyer/ask")
    def buyer_ask(payload: dict[str, Any]) -> dict[str, Any]:
        return _buyer_ask(db_path, payload)

    @app.post("/conversations")
    def create_conversation(payload: dict[str, Any]) -> dict[str, Any]:
        return _create_conversation(db_path, payload)

    @app.get("/buyers/{buyer_id}/conversations")
    def get_buyer_conversations(
        buyer_id: str,
        status: str = "",
        merchant_id: str = "",
        sku: str = "",
        updated_since: str = "",
    ) -> dict[str, Any]:
        return _conversation_list(
            db_path,
            {
                "buyer_id": buyer_id,
                "status": status,
                "merchant_id": merchant_id,
                "sku": sku,
                "updated_since": updated_since,
            },
        )

    @app.get("/conversations/{conversation_id}")
    def get_conversation(conversation_id: str) -> dict[str, Any]:
        return _get_conversation(db_path, conversation_id)

    @app.post("/conversations/{conversation_id}/messages")
    def add_message(conversation_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return _append_conversation_message(db_path, conversation_id, payload)

    @app.post("/conversations/{conversation_id}/close")
    def close_conversation(conversation_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return _close_conversation(db_path, conversation_id, payload)

    @app.post("/agents/heartbeat")
    def agent_heartbeat(payload: dict[str, Any]) -> dict[str, Any]:
        return _agent_heartbeat(db_path, payload)

    @app.get("/agents")
    def list_agents() -> dict[str, Any]:
        return _list_agents(db_path)

    @app.get("/agents/{agent_id}")
    def get_agent(agent_id: str) -> dict[str, Any]:
        return _get_agent(db_path, agent_id)

    @app.get("/merchants/{merchant_id}/agents")
    def get_merchant_agents(merchant_id: str) -> dict[str, Any]:
        return _list_agents(db_path, owner_id=merchant_id)

    @app.get("/human-review/queue")
    def human_review_queue() -> dict[str, Any]:
        return _human_review_queue(db_path)

    @app.get("/merchants/{merchant_id}/conversations")
    def get_merchant_conversations(
        merchant_id: str,
        status: str = "",
        buyer_id: str = "",
        sku: str = "",
        updated_since: str = "",
    ) -> dict[str, Any]:
        return _conversation_list(
            db_path,
            {
                "merchant_id": merchant_id,
                "status": status,
                "buyer_id": buyer_id,
                "sku": sku,
                "updated_since": updated_since,
            },
        )

    @app.get("/merchants/{merchant_id}/human-review")
    def human_review(merchant_id: str) -> dict[str, Any]:
        return _merchant_conversations(db_path, merchant_id, status="human_required")

    @app.post("/conversations/{conversation_id}/human-review")
    def create_human_review(conversation_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return _create_human_review(db_path, conversation_id, payload)

    @app.post("/conversations/{conversation_id}/human-review/resolve")
    def resolve_human_review(conversation_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return _resolve_human_review(db_path, conversation_id, payload)

    return app
