"""Scaffolding order source — PHASE 1 ONLY, replaced in Phase 2.

This exists so the tick loop can be exercised end to end before the real
prosumer and consumer agents exist. It has no forecasting, no price memory, no
strategy and no battery: it reads the block's ticks and turns surplus into an
offer and deficit into a bid, at the crudest defensible limits.

Delete this file when `agents/prosumer.py` and `agents/consumer.py` land. It is
not a fallback and it is not a baseline — it is a smoke test with a pulse.
"""
from __future__ import annotations

from engine.config import Config
from engine.domain import House, MeterTick, Order


class NaiveOrderSource:
    def __init__(self, houses: list[House], config: Config):
        self.houses = {h.house_id: h for h in houses}
        self.config = config

    def build(self, block: int, ticks: list[MeterTick]) -> list[Order]:
        orders: list[Order] = []
        for tick in sorted(ticks, key=lambda t: t.house_id):
            house = self.houses[tick.house_id]
            net = tick.gen_kwh - tick.load_kwh
            if net > self.config.min_order_kwh:
                orders.append(Order(
                    order_id=f"O{block}-{tick.house_id}",
                    block=block,
                    house_id=tick.house_id,
                    side="offer",
                    quantity_kwh=round(net, 6),
                    limit_price=self.config.feed_in_tariff,
                ))
            elif -net > self.config.min_order_kwh:
                orders.append(Order(
                    order_id=f"B{block}-{tick.house_id}",
                    block=block,
                    house_id=tick.house_id,
                    side="bid",
                    # Capped so scarce surplus is competed for rather than
                    # swallowed by whichever buyer sorts first (DECISIONS.md D10).
                    quantity_kwh=round(min(-net, self.config.max_bid_kwh_per_block), 6),
                    limit_price=round(house.retail_tariff * 0.9, 4),
                ))
        return orders
