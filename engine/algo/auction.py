"""Uniform-price double auction. Target hour 6."""
from __future__ import annotations

from engine.domain import ClearingResult, Order, Trade


def clear(orders: list[Order]) -> ClearingResult:
    """STUB: greedy price-time match, no uniform pricing.

    Real implementation (PRD §6.3): sort offers ascending and bids descending by
    limit_price, walk both while bid.limit >= offer.limit, partial fills
    permitted, clearing_price is the midpoint of the LAST matched pair, and
    every trade settles at clearing_price rather than at its own limit.

    Invariants MK1 (traded <= min(offered, bid)), MK2 (seller.limit <= price <=
    buyer.limit), MK3 (identical input -> identical output, trade_ids included).
    """
    offers = sorted([o for o in orders if o.side == "offer"],
                    key=lambda o: (o.limit_price, o.order_id))
    bids = sorted([o for o in orders if o.side == "bid"],
                  key=lambda o: (-o.limit_price, o.order_id))
    trades: list[Trade] = []
    price: float | None = None
    i = j = 0
    rem_o = [o.quantity_kwh for o in offers]
    rem_b = [b.quantity_kwh for b in bids]
    while i < len(offers) and j < len(bids):
        if bids[j].limit_price < offers[i].limit_price:
            break
        qty = min(rem_o[i], rem_b[j])
        price = (offers[i].limit_price + bids[j].limit_price) / 2
        trades.append(Trade(
            trade_id=f"T{offers[i].block}-{offers[i].order_id}-{bids[j].order_id}",
            block=offers[i].block,
            seller_id=offers[i].house_id,
            buyer_id=bids[j].house_id,
            quantity_kwh=qty,
            clearing_price=price,
            curtailed_fraction=0.0,
        ))
        rem_o[i] -= qty
        rem_b[j] -= qty
        if rem_o[i] <= 1e-9:
            i += 1
        if rem_b[j] <= 1e-9:
            j += 1
    trades = [Trade(**{**t.__dict__, "clearing_price": price}) for t in trades]
    return ClearingResult(
        trades=trades,
        clearing_price=price,
        unmatched_offers=[o for k, o in enumerate(offers) if rem_o[k] > 1e-9],
        unmatched_bids=[b for k, b in enumerate(bids) if rem_b[k] > 1e-9],
    )
