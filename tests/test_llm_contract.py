import os
import unittest
from unittest.mock import patch

from mai_cli.llm.providers import OpenAICompatibleProvider, provider_from_env
from mai_cli.llm.tools import marketplace_tool_schemas


class LlmContractTest(unittest.TestCase):
    def test_marketplace_tool_schemas_are_openai_function_tools(self):
        tools = marketplace_tool_schemas()
        names = [tool["function"]["name"] for tool in tools]

        self.assertEqual(
            names,
            [
                "catalog_search",
                "conversation_send",
                "conversation_summarize",
                "human_review_flag",
                "merchant_reply",
            ],
        )
        self.assertNotIn("create_order", names)
        self.assertNotIn("charge_payment", names)
        for tool in tools:
            self.assertEqual(tool["type"], "function")
            parameters = tool["function"]["parameters"]
            self.assertEqual(parameters["type"], "object")
            self.assertFalse(parameters["additionalProperties"])

    def test_openai_compatible_provider_builds_payload_with_tools(self):
        calls = []

        def fake_transport(url, headers, payload, timeout):
            calls.append({"url": url, "headers": headers, "payload": payload, "timeout": timeout})
            return {"choices": [{"message": {"content": "consultation reply"}}]}

        provider = OpenAICompatibleProvider(
            base_url="https://llm.example/v1/",
            api_key="secret-token",
            model="mai-test-model",
            timeout=12,
            transport=fake_transport,
        )

        response = provider.complete(
            [
                {"role": "system", "content": "Stay inside MVP consultation boundaries."},
                {"role": "user", "content": "Can this merchant deliver today?"},
            ],
            tools=marketplace_tool_schemas(),
        )

        self.assertEqual(response.content, "consultation reply")
        self.assertEqual(calls[0]["url"], "https://llm.example/v1/chat/completions")
        self.assertEqual(calls[0]["headers"]["authorization"], "Bearer secret-token")
        self.assertEqual(calls[0]["timeout"], 12)
        payload = calls[0]["payload"]
        self.assertEqual(payload["model"], "mai-test-model")
        self.assertEqual(payload["temperature"], 0)
        self.assertEqual(payload["messages"][1]["content"], "Can this merchant deliver today?")
        self.assertEqual(payload["tools"][0]["function"]["name"], "catalog_search")
        self.assertNotIn("secret-token", str(payload))

    def test_provider_from_env_reads_openai_compatible_settings(self):
        env = {
            "MAI_LLM_BASE_URL": "https://llm.example/custom",
            "MAI_LLM_API_KEY": "env-token",
            "MAI_LLM_MODEL": "env-model",
            "MAI_LLM_TIMEOUT_SECONDS": "9",
        }
        with patch.dict(os.environ, env, clear=False):
            provider = provider_from_env(transport=lambda *_args: {"choices": [{"message": {"content": "ok"}}]})

        self.assertEqual(provider.base_url, "https://llm.example/custom")
        self.assertEqual(provider.api_key, "env-token")
        self.assertEqual(provider.model, "env-model")
        self.assertEqual(provider.timeout, 9)


if __name__ == "__main__":
    unittest.main()
