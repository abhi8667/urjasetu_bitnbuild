"""EWMA forecasting. Target hour 6."""
from __future__ import annotations


def ewma(history: list[float], alpha: float = 0.3) -> float:
    """STUB: last observation, or 0.0 on an empty history.

    Real implementation: exponentially weighted mean over the same block-of-day
    across the last 7 simulated days.
    """
    return history[-1] if history else 0.0
