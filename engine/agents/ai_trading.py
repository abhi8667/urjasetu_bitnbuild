"""Groq-backed daily trading strategy agent with a model fallback."""
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
    """Lets an LLM tune two safe market parameters once per simulated day."""

    def __init__(self, config: Config, transport: Callable | None = None):
        self.config = config
        self.transport = transport or self._groq_request
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
            except Exception as exc:
                # Provider error bodies can contain sensitive request details.
                self.last_error = f"{type(exc).__name__}" + (f" (HTTP {exc.code})" if isinstance(exc, urllib.error.HTTPError) else "")
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
        body = json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(context)},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
            "max_completion_tokens": 180,
        }).encode("utf-8")
        request = urllib.request.Request(
            GROQ_CHAT_URL, data=body, method="POST",
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


def _clamp(value: float, low: float, high: float) -> float:
    if not math.isfinite(value):
        raise ValueError("Strategy values must be finite")
    return max(low, min(high, value))
