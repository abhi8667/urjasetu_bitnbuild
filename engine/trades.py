"""What a Trade's quantity actually means. Owner: B.

One function, because the answer was ambiguous and two modules disagreed.

`Trade` carries both `quantity_kwh` and `curtailed_fraction`, and there are two
defensible conventions:

  (a) quantity_kwh is the ORIGINAL commitment, curtailed_fraction says how much
      of it survived  ->  delivered = quantity_kwh * (1 - curtailed_fraction)
  (b) quantity_kwh is ALREADY the surviving quantity, curtailed_fraction is a
      record of what was taken  ->  delivered = quantity_kwh

`FlowAgent.fallback_curtail` writes (b): it scales quantity_kwh by rho and sets
curtailed_fraction to 1 - rho. `SettlementAgent` reads (b) and bills the
quantity directly. `GridSentinel` read (a), so it discounted every curtailed
trade twice — rho squared — and any re-check after a curtailment was measuring a
street that did not exist.

(b) is the convention, because it is the one that makes a Trade self-describing:
a downstream reader that knows nothing about curtailment still gets the right
number from `quantity_kwh`. `curtailed_fraction` is provenance, not a multiplier.

Call `delivered_kwh(trade)` rather than reaching for either field, and the
question cannot be got wrong again.
"""
from __future__ import annotations

from engine.domain import Trade


def delivered_kwh(trade: Trade) -> float:
    """The kWh this trade actually moves. Convention (b) — see module docstring.

    `quantity_kwh` is already net of any curtailment the flow agent applied, so
    this is deliberately an identity. It exists to be the thing you call, so
    that the convention lives in one place with the reasoning attached rather
    than being re-derived (differently) at each call site.
    """
    return trade.quantity_kwh


def original_kwh(trade: Trade) -> float:
    """What the trade was before the flow agent touched it.

    The inverse of the curtailment: if rho of the original survived, the
    original was delivered / rho. Used for reporting "you asked for X, you got
    Y", never for physics or for money.
    """
    retained = 1.0 - trade.curtailed_fraction
    if retained <= 1e-9:
        return 0.0
    return trade.quantity_kwh / retained
