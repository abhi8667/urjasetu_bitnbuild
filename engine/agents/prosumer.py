"""Prosumer agent — offers surplus. Owner: B. One per PV premises.

Owns state across blocks: forecast history, observed evening prices, battery SoC.
`build_offer` is pure with respect to that state (PR3); it mutates only in
`on_settled`. Call it twice, get the same answer.
"""
from __future__ import annotations

from collections import defaultdict, deque

from engine import algo
from engine.config import Config
from engine.domain import (House, InvariantError, MeterTick, Order,
                           StorageClaim, StrategyParams, Trade)


class ProsumerAgent:
    def __init__(self, house: House, config: Config, rng=None,
                 strategy: StrategyParams | None = None):
        self.house = house
        self.config = config
        self.rng = rng
        self.strategy = strategy or StrategyParams()
        self._soc_kwh = 0.0
        self._gen_hist: dict[int, deque[float]] = defaultdict(
            lambda: deque(maxlen=config.forecast_history_days))
        self._load_hist: dict[int, deque[float]] = defaultdict(
            lambda: deque(maxlen=config.forecast_history_days))
        self._evening_prices: deque[float] = deque(
            maxlen=config.forecast_history_days * config.blocks_per_day)

    # ------------------------------------------------------------- decision

    def build_offer(self, block: int, feed) -> Order | None:
        surplus = self.forecast_surplus_kwh(block, feed)
        surplus -= self.reserve_kwh(block, feed)
        if surplus <= self.config.min_order_kwh:
            return None
        return Order(
            order_id=f"O{block}-{self.house.house_id}",
            block=block,
            house_id=self.house.house_id,
            side="offer",
            quantity_kwh=round(surplus, 6),
            limit_price=round(self.reserve_price(), 4),
        )

    def forecast_surplus_kwh(self, block: int, feed) -> float:
        """Own EWMA over the same hour-of-day across recent days, blended with
        the feed's belief. Day 1 has no history, so it is the feed's belief
        alone.

        PR2: the offer never exceeds this, plus discharge from claims the agent
        owns — and it owns none until C's battery custody lands.
        """
        hour = block % self.config.blocks_per_day
        belief = feed.forecast(self.house.house_id, block, 1)
        feed_surplus = (belief[0].gen_kwh - belief[0].load_kwh) if belief else 0.0
        gen_hist, load_hist = self._gen_hist[hour], self._load_hist[hour]
        if not gen_hist and not load_hist:
            return max(0.0, feed_surplus)
        own = (algo.forecast.ewma(list(gen_hist), self.config.forecast_alpha)
               - algo.forecast.ewma(list(load_hist), self.config.forecast_alpha))
        blend = self.config.forecast_blend
        return max(0.0, blend * own + (1 - blend) * feed_surplus)

    def reserve_kwh(self, block: int, feed) -> float:
        """Daylight surplus held back to charge this premises' own battery.

        Selling every kWh at midday leaves nothing to discharge at 19:00, which
        is when this street's transformers actually breach. Reserving a fraction
        is what turns "batteries absorb" into "the energy comes back out at the
        evening peak" — without it the flow agent has a lever with nothing behind
        it. Own-battery only: no claims, no custody, so FL4 cannot be broken by
        this path (the own-battery-only fallback of C's cut list).
        """
        if not self.house.has_battery:
            return 0.0
        surplus = self.forecast_surplus_kwh(block, feed)
        if surplus <= 0.0:
            return 0.0
        room = max(0.0, self.house.battery_kwh - self._soc_kwh)
        power = self.house.battery_max_kw * self.config.block_hours
        return min(surplus * self.strategy.battery_reserve_frac, room, power)

    def reserve_price(self) -> float:
        """floor = max(feed_in_tariff, expected_evening_price * discount)

        The evening price is the opportunity cost of selling now instead of
        storing for the peak. On this dataset no trade clears after 18:00 until
        C's batteries can discharge into the evening, so `_evening_prices` stays
        empty and the floor is the feed-in tariff — correctly, since that is
        genuinely the only alternative buyer. Expect this to start moving once
        battery discharge exists.
        """
        floor = self.config.feed_in_tariff
        if self._evening_prices:
            expected = algo.forecast.ewma(list(self._evening_prices),
                                          self.config.forecast_alpha)
            floor = max(floor, expected * self.strategy.discount)
        return floor

    # ---------------------------------------------------------------- state

    def on_settled(self, block: int, ticks: list[MeterTick], trades: list[Trade],
                   clearing_price: float | None = None,
                   claims: list[StorageClaim] | None = None,
                   battery_delta_kwh: float = 0.0) -> None:
        """THE ONLY PLACE STATE MUTATES."""
        hour = block % self.config.blocks_per_day
        for tick in ticks:
            if tick.house_id == self.house.house_id:
                self._gen_hist[hour].append(tick.gen_kwh)
                self._load_hist[hour].append(tick.load_kwh)
                break

        lo, hi = self.config.evening_blocks
        if clearing_price is not None and lo <= hour <= hi:
            self._evening_prices.append(clearing_price)

        if battery_delta_kwh:
            self._soc_kwh += battery_delta_kwh
            self._assert_soc()

    @property
    def battery_soc_kwh(self) -> float:
        return self._soc_kwh

    def _assert_soc(self) -> None:
        if not -1e-9 <= self._soc_kwh <= self.house.battery_kwh + 1e-9:
            raise InvariantError(
                f"PR1: {self.house.house_id} SoC {self._soc_kwh:.6f} kWh outside "
                f"[0, {self.house.battery_kwh}]")
