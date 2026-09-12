"""The three LLM call sites. Cuttable — the engine must run fully without them."""
from __future__ import annotations

import json

from engine.domain import Breach, StrategyParams, TransformerState

try:
    from engine import config as _cfg
except ImportError:
    _cfg = None


def _cfg_get(name, default):
    return getattr(_cfg, name, default) if _cfg is not None else default


LLM_ENABLED = _cfg_get("llm_enabled", False)

# LM1: the LLM may only ever move these two fields, each clamped to a
# configured range before anything downstream sees it.
#
# OPEN QUESTION: the real StrategyParams (domain.py) has discount,
# battery_reserve_frac, bid_aggression — no field literally named "margin".
# `bid_aggression` is my best guess for what ".margin" maps to; confirm with
# whoever owns domain.py. This dict is the one place to fix once you know —
# nothing else in this file needs to change.
_CLAMPS = {
    "discount": _cfg_get("LLM_DISCOUNT_RANGE", (0.5, 1.0)),
    "bid_aggression": _cfg_get("LLM_MARGIN_RANGE", (0.5, 1.5)),  # <- "margin" guess
}


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _call_llm(prompt: str, timeout: float) -> str:
    """The single real network call site — every public function below routes
    through here, so it's the only thing tests need to mock to simulate a
    timeout, a malformed response, or a normal reply.

    Not wired to a real provider yet (cuttable, last priority). Raises by
    design: with no backend, every call should hit exactly the same
    fallback path a genuine timeout or bad response would hit — never a
    silent, different code path just because nothing's connected."""
    raise NotImplementedError("no LLM backend wired up yet")


def daily_strategy(weather, price_history, previous: StrategyParams | None = None,
                    timeout: float = 3.0) -> StrategyParams:
    """Returns clamped params. On timeout or malformed output, returns the
    previous params unchanged.

    NOTE: `previous` isn't in the spec's literal 2-argument signature, but
    "returns the previous params unchanged" is meaningless for a pure,
    stateless function without SOME way to know what "previous" was. Added
    as an optional third argument (backward compatible with any 2-arg
    caller) defaulting to StrategyParams() if the caller has nothing to
    hand back.
    """
    previous = previous or StrategyParams()
    if not LLM_ENABLED:
        return previous

    try:
        raw = _call_llm(f"weather={weather} price_history={price_history}", timeout)
        data = json.loads(raw)
        discount = _clamp(float(data["discount"]), *_CLAMPS["discount"])
        bid_aggression = _clamp(float(data["margin"]), *_CLAMPS["bid_aggression"])
        return StrategyParams(
            discount=discount,
            battery_reserve_frac=previous.battery_reserve_frac,  # LM1: the LLM never touches this field
            bid_aggression=bid_aggression,
        )
    except Exception:
        return previous


def _templated_diagnosis(breach: Breach, state: TransformerState) -> str:
    return (f"{breach.transformer_id} {breach.kind} breach at "
            f"{breach.severity:.0%} of limit; {state.life_used_frac:.4%} of "
            f"insulation life consumed.")


def diagnose(breach: Breach, transformer_state: TransformerState, timeout: float = 2.0) -> str:
    """One plain line for the trace. On failure, a templated string."""
    if not LLM_ENABLED:
        return _templated_diagnosis(breach, transformer_state)
    try:
        raw = _call_llm(f"breach={breach} state={transformer_state}", timeout)
        line = raw.strip().splitlines()[0] if raw and raw.strip() else ""
        if not line:
            raise ValueError("empty response")
        return line
    except Exception:
        return _templated_diagnosis(breach, transformer_state)


def answer(question: str, block_history, timeout: float = 5.0) -> str:
    """On failure, 'unavailable'."""
    if not LLM_ENABLED:
        return "unavailable"
    try:
        raw = _call_llm(f"question={question} history={block_history}", timeout)
        if not raw or not raw.strip():
            raise ValueError("empty response")
        return raw.strip()
    except Exception:
        return "unavailable"