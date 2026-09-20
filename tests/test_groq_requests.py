"""Regression coverage for the deployed Groq model migration."""
import io
import json
import unittest
import urllib.error
from dataclasses import replace
from unittest.mock import MagicMock, patch

from engine import settings
from engine.agents.ai_trading import AITradingStrategyAgent
from engine.algo import llm
from engine.config import DEFAULT
from engine.domain import StrategyParams


class GroqRequestTests(unittest.TestCase):
    def test_model_not_found_uses_current_fallback(self):
        config = replace(DEFAULT, llm_enabled=True,
                         groq_primary_model="llama-3.3-70b-versatile")
        missing = urllib.error.HTTPError(
            "https://api.groq.com/openai/v1/chat/completions", 404,
            "Not Found", {}, io.BytesIO(b'{"error":{"message":"model not found"}}'))
        transport = MagicMock(side_effect=[missing, '{"discount":0.8,"margin":0.1}'])
        agent = AITradingStrategyAgent(config, transport=transport)
        result = agent.decide({}, [], [], StrategyParams())
        missing.close()
        self.assertEqual(agent.last_model, "openai/gpt-oss-120b")
        self.assertIsNone(agent.last_error)
        self.assertEqual(result.discount, 0.8)
        self.assertEqual(transport.call_count, 2)

    def test_strategy_request_reserves_reasoning_budget(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "choices": [{"message": {"content": '{"discount":0.8,"margin":0.1}'}}]
        }).encode()
        with patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}), patch(
                "urllib.request.urlopen", return_value=response) as send:
            agent = AITradingStrategyAgent(DEFAULT)
            for model in (DEFAULT.groq_primary_model, DEFAULT.groq_fallback_model):
                agent._groq_request(model, {}, 6)
                body = json.loads(send.call_args.args[0].data)
                self.assertEqual(body["model"], model)
                qwen = model == "qwen/qwen3.8-27b"
                self.assertEqual(body["reasoning_effort"], "none" if qwen else "low")
                self.assertEqual(body["max_completion_tokens"], 400 if qwen else 2048)
                self.assertNotIn("max_tokens", body)
                self.assertEqual(body["response_format"], {"type": "json_object"})

    def test_operator_request_reserves_reasoning_budget(self):
        # LLM_PROVIDER must be "groq" so _call_llm routes to _call_llm_groq,
        # not the watsonx path which uses a different auth/response shape.
        with patch.object(llm, "LLM_ENABLED", True), patch.object(
                settings, "LLM_PROVIDER", "groq"), patch.object(
                settings, "GROQ_MODEL", DEFAULT.groq_primary_model), patch("httpx.post") as send:
            send.return_value.json.return_value = {
                "choices": [{"message": {"content": "answer"}}]}
            self.assertEqual(llm._call_llm("question", 6), "answer")
            body = send.call_args.kwargs["json"]
            self.assertEqual(body["model"], "qwen/qwen3.8-27b")
            self.assertEqual(body["max_completion_tokens"], 400)
            self.assertEqual(body["reasoning_effort"], "none")


if __name__ == "__main__":
    unittest.main()
