"""Dispatch optional LLM tool calls into trusted marketplace operations."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from mai_cli.agents import buyer_cli
from mai_cli.core.catalog import search_products
from mai_cli.core.conversations import add_flag, append_message, conversation_summary
from mai_cli.core.harness import append_audit_event, next_actor_for_status
from mai_cli.db.session import db_session, now_iso
from mai_cli.llm.tools import marketplace_tool_schema_objects


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]

BUYER_SCOPES = {"buyer", "buyer_cli"}
MERCHANT_SCOPES = {"merchant", "merchant_agent"}
PRIVILEGED_CONVERSATION_SCOPES = {"local_trusted", "operator"}
SOURCE_OWNER_PREFIXES = ("mai-cli-merchant-agent:", "mai-cli-buyer-agent:", "merchant:", "buyer:")

TOOL_SCOPE_ALLOWLIST = {
    "catalog_search": {"local_trusted", "buyer", "buyer_cli", "merchant", "merchant_agent", "operator"},
    "conversation_send": {"local_trusted", "buyer", "buyer_cli"},
    "conversation_summarize": {"local_trusted", "buyer", "buyer_cli", "merchant", "merchant_agent", "operator"},
    "human_review_flag": {"local_trusted", "merchant", "merchant_agent", "operator"},
    "merchant_reply": {"local_trusted", "merchant", "merchant_agent"},
}


class ToolAccessDenied(Exception):
    """Raised when a scoped tool call targets a conversation owned by another actor."""


class MarketplaceToolDispatcher:
    def __init__(
        self,
        db_path: str | Path,
        source_id: str = "llm-tool",
        host: str = "local",
        session_id: str = "",
        actor: str = "",
        token_scope: str = "local_trusted",
    ):
        self.db_path = Path(db_path).expanduser()
        self.source_id = source_id
        self.host = host
        self.session_id = session_id
        self.actor = actor
        self.token_scope = token_scope
        self.allowed_tools = {tool.name for tool in marketplace_tool_schema_objects()}

    def dispatch(self, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        arguments = arguments or {}
        if tool_name not in self.allowed_tools:
            self._audit_tool_call(tool_name, arguments, "denied", f"Unknown or disallowed marketplace tool: {tool_name}")
            raise SystemExit(f"Unknown or disallowed marketplace tool: {tool_name}")
        allowed_scopes = TOOL_SCOPE_ALLOWLIST.get(tool_name, set())
        if self.token_scope not in allowed_scopes:
            error = f"tool {tool_name} is not allowed for token scope {self.token_scope}"
            self._audit_tool_call(tool_name, arguments, "denied", error)
            raise SystemExit(error)
        handler = getattr(self, f"_dispatch_{tool_name}")
        try:
            result = handler(arguments)
        except ToolAccessDenied as exc:
            error = str(exc)
            self._audit_tool_call(tool_name, arguments, "denied", error)
            raise SystemExit(error) from exc
        except Exception as exc:
            self._audit_tool_call(tool_name, arguments, "error", str(exc))
            raise
        self._audit_tool_call(tool_name, arguments, "ok", "")
        return {"ok": True, "tool": tool_name, "result": result}

    def _audit_tool_call(self, tool_name: str, arguments: dict[str, Any], status: str, error: str = "") -> None:
        conversation_id = str(arguments.get("conversation_id") or "")
        with db_session(self.db_path) as conn:
            append_audit_event(
                conn,
                conversation_id,
                self.actor or self.source_id,
                "llm_tool_call",
                {
                    "tool": tool_name,
                    "status": status,
                    "host": self.host,
                    "session_id": self.session_id,
                    "actor": self.actor,
                    "source_id": self.source_id,
                    "token_scope": self.token_scope,
                    "error": error,
                },
            )

    def _conversation_for_tool(self, conn: Any, conversation_id: str, tool_name: str) -> dict[str, Any]:
        conversation = conversation_summary(conn, conversation_id)
        self._require_conversation_access(conversation, tool_name)
        return conversation

    def _identity_candidates(self) -> set[str]:
        candidates: set[str] = set()
        for value in (self.actor, self.source_id):
            identity = str(value or "").strip()
            if not identity:
                continue
            candidates.add(identity)
            for prefix in SOURCE_OWNER_PREFIXES:
                if identity.startswith(prefix):
                    owner_id = identity[len(prefix) :].strip()
                    if owner_id:
                        candidates.add(owner_id)
        return candidates

    def _require_conversation_access(self, conversation: dict[str, Any], tool_name: str) -> None:
        if self.token_scope in PRIVILEGED_CONVERSATION_SCOPES:
            return
        if self.token_scope in MERCHANT_SCOPES:
            owner_key = "merchant_id"
        elif self.token_scope in BUYER_SCOPES:
            owner_key = "buyer_id"
        else:
            raise ToolAccessDenied(f"tool {tool_name} is not allowed for token scope {self.token_scope}")

        owner_id = str(conversation.get(owner_key) or "")
        if owner_id and owner_id in self._identity_candidates():
            return
        actor = self.actor or self.source_id or "<missing>"
        raise ToolAccessDenied(
            f"tool {tool_name} is not allowed for token scope {self.token_scope} actor {actor} "
            f"on conversation {conversation.get('id')}"
        )

    def _dispatch_catalog_search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with db_session(self.db_path) as conn:
            results = search_products(
                conn,
                query=str(arguments["query"]),
                city=str(arguments.get("city") or ""),
                area=str(arguments.get("area") or ""),
                max_price=arguments.get("max_price"),
                include_out_of_stock=bool(arguments.get("include_out_of_stock") or False),
            )
        return {"ok": True, "query": str(arguments["query"]), "results": results}

    def _dispatch_conversation_send(self, arguments: dict[str, Any]) -> dict[str, Any]:
        sender = str(arguments["sender"])
        if sender not in {"buyer", "buyer_cli"}:
            raise SystemExit("conversation_send only supports buyer or buyer_cli senders")
        conversation_id = str(arguments["conversation_id"])
        with db_session(self.db_path) as conn:
            self._conversation_for_tool(conn, conversation_id, "conversation_send")
            message = append_message(
                conn,
                conversation_id,
                sender,
                str(arguments["intent"]),
                str(arguments["text"]),
                structured_payload={"source_id": self.source_id, "tool": "conversation_send"},
            )
            conversation = conversation_summary(conn, conversation_id)
        return {"ok": True, "message": message, "conversation": conversation}

    def _dispatch_conversation_summarize(self, arguments: dict[str, Any]) -> dict[str, Any]:
        conversation_id = str(arguments["conversation_id"])
        with db_session(self.db_path) as conn:
            self._conversation_for_tool(conn, conversation_id, "conversation_summarize")
            summary = buyer_cli.summarize(conn, conversation_id)
        return {"ok": True, "summary": summary}

    def _dispatch_human_review_flag(self, arguments: dict[str, Any]) -> dict[str, Any]:
        conversation_id = str(arguments["conversation_id"])
        reason = str(arguments.get("reason") or "human_required")
        severity = str(arguments.get("severity") or "review")
        with db_session(self.db_path) as conn:
            conversation = self._conversation_for_tool(conn, conversation_id, "human_review_flag")
            flag = add_flag(conn, conversation_id, reason=reason, severity=severity, sku=conversation.get("sku") or "")
            next_actor = next_actor_for_status("human_required", reason)
            conn.execute(
                "update conversations set status = 'human_required', next_actor = ?, updated_at = ?, last_sender = ? where id = ?",
                (next_actor, now_iso(), self.source_id, conversation_id),
            )
            append_audit_event(
                conn,
                conversation_id,
                self.source_id,
                "conversation_routed",
                {"status": "human_required", "next_actor": next_actor, "reason": reason, "tool": "human_review_flag"},
            )
            review = add_review_source(flag, self.source_id)
            conversation = conversation_summary(conn, conversation_id)
        return {"ok": True, "review": review, "conversation": conversation}

    def _dispatch_merchant_reply(self, arguments: dict[str, Any]) -> dict[str, Any]:
        conversation_id = str(arguments["conversation_id"])
        human_required = bool(arguments.get("human_required") or False)
        reason = str(arguments.get("reason") or "")
        status = "human_required" if human_required else "waiting_buyer"
        with db_session(self.db_path) as conn:
            conversation = self._conversation_for_tool(conn, conversation_id, "merchant_reply")
            message = append_message(
                conn,
                conversation_id,
                "merchant_agent",
                str(arguments["intent"]),
                str(arguments["text"]),
                structured_payload={
                    "source_id": self.source_id,
                    "tool": "merchant_reply",
                    "human_required": human_required,
                    "reason": reason,
                },
                status=status,
            )
            flags = []
            if human_required:
                flag = add_flag(conn, conversation_id, reason or "human_required", sku=conversation.get("sku") or "")
                flags.append(add_review_source(flag, self.source_id))
            conversation = conversation_summary(conn, conversation_id)
        return {"ok": True, "message": message, "flags": flags, "conversation": conversation}


def add_review_source(review: dict[str, Any], source_id: str) -> dict[str, Any]:
    sourced = dict(review)
    sourced["source_id"] = source_id
    return sourced


def dispatch_marketplace_tool(
    db_path: str | Path,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    source_id: str = "llm-tool",
) -> dict[str, Any]:
    return MarketplaceToolDispatcher(db_path, source_id=source_id).dispatch(tool_name, arguments)
