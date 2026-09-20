"""Daily trading strategy agent. Supports IBM watsonx.ai (default) and Groq (legacy).

The transport layer is injectable (pass `transport=` in tests). The default
transport auto-selects watsonx.ai when LLM_PROVIDER=watsonx, falling back to
Groq when LLM_PROVIDER=groq — matching the operator LLM in engine/algo/llm.py.
"""
from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request
from dataclasses import asdict
from typing import Callable

from engine.config import Config
from engine.domain import GridRiskPrediction, StrategyParams

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"


class AITradingStrategyAgent:
    """Lets an LLM tune two safe market parameters once per simulated day.

    When no `transport` is injected, the default transport is chosen based on
    the LLM_PROVIDER environment variable:
      - "watsonx" (default): uses IBM watsonx.ai via engine/algo/llm.py
      - "groq" (legacy): uses Groq's OpenAI-compatible chat completions API
    """

    def __init__(self, config: Config, transport: Callable | None = None):
        self.config = config
        if transport is not None:
            self.transport = transport
        else:
            from engine import settings as _settings
            if _settings.LLM_PROVIDER == "groq":
                self.transport = self._groq_request
            else:
                self.transport = self._watsonx_request
        self.last_model: str | None = None
        self.last_error: str | None = None

    def decide(self, weather: dict, price_history: list[float],
               grid_risk: list[GridRiskPrediction],
               previous: StrategyParams) -> StrategyParams:
        self.last_model = None
        self.last_error = None
        if not self.config.llm_enabled:
            return previous
        prompt = {
            "weather": weather,
            "recent_clearing_prices_inr": price_history[-24:],
            "grid_risk": [asdict(r) for r in grid_risk],
            "previous": asdict(previous),
        }
        self.last_error = None
        for model in (self.config.groq_primary_model,
                      self.config.groq_fallback_model):
            try:
                raw = self.transport(model, prompt, self.config.groq_timeout_seconds)
                decision = json.loads(raw) if isinstance(raw, str) else raw
                strategy = StrategyParams(
                    discount=_clamp(float(decision["discount"]), 0.50, 1.00),
                    margin=_clamp(float(decision["margin"]), 0.00, 0.35),
                    battery_reserve_frac=previous.battery_reserve_frac,
                    bid_aggression=previous.bid_aggression,
                )
                self.last_model = model
                self.last_error = None
                return strategy
            except urllib.error.HTTPError as exc:
                detail = f"HTTP {exc.code}"
                try:
                    raw_body = exc.read().decode("utf-8")
                    err_json = json.loads(raw_body)
                    msg = err_json.get("error", {}).get("message")
                    if msg:
                        detail += f": {msg}"
                except Exception:
                    pass
                self.last_error = f"HTTPError ({detail})"
            except Exception as exc:
                # Provider error bodies can contain sensitive request details.
                self.last_error = f"{type(exc).__name__}"
        self.last_model = None
        return previous

    def _groq_request(self, model: str, context: dict, timeout: float) -> str:
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError("GROQ_API_KEY is not set")
        system = (
            "You manage a local peer-to-peer energy market. Return JSON only "
            "with numeric keys discount and margin. Keep trades attractive to "
            "households while reducing demand when transformer risk is high."
        )
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(context)},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
            "max_tokens": 180,
        }
        if model.startswith("openai/gpt-oss-"):
            # Reasoning uses the completion budget too; 180 tokens can leave
            # no room for the final JSON strategy.
            payload.pop("max_tokens")
            payload.update(max_completion_tokens=2048, reasoning_effort="low")
        elif model == "qwen/qwen3.8-27b":
            # These short strategy decisions do not need a thinking trace.
            payload.pop("max_tokens")
            payload.update(max_completion_tokens=400, reasoning_effort="none")
        body = json.dumps(payload).encode("utf-8")
        base_url = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
        chat_url = f"{base_url}/chat/completions"
        request = urllib.request.Request(
            chat_url, data=body, method="POST",
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json",
                     # urllib otherwise sends "Python-urllib/3.x", which
                     # Cloudflare rejects with error 1010 BEFORE Groq ever
                     # evaluates the key. Every call returned 403, both models
                     # in decide() burned their attempt, and the agent fell back
                     # to the previous strategy on every simulated day — with a
                     # perfectly valid key. engine/algo/llm.py was never hit by
                     # this because httpx sends a User-Agent of its own.
                     "User-Agent": "UrjaSetu/1.0 (engine agent)",
                     "Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload["choices"][0]["message"]["content"]

    def _watsonx_request(self, model: str, context: dict, timeout: float) -> str:
        """IBM watsonx.ai transport for the trading strategy agent.

        The `model` argument is ignored here (model is set by WATSONX_MODEL_ID env
        var); it is kept in the signature so the transport interface stays uniform
        and tests can inject watsonx responses without changing the call site.
        """
        from engine import settings as _settings
        from engine.algo import llm as _llm
        prompt = (
            "You manage a local peer-to-peer energy market. Return JSON only "
            "with numeric keys discount and margin. Keep trades attractive to "
            "households while reducing demand when transformer risk is high.\n\n"
            f"{json.dumps(context)}"
        )
        # Reuse the operator LLM call path (watsonx bearer token, same endpoint).
        # The system prompt is embedded directly in the prompt string above.
        raw = _llm._call_llm_watsonx(prompt, timeout,
                                      system="Return JSON only with keys discount and margin.")
        return raw


def _clamp(value: float, low: float, high: float) -> float:
    if not math.isfinite(value):
        raise ValueError("Strategy values must be finite")
    return max(low, min(high, value))
