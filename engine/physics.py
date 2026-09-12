"""The two unit conversions every agent has to agree on. Owner: B.

Small on purpose. Before this file existed, `POWER_FACTOR = 0.95` was a
module-level literal in `grid/flow.py` and `grid/health.py`, an inline `/ 0.95`
in `engine/sim/baseline.py`, and *absent entirely* from `grid/sentinel.py`. The
result was that the sentinel measured a transformer at 5.3% lower loading than
the health agent measured the same transformer in the same block, and the LP
solved against a third figure again.

That is not a rounding difference. F_AA is exponential in hot-spot temperature,
so a 5% error in K compounds into a large error in loss-of-life — and the
comparison against the baseline is only meaningful if both sides measure the
same way.

So: one function, one config field, no literals. If you are about to write
`/ 0.95` anywhere, call `apparent_kva` instead.
"""
from __future__ import annotations


def apparent_kva(net_kw: float, power_factor: float) -> float:
    """Real power (kW, what the meters report) -> apparent power (kVA, what a
    transformer is rated in).

    A 100 kVA transformer carrying 100 kW at 0.95 pf is drawing 105.3 kVA and is
    genuinely overloaded. Treating kW and kVA as interchangeable understates
    every loading figure by 1/pf.

    A non-positive power factor is meaningless; it returns the kW unchanged
    rather than dividing by zero, which keeps this total for any config a test
    might construct.
    """
    if power_factor <= 0.0:
        return abs(net_kw)
    return abs(net_kw) / power_factor


def loading_k(net_kw: float, rating_kva: float, power_factor: float) -> float:
    """K = apparent load / rated kVA. The single definition of transformer
    loading used by the sentinel, the health agent, the LP and the baseline.

    Zero or negative rating returns 0.0 rather than raising — matches the
    "never raise for bad domain input" rule the algo layer follows.
    """
    if rating_kva <= 0.0:
        return 0.0
    return apparent_kva(net_kw, power_factor) / rating_kva


def kw_headroom_for(rating_kva: float, loading_limit: float,
                    power_factor: float) -> float:
    """The most REAL power (kW) a transformer may carry before K exceeds the
    limit — the inverse of `loading_k`.

    This is what the reshape LP needs: it builds its constraint rows in kW, so it
    has to be handed a kW bound, not a kVA rating. Handing it the raw kVA figure
    made its constraint looser than the sentinel's and the reshape came back
    "feasible" while the re-check still breached.
    """
    return max(0.0, rating_kva * loading_limit * max(0.0, power_factor))
