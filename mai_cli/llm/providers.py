"""Provider abstraction for optional OpenAI-compatible LLM runtimes."""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable


Transport = Callable[[str, dict[str, str], dict[str, Any], int], dict[str, Any]]


@dataclass(frozen=True)
class LLMResponse:
    content: str
    raw: dict[str, Any]


def _default_transport(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:  # pragma: no cover - network path
        return json.loads(response.read().decode("utf-8") or "{}")


class OpenAICompatibleProvider:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: int = 30,
        transport: Transport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = int(timeout or 30)
        self.transport = transport or _default_transport

    def complete(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
        }
        if tools:
            payload["tools"] = tools
        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {self.api_key}",
        }
        raw = self.transport(f"{self.base_url}/chat/completions", headers, payload, self.timeout)
        choices = raw.get("choices") or []
        message = choices[0].get("message", {}) if choices else {}
        return LLMResponse(content=str(message.get("content") or ""), raw=raw)


def provider_from_env(transport: Transport | None = None) -> OpenAICompatibleProvider:
    timeout_raw = os.environ.get("MAI_LLM_TIMEOUT_SECONDS") or "30"
    try:
        timeout = int(timeout_raw)
    except ValueError:
        timeout = 30
    return OpenAICompatibleProvider(
        base_url=os.environ.get("MAI_LLM_BASE_URL") or "https://api.openai.com/v1",
        api_key=os.environ.get("MAI_LLM_API_KEY") or "",
        model=os.environ.get("MAI_LLM_MODEL") or "gpt-4.1-mini",
        timeout=timeout,
        transport=transport,
    )
