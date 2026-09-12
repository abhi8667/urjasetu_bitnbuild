"""Consumer agent — bids for deficit. Owner: B. One per premises.

Owns a spend ledger, which is what makes CN2 checkable.
"""
from __future__ import annotations

from collections import defaultdict, deque

from engine import algo
from engine.config import Config
from engine.domain import (BillLine, House, InvariantError, MeterTick, Order,
                           StrategyParams)


class ConsumerAgent:
    def __init__(self, house: House, config: Config, rng=None,
                 strategy: StrategyParams | None = None):
        self.house = house
        self.config = config
        self.rng = rng
        self.strategy = strategy or StrategyParams()
        self.cumulative_spend = 0.0
        self.cumulative_baseline = 0.0
        self.kwh_bought = 0.0
        self._gen_hist: dict[int, deque[float]] = defaultdict(
            lambda: deque(maxlen=config.forecast_history_days))
        self._load_hist: dict[int, deque[float]] = defaultdict(
            lambda: deque(maxlen=config.forecast_history_days))

    # ------------------------------------------------------------- decision

    def build_bid(self, block: int, feed) -> Order | None:
        deficit = self.forecast_deficit_kwh(block, feed)
        if deficit <= self.config.min_order_kwh:
            return None
        ceiling = self.bid_ceiling()
        return Order(
            order_id=f"B{block}-{self.house.house_id}",
            block=block,
            house_id=self.house.house_id,
            side="bid",
            # Capped so scarce surplus is competed for rather than swallowed by
            # whichever buyer happens to sort first. Supply is short on this
            # street every single block (DECISIONS.md D10).
            quantity_kwh=round(min(deficit, self.config.max_bid_kwh_per_block), 6),
            limit_price=ceiling,
        )

    def forecast_deficit_kwh(self, block: int, feed) -> float:
        hour = block % self.config.blocks_per_day
        belief = feed.forecast(self.house.house_id, block, 1)
        feed_deficit = (belief[0].load_kwh - belief[0].gen_kwh) if belief else 0.0
        gen_hist, load_hist = self._gen_hist[hour], self._load_hist[hour]
        if not load_hist:
            return max(0.0, feed_deficit)
        own = (algo.forecast.ewma(list(load_hist), self.config.forecast_alpha)
               - algo.forecast.ewma(list(gen_hist), self.config.forecast_alpha))
        blend = self.config.forecast_blend
        return max(0.0, blend * own + (1 - blend) * feed_deficit)

    def bid_ceiling(self) -> float:
        """CN1, enforced at construction — a bid above the retail tariff raises.

        Not a warning, not a clamp. A household that would pay more than the
        grid charges has no reason to be in this market at all, and silently
        clamping hides the bug that produced it.

        The check used to test `retail * (1 - margin) > retail`, which is false
        for any non-negative margin and so could never fire — a dead assertion
        that read as a guarantee. What can actually go wrong is a NEGATIVE
        margin, which the LLM strategy layer could in principle hand over, so
        that is what is checked: the margin itself, at the point where a bad
        value enters, rather than an arithmetic identity downstream of it.
        """
        margin = self.strategy.margin
        if margin < 0.0:
            raise InvariantError(
                f"CN1: {self.house.house_id} has a negative bid margin "
                f"{margin} — it would bid ABOVE its retail tariff "
                f"{self.house.retail_tariff} and should not be in this market")
        ceiling = round(self.house.retail_tariff * (1 - margin), 4)
        if ceiling > self.house.retail_tariff + 1e-9:
            raise InvariantError(
                f"CN1: {self.house.house_id} bid ceiling {ceiling} exceeds retail "
                f"tariff {self.house.retail_tariff}")
        return ceiling

    # ---------------------------------------------------------------- state

    def on_settled(self, block: int, ticks: list[MeterTick],
                   bill_lines: list[BillLine] | None = None) -> None:
        """Forecast history only. The spend ledger moves in `record_purchase`,
        which the settlement agent drives — one writer, so no double count."""
        hour = block % self.config.blocks_per_day
        for tick in ticks:
            if tick.house_id == self.house.house_id:
                self._gen_hist[hour].append(tick.gen_kwh)
                self._load_hist[hour].append(tick.load_kwh)
                break

    def record_purchase(self, kwh: float, all_in_inr: float) -> None:
        """Spend ledger. Baseline is what the same kWh would have cost from the
        DISCOM at this premises' own slab rate."""
        self.kwh_bought += kwh
        self.cumulative_spend += all_in_inr
        self.cumulative_baseline += kwh * self.house.retail_tariff
        self._assert_cn2()

    def _assert_cn2(self) -> None:
        """CN2 — cumulative spend never exceeds the grid-only counterfactual.

        CN1 makes this true automatically, so CN2 should never fire. It exists to
        catch settlement bugs, not bidding bugs: if it trips, the charges stack
        (wheeling + transaction + C's ageing adder) have pushed a buyer's all-in
        cost above retail, and the fault is in settlement or in the adder — not
        here.
        """
        if self.cumulative_spend > self.cumulative_baseline + 1e-6:
            raise InvariantError(
                f"CN2: {self.house.house_id} spent Rs{self.cumulative_spend:.4f} "
                f"against a grid-only baseline of Rs{self.cumulative_baseline:.4f} "
                f"for {self.kwh_bought:.4f} kWh — the charge stack exceeds retail")
