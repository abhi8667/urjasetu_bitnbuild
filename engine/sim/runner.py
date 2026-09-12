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
                 batteries: Any = None, persist=None, start_block: int = 0,
                 include_baseline: bool = True, validate_feed: bool = True):
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
        self.include_baseline = include_baseline
        # PRD §12: the engine validates the entire meter feed before block 0.
        # Off only for tests that deliberately drive a partial or stub feed.
        self.validate_feed = validate_feed
        self.feed_validation: dict | None = None
        self.summary: dict | None = None
        self.tick_durations_ms: list[float] = []

    def _charge_batteries(self, block: int) -> dict[str, float]:
        reserves = getattr(self.orders, "battery_reserves", None)
        if not reserves or self.batteries is None:
            return {}
        stored: dict[str, float] = {}
        for house_id, kwh in sorted(reserves.items()):
            room = self.batteries.available_absorption_kwh(house_id)
            take = min(kwh, room)
            if take <= 1e-9:
                continue
            self.batteries.store_own_energy(house_id, take)
            stored[house_id] = round(take, 6)
            prosumer = getattr(self.orders, "prosumers", {}).get(house_id)
            if prosumer is not None:
                prosumer.on_settled(block, [], [], battery_delta_kwh=take)
        return stored

    # ------------------------------------------------------------------ loop

    def run(self, blocks: int | None = None) -> dict:
        total = blocks if blocks is not None else self.feed.total_blocks()

        # "A run that starts must be able to finish" (PRD §12). A gap found at
        # block 400 has already burned four hundred blocks and left a
        # half-written database; the same gap found here costs a tenth of a
        # second. Validation also warms the feed's tick cache, so the run that
        # follows is faster for it.
        if self.validate_feed and hasattr(self.feed, "validate"):
            self.feed_validation = self.feed.validate()

        summary = _Accumulator()
        for block in range(self.start_block, total):
            started = time.perf_counter()
            self._tick(block, summary)
            self.tick_durations_ms.append((time.perf_counter() - started) * 1000)
        self.summary = summary.finalise(
            self.config, total - self.start_block,
            settlement=self.settlement, health=self.health,
            baseline=self._baseline_figures(total))
        return self.summary

    def _baseline_figures(self, total: int) -> dict | None:
        """The counterfactual's numbers, alongside P2P's own. PRD §10 asks for
        "the same figures for the baseline path" — without them the summary
        states a result with nothing to compare it against."""
        if not self.include_baseline:
            return None
        from engine.sim.baseline import Baseline
        result = Baseline(self.feed, self.config).run(blocks=total)
        return {
            "label": result.label,
            "household_bills_inr": result.household_bills_inr,
            "discom_energy_revenue_inr": result.discom_energy_revenue_inr,
            "discom_charge_revenue_inr": result.discom_charge_revenue_inr,
            "export_credits_inr": result.export_credits_inr,
            "loss_of_life_hours_total": result.loss_of_life_hours,
        }

    def write_run_summary(self, path="run_summary.json") -> str:
        """Emit the summary as JSON. Sorted keys, so two identical runs produce
        byte-identical files (D1)."""
        import json
        summary = getattr(self, "summary", None)
        if summary is None:
            raise RuntimeError("run() must complete before writing a summary")
        with open(path, "w") as fh:
            json.dump(summary, fh, indent=2, sort_keys=True)
            fh.write("\n")
        return path

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

        # Charge policy, before the market sees the book: whatever the prosumers
        # held back is physically stored now, so the evening reshape has
        # something to discharge. Own-battery only — no claims, no custody.
        stored_now = self._charge_batteries(block)

        result = self.market.clear(block, orders)
        settled_ticks = ticks
        plan = None
        passes = 1
        breach = self.sentinel.check(result.trades, ticks) if self.sentinel else None

        if breach is not None:
            summary.record_breach(breach.kind)
            self.bus.publish("breach_detected", block, "sentinel", {
                "transformer_id": breach.transformer_id,
                "kind": breach.kind,
                "severity": round(breach.severity, 4),
            })
            # Only a loading breach is reshapable. Phase imbalance is a property
            # of which premises sit on A, B and C — curtailing a trade or moving
            # a kWh cannot change it, and 210 of 270 breaches on this street are
            # phase. The sentinel keeps reporting them for the trace; the flow
            # agent is not asked to fix what it cannot.
            if self.flow is not None and breach.kind == "loading":
                plan = self.flow.reshape(result.trades, ticks, breach, self.batteries)
                self.bus.publish("reshape_proposed", block, "flow", {
                    "feasible": plan.feasible,
                    "objective_value": plan.objective_value,
                })
                if plan.feasible:
                    result = self.market.clear(block, plan.constrained_orders)
                    passes += 1
                    summary.reshapes_applied += 1
                    self.bus.publish("reshape_applied", block, "flow", {
                        "trades": len(result.trades),
                        "battery_charges": len(plan.battery_charges),
                        "battery_discharge_kwh": round(
                            sum(plan.battery_discharges.values()), 6),
                    })
                # A battery discharging behind the meter reduces that premises'
                # net import, and absorbing raises it. The sentinel reads ticks,
                # so the re-check must read ticks that reflect what the reshape
                # actually did — otherwise it re-measures the unreshaped street
                # and every reshape looks like it failed.
                for house_id, kwh in (plan.battery_discharges or {}).items():
                    prosumer = getattr(self.orders, "prosumers", {}).get(house_id)
                    if prosumer is not None:
                        prosumer.on_settled(block, [], [], battery_delta_kwh=-kwh)
                settled_ticks = _apply_battery(ticks, plan, self.config)
                breach = self.sentinel.check(result.trades, settled_ticks)
                if breach is not None:
                    # P1: the second failed check is final. fallback_curtail is
                    # unconditional and always succeeds — no third pass, ever.
                    result.trades[:] = self.flow.fallback_curtail(result.trades, breach)
                    summary.fallbacks += 1
                    self.bus.publish("fallback_curtailed", block, "flow", {
                        "transformer_id": breach.transformer_id,
                        "trades": len(result.trades),
                    })

        # One event carrying every kWh that entered or left a battery this
        # block. FL4 is unverifiable without it: charging happens before the
        # market and discharging inside the reshape, so nothing downstream
        # could otherwise see both halves of the battery term.
        discharged_now = dict(getattr(plan, "battery_discharges", {}) or {}) if plan else {}
        summary.battery_charged_kwh += sum(stored_now.values())
        summary.battery_discharged_kwh += sum(discharged_now.values())
        if stored_now or discharged_now:
            self.bus.publish("battery_moved", block, "runner", {
                "charged_kwh": {k: round(v, 9) for k, v in sorted(stored_now.items())},
                "discharged_kwh": {k: round(v, 9) for k, v in sorted(discharged_now.items())},
                "net_into_batteries_kwh": round(
                    sum(stored_now.values()) - sum(discharged_now.values()), 9),
            })

        if passes > MAX_CLEARING_PASSES:
            raise InvariantError(
                f"P1: block {block} used {passes} clearing passes, bound is "
                f"{MAX_CLEARING_PASSES}")

        # Forecast commitments meet physical reality here. Offers are built from
        # an EWMA forecast, so a seller can commit to more than it turns out to
        # have generated, and nothing downstream reconciled the two: 12.3% of
        # all traded energy was being sold by premises that never produced it.
        # That is energy the market moved and the street never made, and it is
        # exactly the "inventing kilowatt-hours" failure FL4 exists to catch.
        # The undeliverable share is trimmed; the seller simply earns less,
        # which is the natural economic consequence of a bad forecast.
        result.trades[:], shortfalls = _reconcile_delivery(
            result.trades, settled_ticks, discharged_now, self.config)
        summary.delivery_shortfall_kwh += sum(shortfalls.values())
        if shortfalls:
            self.bus.publish("delivery_shortfall", block, "runner", {
                "sellers": len(shortfalls),
                "kwh": round(sum(shortfalls.values()), 6),
            })

        # The health agent reads ticks, so hand it the ticks the reshape left
        # behind — a battery that discharged genuinely reduced what the iron
        # carried, and the ageing must reflect that or the reshape buys nothing.
        ageing = (self.health.apply(result.trades, settled_ticks)
                  if self.health else None)
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


def _reconcile_delivery(trades: list[Trade], ticks: list[MeterTick],
                        discharged: dict[str, float], config):
    """Trim every trade to energy its seller physically had this block.

    A premises can deliver its own surplus (generation beyond its own load) plus
    whatever it discharged from its battery. Anything it committed beyond that
    does not exist, and clearing it anyway creates kWh from nothing.

    Where a seller is short, its trades are scaled down proportionally rather
    than cancelled — every buyer takes the same haircut, which is the neutral
    treatment, and no single counterparty absorbs one seller's forecast error.
    """
    by_house = {t.house_id: t for t in ticks}
    sold: dict[str, float] = {}
    for trade in trades:
        sold[trade.seller_id] = sold.get(trade.seller_id, 0.0) + trade.quantity_kwh

    scale: dict[str, float] = {}
    shortfalls: dict[str, float] = {}
    for seller_id, committed in sold.items():
        tick = by_house.get(seller_id)
        surplus = max(0.0, tick.gen_kwh - tick.load_kwh) if tick else 0.0
        available = surplus + discharged.get(seller_id, 0.0)
        if committed > available + 1e-9:
            scale[seller_id] = available / committed if committed > 0 else 0.0
            shortfalls[seller_id] = round(committed - available, 9)

    if not scale:
        return list(trades), {}

    out = []
    for trade in trades:
        factor = scale.get(trade.seller_id)
        if factor is None:
            out.append(trade)
            continue
        delivered = trade.quantity_kwh * factor
        if delivered <= config.min_order_kwh * 0.01:
            continue          # nothing meaningful left to settle
        out.append(Trade(
            trade_id=trade.trade_id, block=trade.block, seller_id=trade.seller_id,
            buyer_id=trade.buyer_id, quantity_kwh=round(delivered, 9),
            clearing_price=trade.clearing_price,
            curtailed_fraction=trade.curtailed_fraction))
    return out, shortfalls


def _apply_battery(ticks: list[MeterTick], plan, config) -> list[MeterTick]:
    """Ticks as the meters would read them after the reshape moved battery energy."""
    charge = getattr(plan, "battery_charges", None) or {}
    discharge = getattr(plan, "battery_discharges", None) or {}
    if not charge and not discharge:
        return ticks
    out = []
    for tick in ticks:
        delta = charge.get(tick.house_id, 0.0) - discharge.get(tick.house_id, 0.0)
        if delta == 0.0:
            out.append(tick)
            continue
        out.append(MeterTick(block=tick.block, house_id=tick.house_id,
                             load_kwh=max(0.0, tick.load_kwh + delta),
                             gen_kwh=tick.gen_kwh, ambient_c=tick.ambient_c))
    return out


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
        self.breaches_by_kind: dict[str, int] = {}
        self.reshapes_applied = 0
        self.fallbacks = 0
        self.battery_charged_kwh = 0.0
        self.battery_discharged_kwh = 0.0
        self.delivery_shortfall_kwh = 0.0

    def record_breach(self, kind: str) -> None:
        self.breaches_by_kind[kind] = self.breaches_by_kind.get(kind, 0) + 1

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

    def finalise(self, config: Config, total: int, settlement=None, health=None,
                 baseline: dict | None = None) -> dict:
        """The artifact PRD §10 requires at the end of every run.

        Every field it names is here, and nothing wall-clock is: D1 requires two
        identical runs to produce this byte-identical, so timing lives on
        `Runner.median_tick_ms` instead. Every dict is sorted so serialisation
        order cannot drift either.
        """
        summary = {
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
            "breaches_by_kind": dict(sorted(self.breaches_by_kind.items())),
            "breaches_total": sum(self.breaches_by_kind.values()),
            "reshapes_applied": self.reshapes_applied,
            "fallback_curtailments": self.fallbacks,
            "battery_charged_kwh": round(self.battery_charged_kwh, 6),
            "battery_discharged_kwh": round(self.battery_discharged_kwh, 6),
            "delivery_shortfall_kwh": round(self.delivery_shortfall_kwh, 6),
            "mean_clearing_price_inr": round(
                sum(self.prices) / len(self.prices), 4) if self.prices else None,
            "seed": config.seed,
            # A perfect-foresight feed is acceptable for testing only, and the
            # run summary must say so (PRD §3.2).
            "perfect_foresight": config.forecast_noise_frac == 0.0,
        }

        if settlement is not None:
            delivered = sum(l.quantity_kwh for l in settlement.ledger
                            if l.role == "buyer")
            summary["delivered_kwh"] = round(delivered, 6)
            summary["transmission_loss_kwh"] = round(
                sum(l.loss_kwh for l in settlement.ledger), 6)
            summary["discom_charge_revenue_inr"] = round(
                settlement.charges_collected, 6)
            summary["bill_lines"] = len(settlement.ledger)

        if health is not None:
            life = getattr(health, "_cumulative_life_hours", {})
            summary["loss_of_life_hours"] = {
                k: round(v, 9) for k, v in sorted(life.items())}
            summary["loss_of_life_hours_total"] = round(sum(life.values()), 9)

        if baseline is not None:
            summary["baseline"] = baseline

        return summary
