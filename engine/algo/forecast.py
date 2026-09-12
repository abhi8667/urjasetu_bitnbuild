"""EWMA forecasting. Target hour 6."""
from __future__ import annotations


def ewma(history: list[float], alpha: float = 0.3) -> float:
    """Exponentially weighted mean, most recent weighted highest.

    Empty history returns 0.0. Single value returns that value.

    Recursive form (seeded on the first observation, matching pandas'
    ewm(adjust=False)):
        S_1 = x_1
        S_t = alpha * x_t + (1 - alpha) * S_(t-1)

    The caller supplies same-block-of-day history; this function does not
    know about blocks or days.
    """
    if not history:
        return 0.0
    result = history[0]
    for x in history[1:]:
        result = alpha * x + (1 - alpha) * result
    return result