"""The three LLM call sites. Cuttable — the engine must run fully without them."""
from __future__ import annotations

from engine.domain import Breach, StrategyParams, TransformerState


def daily_strategy(weather, price_history) -> StrategyParams:
    """STUB: defaults. Never blocks the tick path (PRD §5.4 / P2)."""
    return StrategyParams()


def diagnose(breach: Breach, state: TransformerState) -> str:
    """STUB: templated string."""
    return (f"{breach.transformer_id} {breach.kind} breach at "
            f"{breach.severity:.0%} of limit; {state.life_used_frac:.4%} of "
            f"insulation life consumed.")
