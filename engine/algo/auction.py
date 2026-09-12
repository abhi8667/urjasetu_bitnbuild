"""Uniform-price double auction. Target hour 6."""
from __future__ import annotations

from dataclasses import replace

from engine.domain import ClearingResult, Order, Trade


def clear(orders: list[Order]) -> ClearingResult:
    """Uniform-price double auction (PRD §6.3).

    Offers sorted ascending, bids sorted descending by limit_price (tie-break
    by order_id, so identical input always produces identical output). Walk
    both while bid.limit >= offer.limit; partial fills are permitted.
    clearing_price is the midpoint of the LAST matched pair's limit prices —
    every trade settles there, not at its own limit.

    Invariants:
      MK1  total traded <= min(total offered, total bid)
      MK2  seller.limit <= clearing_price <= buyer.limit, for every trade
      MK3  identical order lists -> identical trade lists, including trade_ids
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
            clearing_price=price,          # provisional, rewritten below
            curtailed_fraction=0.0,
        ))
        rem_o[i] -= qty
        rem_b[j] -= qty
        if rem_o[i] <= 1e-9:
            i += 1
        if rem_b[j] <= 1e-9:
            j += 1

    # Every trade settles at the LAST matched pair's midpoint, not its own —
    # rewrite all trades' price in one pass. dataclasses.replace() is used
    # instead of **t.__dict__ so this still works if Trade ever adds __slots__.
    if price is not None:
        trades = [replace(t, clearing_price=price) for t in trades]

    # Unmatched orders must carry their REMAINING quantity, not the original —
    # otherwise a partially-filled order looks fully untraded to whoever
    # carries these into the next block's order book (silent double-count).
    unmatched_offers = [replace(o, quantity_kwh=rem_o[k])
                        for k, o in enumerate(offers) if rem_o[k] > 1e-9]
    unmatched_bids = [replace(b, quantity_kwh=rem_b[k])
                      for k, b in enumerate(bids) if rem_b[k] > 1e-9]

    return ClearingResult(
        trades=trades,
        clearing_price=price,
        unmatched_offers=unmatched_offers,
        unmatched_bids=unmatched_bids,
    )