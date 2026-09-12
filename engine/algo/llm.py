"""The three LLM call sites, backed by Groq. Cuttable — the engine must run
fully without them, and PRD integration check 10 is exactly that claim.

Three things this file used to get wrong:

  * `LLM_ENABLED = _cfg_get("llm_enabled", False)` read a MODULE attribute off
    `engine.config`. `llm_enabled` is a field on the `Config` dataclass, not a
    module global, so the lookup always fell through to the default and setting
    `llm_enabled: true` in config.yaml did nothing at all. Deployment config
    lives in `engine/settings.py` now, read from the environment.
  * `_call_llm` raised NotImplementedError — there was no backend.
  * `_CLAMPS` guessed that the spec's ".margin" meant `bid_aggression`, with an
    OPEN QUESTION comment. `StrategyParams` has a `margin` field. It is `margin`.

WHAT THE LLM IS AND IS NOT ALLOWED TO DO (LM1). It may move exactly four
strategy numbers, each clamped to a configured range before anything downstream
sees it, and it may write one line of prose for the trace. It cannot clear a
market, move a kWh, price a trade, or touch an invariant. If Groq is slow,
absent, rate-limited, or returns nonsense, every function here returns the
deterministic answer instead and the run is unaffected — which is why the engine
can be demonstrated with no key at all.
"""
from __future__ import annotations

import json

from engine.domain import Breach, StrategyParams, TransformerState
from engine import settings

#: Read once at import, from the environment. True only when the feature is
#: switched on AND a key exists — see engine/settings.py.
LLM_ENABLED = settings.LLM_ENABLED

# LM1: the only fields the LLM may move, each with the range it is clamped to.
# `margin` is a real field on StrategyParams — the previous "best guess" mapping
# onto bid_aggression, with the open question left in a comment, meant the LLM's
# answer for margin was silently applied to a different parameter.
_CLAMPS = {
    "discount": (0.50, 1.00),
    "margin": (0.02, 0.30),
    "bid_aggression": (0.50, 1.50),
    "battery_reserve_frac": (0.00, 0.60),
}

#: Fields the LLM is NOT permitted to move, however it answers. Kept as data so
#: the rule is inspectable rather than buried in the assignment below.
_LM1_FROZEN = ("battery_reserve_frac",)

_SYSTEM_PROMPT = (
    "You are a trading-strategy advisor for a peer-to-peer electricity market on "
    "a single low-tension distribution street in Bengaluru. You never move energy "
    "and never set prices; you only suggest bidding posture. Answer with a JSON "
    "object and nothing else."
)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _call_llm(prompt: str, timeout: float, system: str = _SYSTEM_PROMPT) -> str:
    """The single real network call site.

    Every public function below routes through here, so it is the only thing a
    test needs to monkeypatch to simulate a timeout, a malformed response, or a
    normal reply. It raises on any failure — by design, so that "no backend
    configured" reaches exactly the same fallback path a genuine timeout would,
    and there is no second, untested code path that only runs in production.
    """
    if not LLM_ENABLED:
        raise RuntimeError("LLM disabled: see URJASETU_LLM_ENABLED / GROQ_API_KEY")

    import httpx           # imported lazily so the engine runs without httpx

    response = httpx.post(
        f"{settings.GROQ_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}",
                 "Content-Type": "application/json"},
        json={
            "model": settings.GROQ_MODEL,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 400,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def daily_strategy(weather, price_history, previous: StrategyParams | None = None,
                   timeout: float | None = None) -> StrategyParams:
    """Set once per simulated day. Returns clamped params; on any failure,
    returns `previous` unchanged.

    `previous` is not in the spec's literal two-argument signature, but "returns
    the previous params unchanged" is meaningless for a stateless function
    without some way to know what previous was. Optional third argument, so a
    two-argument caller still works.
    """
    previous = previous or StrategyParams()
    if not LLM_ENABLED:
        return previous

    timeout = settings.GROQ_TIMEOUT_SECONDS if timeout is None else timeout
    try:
        raw = _call_llm(
            "Given today's weather and the recent clearing prices, suggest a "
            "bidding posture for the coming day.\n"
            f"weather: {weather}\n"
            f"recent clearing prices (INR/kWh): {price_history}\n\n"
            'Reply as JSON: {"discount": <0.5-1.0>, "margin": <0.02-0.30>, '
            '"bid_aggression": <0.5-1.5>}\n'
            "discount: how far below the expected evening price a seller will "
            "accept now. margin: how far below its retail tariff a buyer bids. "
            "bid_aggression: willingness to compete for scarce surplus.",
            timeout)
        data = json.loads(_extract_json(raw))
        values = {}
        for name, (lo, hi) in _CLAMPS.items():
            if name in _LM1_FROZEN or name not in data:
                continue
            values[name] = _clamp(float(data[name]), lo, hi)
        # LM1: battery_reserve_frac is never the LLM's to move — it is the lever
        # that keeps energy available for the evening peak, which is a grid
        # decision, not a trading one.
        return StrategyParams(
            discount=values.get("discount", previous.discount),
            margin=values.get("margin", previous.margin),
            battery_reserve_frac=previous.battery_reserve_frac,
            bid_aggression=values.get("bid_aggression", previous.bid_aggression),
        )
    except Exception:
        # Deliberately broad. Network error, HTTP error, bad JSON, a key that is
        # not a number — the answer is the same in every case, and it is the
        # answer the engine runs on by default anyway.
        return previous


def _templated_diagnosis(breach: Breach, state: TransformerState) -> str:
    return (f"{breach.transformer_id} {breach.kind} breach at "
            f"{breach.severity:.0%} of limit; {state.life_used_frac:.4%} of "
            f"insulation life consumed.")


def diagnose(breach: Breach, transformer_state: TransformerState,
             timeout: float | None = None) -> str:
    """One plain line for the operator trace. On any failure, the templated
    string — which is itself accurate, just less readable."""
    if not LLM_ENABLED:
        return _templated_diagnosis(breach, transformer_state)
    timeout = settings.GROQ_TIMEOUT_SECONDS if timeout is None else timeout
    try:
        raw = _call_llm(
            "Explain this distribution-transformer constraint breach to a "
            "control-room operator in ONE sentence, under 30 words. Be concrete "
            "and do not speculate beyond the numbers given.\n"
            f"transformer: {breach.transformer_id}\n"
            f"breach kind: {breach.kind}\n"
            f"severity (1.0 = exactly at limit): {breach.severity:.3f}\n"
            f"hot-spot: {transformer_state.hotspot_c} C\n"
            f"loading K: {transformer_state.loading_k}\n"
            f"insulation life used: {transformer_state.life_used_frac:.6f}",
            timeout, system="You are a terse electricity distribution operator.")
        line = raw.strip().splitlines()[0] if raw and raw.strip() else ""
        if not line:
            raise ValueError("empty response")
        return line
    except Exception:
        return _templated_diagnosis(breach, transformer_state)


def answer(question: str, block_history, timeout: float | None = None) -> str:
    """Operator Q&A over the recent block history. On failure, 'unavailable' —
    never a guess, because a confident wrong answer about the grid is worse than
    no answer."""
    if not LLM_ENABLED:
        return "unavailable"
    timeout = settings.GROQ_TIMEOUT_SECONDS if timeout is None else timeout
    try:
        raw = _call_llm(
            "Answer the operator's question using ONLY the block history below. "
            "If the history does not contain the answer, say so plainly. Do not "
            "invent numbers.\n\n"
            f"question: {question}\n\n"
            f"recent blocks: {json.dumps(block_history, default=str)[:6000]}",
            timeout, system="You are a careful grid operations assistant. You "
                            "never state a figure that is not in the data given.")
        if not raw or not raw.strip():
            raise ValueError("empty response")
        return raw.strip()
    except Exception:
        return "unavailable"


def _extract_json(raw: str) -> str:
    """Pull the JSON object out of a reply that may be fenced or prefaced.

    Models wrap JSON in ```json fences or add a sentence before it even when
    told not to. Rather than let that count as a malformed response and lose an
    otherwise-good answer, take the outermost braces.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start != -1 and end > start else text
