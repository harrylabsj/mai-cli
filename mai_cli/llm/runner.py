"""Deterministic LLM tool-call loop for marketplace tools."""

from __future__ import annotations

import json
from typing import Any

from mai_cli.llm.dispatcher import MarketplaceToolDispatcher
from mai_cli.llm.providers import OpenAICompatibleProvider, LLMResponse
from mai_cli.llm.tools import marketplace_tool_schemas

FALLBACK_CONTENT = "I could not safely complete this consultation tool loop. A human should review before replying."


def _assistant_message(response: LLMResponse) -> dict[str, Any]:
    choices = response.raw.get("choices") or []
    message = choices[0].get("message", {}) if choices else {}
    if not isinstance(message, dict):
        return {"role": "assistant", "content": response.content}
    return dict(message)


def _fallback(messages: list[dict[str, Any]], tool_results: list[dict[str, Any]], error: str) -> dict[str, Any]:
    return {
        "ok": False,
        "content": FALLBACK_CONTENT,
        "messages": messages,
        "tool_results": tool_results,
        "error": error,
    }


def _tool_call_name_and_arguments(tool_call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    function = tool_call.get("function") or {}
    name = str(function.get("name") or "")
    if not name:
        raise ValueError("tool call missing function name")
    raw_arguments = function.get("arguments") or "{}"
    if isinstance(raw_arguments, dict):
        arguments = raw_arguments
    else:
        arguments = json.loads(str(raw_arguments or "{}"))
    if not isinstance(arguments, dict):
        raise ValueError(f"tool call {name} arguments must be a JSON object")
    return name, arguments


def run_marketplace_tool_loop(
    provider: OpenAICompatibleProvider,
    dispatcher: MarketplaceToolDispatcher,
    messages: list[dict[str, Any]],
    max_steps: int = 4,
) -> dict[str, Any]:
    conversation_messages = [dict(message) for message in messages]
    tool_results: list[dict[str, Any]] = []
    tools = marketplace_tool_schemas()

    for _step in range(max(1, int(max_steps or 1))):
        try:
            response = provider.complete(conversation_messages, tools=tools)
        except (Exception, SystemExit) as exc:
            return _fallback(conversation_messages, tool_results, f"{type(exc).__name__}: {exc}")

        assistant = _assistant_message(response)
        tool_calls = assistant.get("tool_calls") or []
        if not tool_calls:
            return {
                "ok": True,
                "content": str(assistant.get("content") or response.content or ""),
                "messages": conversation_messages + [assistant],
                "tool_results": tool_results,
                "error": "",
            }

        conversation_messages.append(assistant)
        for tool_call in tool_calls:
            try:
                name, arguments = _tool_call_name_and_arguments(tool_call)
                dispatched = dispatcher.dispatch(name, arguments)
            except (Exception, SystemExit) as exc:
                return _fallback(conversation_messages, tool_results, f"{type(exc).__name__}: {exc}")
            tool_results.append(dispatched)
            conversation_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(tool_call.get("id") or ""),
                    "name": name,
                    "content": json.dumps(dispatched, ensure_ascii=False, sort_keys=True),
                }
            )

    return _fallback(conversation_messages, tool_results, "LLM tool loop exceeded max_steps")
