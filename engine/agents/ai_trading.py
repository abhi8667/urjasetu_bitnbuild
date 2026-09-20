"""Groq-backed daily trading strategy agent with a model fallback."""
from __future__ import annotations

import json
import math
import os
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from typing import Callable

from engine.config import Config
from engine.domain import GridRiskPrediction, StrategyParams

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"


@dataclass(frozen=True)
class LLMResponse:
    """The provider's final content and separately-returned reasoning trace."""

    content: str
    reasoning: str | None = None


class AITradingStrategyAgent:
    """Lets an LLM tune two safe market parameters once per simulated day."""

    def __init__(self, config: Config, transport: Callable | None = None):
        self.config = config
        self.transport = transport or self._groq_request
        self.last_model: str | None = None
        self.last_error: str | None = None
        self.last_reasoning: str | None = None

    def decide(self, weather: dict, price_history: list[float],
               grid_risk: list[GridRiskPrediction],
               previous: StrategyParams) -> StrategyParams:
        self.last_model = None
        self.last_error = None
        self.last_reasoning = None
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
                response = self.transport(model, prompt, self.config.groq_timeout_seconds)
                if isinstance(response, LLMResponse):
                    raw = response.content
                    reasoning = _display_reasoning(response.reasoning)
                else:
                    # Keep custom/test transports backwards compatible.
                    raw = response
                    reasoning = None
                decision = json.loads(raw) if isinstance(raw, str) else raw
                strategy = StrategyParams(
                    discount=_clamp(float(decision["discount"]), 0.50, 1.00),
                    margin=_clamp(float(decision["margin"]), 0.00, 0.35),
                    battery_reserve_frac=previous.battery_reserve_frac,
                    bid_aggression=previous.bid_aggression,
                )
                self.last_model = model
                self.last_error = None
                # Only associate a rationale with a response whose final JSON
                # passed validation. A malformed primary response must not be
                # shown as though it explained a successful fallback decision.
                self.last_reasoning = reasoning
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

    def _groq_request(self, model: str, context: dict, timeout: float) -> LLMResponse:
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
            payload.update(max_completion_tokens=2048, reasoning_effort="low",
                           include_reasoning=True)
        elif model == "qwen/qwen3.8-27b":
            # Parsed mode keeps the final JSON machine-readable and exposes the
            # provider-returned rationale separately for the operator trace.
            payload.pop("max_tokens")
            payload.update(max_completion_tokens=2048, reasoning_effort="low",
                           reasoning_format="parsed")
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
        message = payload["choices"][0]["message"]
        return LLMResponse(content=message["content"],
                           reasoning=message.get("reasoning"))


def _clamp(value: float, low: float, high: float) -> float:
    if not math.isfinite(value):
        raise ValueError("Strategy values must be finite")
    return max(low, min(high, value))


def _display_reasoning(reasoning: object, limit: int = 1200) -> str | None:
    """Make provider reasoning safe and compact enough for an activity feed."""
    if not isinstance(reasoning, str):
        return None
    # The activity UI is plain text. Remove Markdown decoration and the model's
    # repetitive preamble while retaining the provider-returned rationale.
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", reasoning)
    text = re.sub(r"^\s*(?:Let me |I'll |I will )?(?:analyze|reason through|assess)"
                  r"(?: the situation)?\s*:\s*", "", text,
                  flags=re.IGNORECASE)
    text = " ".join(text.split())
    if not text:
        return None
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"
