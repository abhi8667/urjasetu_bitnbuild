"""THE TICK LOOP. Owner: B.

The sequence is the system's argument, and it lives here:

    ticks -> orders -> clear -> check -> [reshape -> re-clear -> fallback]
          -> age -> settle

Read top to bottom: economics proposes, physics disposes, money follows the
physics. Net metering has no middle — no step asks whether the grid could carry
the trade, and no step charges anyone for the wear. That missing middle is the
project, and it is four lines of this file.

Phase 1: C's grid agents and B's settlement are optional. Absent, the loop runs
clear-only and the sequence above still holds — the branches are simply not
taken. Nothing here changes when they arrive.
"""
from __future__ import annotations

import time
from typing import Any, Protocol

from engine.agents.market import MarketAgent
from engine.bus import Bus
from engine.config import DEFAULT, Config
from engine.domain import InvariantError, MeterTick, Order, Trade
from engine.feed import WhitefieldFeed

AGENT_ID = "runner"

#: Invariant P1. Hardcoded, not configurable: someone will want to raise it at
#: hour 29 to get past a bug, and that is exactly when it must not move.
MAX_CLEARING_PASSES = 2


class OrderSource(Protocol):
    def build(self, block: int, ticks: list[MeterTick], feed=None) -> list[Order]: ...


class Sentinel(Protocol):
    def check(self, trades: list[Trade], ticks: list[MeterTick]) -> Any | None: ...


class Flow(Protocol):
    def reshape(self, trades, ticks, breach, batteries) -> Any: ...
    def fallback_curtail(self, trades: list[Trade], breach) -> list[Trade]: ...


class Health(Protocol):
    def apply(self, trades: list[Trade], ticks: list[MeterTick]) -> Any: ...


class Settlement(Protocol):
    def settle(self, trades: list[Trade], ageing) -> Any: ...


class Runner:
    def __init__(self, feed: WhitefieldFeed, orders: OrderSource,
                 config: Config = DEFAULT, bus: Bus | None = None,
                 sentinel: Sentinel | None = None, flow: Flow | None = None,
                 health: Health | None = None, settlement: Settlement | None = None,
                 batteries: Any = None, persist=None, start_block: int = 0):
        self.feed = feed
        self.orders = orders
        self.config = config
        self.bus = bus or Bus()
        self.market = MarketAgent(self.bus, feed.houses(), config)
        self.sentinel = sentinel
        self.flow = flow
        self.health = health
        self.settlement = settlement
        self.batteries = batteries
        self.persist = persist
        # Resume support (PS1): a run that died at block N restarts here.
        self.start_block = start_block
        self.tick_durations_ms: list[float] = []

    # ------------------------------------------------------------------ loop

    def run(self, blocks: int | None = None) -> dict:
        total = blocks if blocks is not None else self.feed.total_blocks()
        summary = _Accumulator()
        for block in range(self.start_block, total):
            started = time.perf_counter()
            self._tick(block, summary)
            self.tick_durations_ms.append((time.perf_counter() - started) * 1000)
        return summary.finalise(self.config, total - self.start_block)

    @property
    def median_tick_ms(self) -> float:
        """Invariant P3, measured — deliberately NOT in run_summary.

        D1 requires two identical runs to produce a byte-identical summary, and
        wall-clock timing never is. Timing is a measurement of the run, not a
        result of it. Report it beside the summary, never inside it.
        """
        ordered = sorted(self.tick_durations_ms)
        return ordered[len(ordered) // 2] if ordered else 0.0

    def _tick(self, block: int, summary: "_Accumulator") -> None:
        self.bus.publish("block_opened", block, AGENT_ID, {
            "hour": block % self.config.blocks_per_day,
            "day": block // self.config.blocks_per_day,
        })

        ticks = self.feed.ticks(block)
        orders = _build_orders(self.orders, block, ticks, self.feed)
        for order in orders:
            self.bus.publish("order_submitted", block, order.house_id, {
                "order_id": order.order_id,
                "side": order.side,
                "quantity_kwh": order.quantity_kwh,
                "limit_price": order.limit_price,
            })

        result = self.market.clear(block, orders)
        passes = 1
        breach = self.sentinel.check(result.trades, ticks) if self.sentinel else None

        if breach is not None:
            self.bus.publish("breach_detected", block, "sentinel", {
                "transformer_id": breach.transformer_id,
                "kind": breach.kind,
                "severity": round(breach.severity, 4),
            })
            if self.flow is not None:
                plan = self.flow.reshape(result.trades, ticks, breach, self.batteries)
                self.bus.publish("reshape_proposed", block, "flow", {
                    "feasible": plan.feasible,
                    "objective_value": plan.objective_value,
                })
                if plan.feasible:
                    result = self.market.clear(block, plan.constrained_orders)
                    passes += 1
                    self.bus.publish("reshape_applied", block, "flow", {
                        "trades": len(result.trades),
                        "battery_charges": len(plan.battery_charges),
                    })
                breach = self.sentinel.check(result.trades, ticks)
                if breach is not None:
                    # P1: the second failed check is final. fallback_curtail is
                    # unconditional and always succeeds — no third pass, ever.
                    result.trades[:] = self.flow.fallback_curtail(result.trades, breach)
                    self.bus.publish("fallback_curtailed", block, "flow", {
                        "transformer_id": breach.transformer_id,
                        "trades": len(result.trades),
                    })

        if passes > MAX_CLEARING_PASSES:
            raise InvariantError(
                f"P1: block {block} used {passes} clearing passes, bound is "
                f"{MAX_CLEARING_PASSES}")

        ageing = self.health.apply(result.trades, ticks) if self.health else None
        if ageing is not None:
            self.bus.publish("ageing_applied", block, "health", {
                "life_used_frac": ageing.life_used_frac,
                "ageing_adder": ageing.ageing_adder,
            })

        bills = (self.settlement.settle(result.trades, ageing, block)
                 if self.settlement else None)

        # Agents learn here and nowhere else (PR3). Fanning out after settlement
        # means their history includes this block's outcome, never this block's
        # own decision.
        if hasattr(self.orders, "on_settled"):
            self.orders.on_settled(block, ticks, result.trades,
                                   result.clearing_price, bills or [])

        if self.persist is not None:
            # Accepts a Persistence instance or any callable with the same shape.
            writer = getattr(self.persist, "write_block", self.persist)
            writer(block, ticks, orders, result.trades, bills, ageing)

        summary.record(block, ticks, orders, result, passes)
        self.bus.publish("block_settled", block, AGENT_ID, {
            "trades": len(result.trades),
            "clearing_price": result.clearing_price,
            "volume_kwh": round(sum(t.quantity_kwh for t in result.trades), 6),
            "bills": len(bills) if bills else 0,
        })


def _build_orders(source, block, ticks, feed):
    """Phase 1's scaffolding source takes (block, ticks); the real agent pool
    also needs the feed to forecast from. Accept both."""
    try:
        return source.build(block, ticks, feed)
    except TypeError:
        return source.build(block, ticks)


# ----------------------------------------------------------------- summary

class _Accumulator:
    """Builds `run_summary`. D1 asserts two identical runs produce this
    byte-identical, so every value here is rounded and every dict is sorted."""

    def __init__(self):
        self.blocks = 0
        self.orders = 0
        self.trades = 0
        self.volume_kwh = 0.0
        self.gen_kwh = 0.0
        self.load_kwh = 0.0
        self.prices: list[float] = []
        self.blocks_with_trades = 0
        self.reshaped_blocks = 0

    def record(self, block, ticks, orders, result, passes) -> None:
        self.blocks += 1
        self.orders += len(orders)
        self.trades += len(result.trades)
        self.volume_kwh += sum(t.quantity_kwh for t in result.trades)
        self.gen_kwh += sum(t.gen_kwh for t in ticks)
        self.load_kwh += sum(t.load_kwh for t in ticks)
        if result.trades:
            self.blocks_with_trades += 1
        if result.clearing_price is not None:
            self.prices.append(result.clearing_price)
        if passes > 1:
            self.reshaped_blocks += 1

    def finalise(self, config: Config, total: int) -> dict:
        return {
            "blocks": self.blocks,
            "block_minutes": config.block_minutes,
            "days": round(total / config.blocks_per_day, 4),
            "orders_submitted": self.orders,
            "trades": self.trades,
            "traded_kwh": round(self.volume_kwh, 6),
            "generation_kwh": round(self.gen_kwh, 6),
            "consumption_kwh": round(self.load_kwh, 6),
            "p2p_share_of_demand_pct": round(
                100 * self.volume_kwh / self.load_kwh, 4) if self.load_kwh else 0.0,
            "blocks_with_trades": self.blocks_with_trades,
            "reshaped_blocks": self.reshaped_blocks,
            "mean_clearing_price_inr": round(
                sum(self.prices) / len(self.prices), 4) if self.prices else None,
            "seed": config.seed,
            # A perfect-foresight feed is acceptable for testing only, and the
            # run summary must say so (PRD §3.2).
            "perfect_foresight": config.forecast_noise_frac == 0.0,
        }
