"""Constraint-feasible trade reshaping. Target hour 20 — the critical path."""
from __future__ import annotations

from engine.domain import ReshapeSolution, Trade


def solve(trades: list[Trade], limits, batteries, topology) -> ReshapeSolution:
    """STUB: uniform curtailment bringing loading to the limit.

    Real implementation: scipy.optimize.linprog over c_t in [0,1] per trade and
    b_h >= 0 per battery house. Never raises — an infeasible program returns
    feasible=False with an empty solution and C runs the fallback.

    Invariants FL_A (retention in [0,1]), FL_B (battery_charge >= 0 and within
    both limits), FL_C (a feasible solution satisfies every constraint to 1e-6).
    """
    retention = getattr(limits, "uniform_retention", 1.0)
    return ReshapeSolution(
        retention={t.trade_id: retention for t in trades},
        battery_charge={},
        feasible=True,
        objective_value=0.0,
    )
