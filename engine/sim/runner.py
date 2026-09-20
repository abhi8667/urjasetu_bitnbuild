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
from dataclasses import asdict
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
    # Matches the call site. The runner passes `block` (so the agent can publish)
    # and `claims` (so storage fees get billed); the protocol declared a
    # two-argument method and quietly disagreed with the code below.
    def settle(self, trades: list[Trade], ageing=None, block: int | None = None,
               claims=None) -> Any: ...


class Runner:
    def __init__(self, feed: WhitefieldFeed, orders: OrderSource,
                 config: Config = DEFAULT, bus: Bus | None = None,
                 sentinel: Sentinel | None = None, flow: Flow | None = None,
                 health: Health | None = None, settlement: Settlement | None = None,
                 batteries: Any = None, persist=None, start_block: int = 0,
                 include_baseline: bool = True, validate_feed: bool = True,
                 on_block: Any = None, risk_agent: Any = None):
        self.feed = feed
        self.orders = orders
        self.risk_agent = risk_agent
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
        # Called once per block with a read-only snapshot of what that block did
        # (see `_BlockView` at the foot of this file). This is how the server
        # layer records a run for the UI without reaching into engine state —
        # the same one-way rule `engine/bus.py` states for the ring buffer.
        # Optional; None keeps the loop exactly as it was.
        self.on_block = on_block
        self.feed_validation: dict | None = None
        self.summary: dict | None = None
        self.tick_durations_ms: list[float] = []
        #: block -> transformer_id the predictor flagged for that block. Scored
        #: against what actually breached, so the prediction is falsifiable
        #: rather than decorative.
        self._predictions: dict[int, str] = {}
        self.prediction_hits = 0
        self.prediction_misses = 0

    def _charge_batteries(self, block: int) -> tuple[dict[str, float], dict[str, float]]:
        """Store what the prosumers held back. Returns (drawn, stored).

        The two are NOT the same number, and conflating them was inventing
        energy. `store_own_energy` applies round-trip efficiency: hand it 1.0 kWh
        and 0.90 kWh lands in the battery. The runner used to ignore the return
        value and credit the prosumer — and the run summary — with the full
        1.0 kWh, so the agents' belief about their own state of charge drifted
        above what the BatteryBook actually held, by exactly the conversion loss,
        and PR1 never caught it because PR1 checks the drifting number.

          drawn   what leaves the premises' surplus. This is what the METER sees
                  as additional load, so it is what the ticks must be adjusted by.
          stored  what is in the battery afterwards, and therefore what the
                  prosumer's SoC and the run summary must record.

        drawn - stored is the conversion loss, reported separately so FL4 has
        somewhere to put it instead of it vanishing.
        """
        reserves = getattr(self.orders, "battery_reserves", None)
        if not reserves or self.batteries is None:
            return {}, {}
        drawn: dict[str, float] = {}
        stored: dict[str, float] = {}
        for house_id, kwh in sorted(reserves.items()):
            room = self.batteries.available_absorption_kwh(house_id)
            take = min(kwh, room)
            if take <= 1e-9:
                continue
            landed = self.batteries.store_own_energy(house_id, take)
            if landed <= 1e-9:
                continue
            drawn[house_id] = round(take, 9)
            stored[house_id] = round(landed, 9)
            prosumer = getattr(self.orders, "prosumers", {}).get(house_id)
            if prosumer is not None:
                # The kWh that actually landed, not the kWh handed over.
                prosumer.on_settled(block, [], [], battery_delta_kwh=landed)
        return drawn, stored

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
        # Scored, not asserted. A predictor that is never checked against what
        # happened is decoration; one that reports its own hit rate can be
        # argued with.
        attempted = self.prediction_hits + self.prediction_misses
        self.summary["breach_prediction"] = {
            "predicted": attempted,
            "hits": self.prediction_hits,
            "misses": self.prediction_misses,
            "precision_pct": round(100 * self.prediction_hits / attempted, 4)
            if attempted else None,
        }
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

    def _maybe_set_daily_strategy(self, block: int) -> None:
        if block % self.config.blocks_per_day != 0:
            return
        setter = getattr(self.orders, "set_strategy", None)
        if setter is None:
            return
        from engine.algo import llm
        if not llm.LLM_ENABLED:
            return
        previous = getattr(self.orders, "strategy", None)
        ticks = self.feed.ticks(block)
        weather = {
            "ambient_c": round(ticks[0].ambient_c, 2) if ticks else None,
            "day": block // self.config.blocks_per_day,
            "street_generation_kwh": round(sum(t.gen_kwh for t in ticks), 3),
            "street_load_kwh": round(sum(t.load_kwh for t in ticks), 3),
        }
        history = [round(p, 4) for p in getattr(self.orders, "price_history", [])[-48:]]
        proposed = llm.daily_strategy(weather, history, previous)
        if proposed == previous:
            return
        setter(proposed)
        self.bus.publish("strategy_updated", block, "llm", {
            "day": weather["day"],
            "discount": proposed.discount,
            "margin": proposed.margin,
            "bid_aggression": proposed.bid_aggression,
            # LM1: never the LLM's to move, so it is reported to prove it did not.
            "battery_reserve_frac": proposed.battery_reserve_frac,
        })

    def _predict_next_block(self, block: int, trades: list[Trade]) -> None:
        """Look one block ahead and say so if the iron is about to be in trouble.

        The problem this system exists for asks agents to "predict grid
        failures", and until now nothing did: `GridSentinel.check` is reactive by
        construction — it evaluates the block that is already happening, after
        the market has cleared it — and `TransformerHealthAgent.risk_rank`, the
        one forward-looking thing in the codebase, had no caller at all.

        So: take the feed's forecast for block+1 (agent belief, noise included,
        NOT truth — this is a prediction and it is allowed to be wrong), run the
        same sentinel over it, and publish. It is advisory. Nothing downstream
        acts on it, and deliberately so: acting on a forecast would let a bad
        forecast curtail real trades. It gives the UI a warning ahead of the
        event and gives the record something to be scored against later.
        """
        if self.sentinel is None:
            return
        next_block = block + 1
        if next_block >= self.feed.total_blocks():
            return
        try:
            forecast_ticks = [self.feed.forecast(h.house_id, next_block, 1)[0]
                              for h in self.feed.houses()]
        except (IndexError, KeyError):
            return
        predicted = self.sentinel.check(trades, forecast_ticks)
        if predicted is None:
            return
        ranked = self.health.risk_rank() if self.health is not None else []
        self.bus.publish("breach_predicted", block, "sentinel", {
            "for_block": next_block,
            "transformer_id": predicted.transformer_id,
            "kind": predicted.kind,
            "severity": round(predicted.severity, 4),
            # risk_rank() was dead code — defined, tested, never called.
            "risk_rank": [[tid, round(score, 9)] for tid, score in ranked],
        })
        self._predictions[next_block] = predicted.transformer_id

    def _tick(self, block: int, summary: "_Accumulator") -> None:
        self.bus.publish("block_opened", block, AGENT_ID, {
            "hour": block % self.config.blocks_per_day,
            "day": block // self.config.blocks_per_day,
        })

        # Once per simulated day, before anything trades: ask the LLM layer for
        # a bidding posture. `algo.llm.daily_strategy` was written, tested and
        # never called — the one place a language model belongs in this system
        # had no call site at all. It is advisory and bounded: it may move four
        # clamped numbers on the agents' strategy and nothing else, and with no
        # key it returns the previous parameters unchanged, which is how the
        # engine runs identically with the LLM off (PRD integration check 10).
        raw_ticks = self.feed.ticks(block)
        risk = self.risk_agent.predict(block, raw_ticks) if self.risk_agent else []
        for prediction in risk:
            self.bus.publish("grid_risk_predicted", block, "grid_risk", asdict(prediction))
        strategy_agent = getattr(self.orders, "strategy_agent", None)
        if strategy_agent is not None:
            updated = self.orders.prepare_strategy(block, raw_ticks, risk)
            state = ("disabled" if not strategy_agent.config.llm_enabled else
                     "fallback" if strategy_agent.last_model is None else
                     "decided" if updated else "reused")
            strategy = self.orders.strategy
            if strategy_agent.last_reasoning:
                # The provider returns this separately from the final JSON. It
                # is deliberately published first so both activity UIs show
                # the rationale before the decision it produced.
                self.bus.publish("ai_strategy_thinking", block, "ai_trading", {
                    "model": strategy_agent.last_model,
                    "reasoning": strategy_agent.last_reasoning,
                })
            self.bus.publish("ai_strategy_updated" if updated else "ai_strategy_status",
                             block, "ai_trading", {
                "state": state, "model": strategy_agent.last_model,
                "discount": strategy.discount, "margin": strategy.margin,
                "risk_inputs": len(risk),
                "error": strategy_agent.last_error,
            })
        else:
            self._maybe_set_daily_strategy(block)
        orders = _build_orders(self.orders, block, raw_ticks, self.feed)
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
        drawn_now, stored_now = self._charge_batteries(block)

        # A battery charging is additional load at that premises, and the meters
        # would read it. This adjustment did not exist: energy entered batteries
        # while the sentinel and the health agent went on measuring a street
        # where it never happened, and the seller's surplus was still available
        # to sell even though it had just been put in a battery. `drawn_now`,
        # not `stored_now` — what leaves the premises is what the meter sees.
        ticks = _apply_charge(raw_ticks, drawn_now)

        result = self.market.clear(block, orders)
        settled_ticks = ticks
        plan = None
        passes = 1
        breach = self.sentinel.check(result.trades, ticks) if self.sentinel else None

        # Forward-looking check, for the UI and for the record. The sentinel is
        # otherwise purely reactive — it reports a breach in the block that is
        # already happening — and "predict grid failures" needs a step that runs
        # before the fact. This uses the feed's own forecast for block+1 and the
        # trades just cleared, so it costs one extra sentinel evaluation.
        self._predict_next_block(block, result.trades)

        predicted_tid = self._predictions.pop(block, None)
        if predicted_tid is not None:
            if breach is not None and breach.transformer_id == predicted_tid:
                self.prediction_hits += 1
            else:
                self.prediction_misses += 1

        if breach is not None:
            summary.record_breach(breach.kind)
            self.bus.publish("breach_detected", block, "sentinel", {
                "predicted": predicted_tid == breach.transformer_id,
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
                    # `apply_reshape`, not `clear`. Re-clearing the constrained
                    # orders through the auction threw away the LP's pairing —
                    # every constrained pair carries the same price, and a
                    # uniform-price auction sorts by price and walks, so seller
                    # A's retained energy was rematched to whichever bid sorted
                    # first. The LP's per-trade allocation IS the thing that
                    # satisfies the loading and voltage rows it solved, so
                    # discarding it meant the re-check below measured a street
                    # the LP had never made feasible. MK1/MK2 still run.
                    result = self.market.apply_reshape(
                        block, plan.constrained_orders, plan.constrained_trades)
                    passes += 1
                    summary.reshapes_applied += 1
                    self.bus.publish("reshape_applied", block, "flow", {
                        "trades": len(result.trades),
                        "battery_charges": len(plan.battery_charges),
                        "battery_discharge_kwh": round(
                            sum(plan.battery_discharges.values()), 6),
                        "claims_opened": len(plan.new_claims),
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
        # `stored_now`, not `drawn_now`: the summary must agree with what the
        # BatteryBook actually holds. Reporting the pre-efficiency figure here
        # while reporting post-efficiency discharges made the run summary claim
        # a net stored energy the batteries never contained.
        summary.battery_charged_kwh += sum(stored_now.values())
        summary.battery_discharged_kwh += sum(discharged_now.values())
        summary.battery_conversion_loss_kwh += (
            sum(drawn_now.values()) - sum(stored_now.values()))
        if stored_now or discharged_now:
            self.bus.publish("battery_moved", block, "runner", {
                "drawn_kwh": {k: round(v, 9) for k, v in sorted(drawn_now.items())},
                "charged_kwh": {k: round(v, 9) for k, v in sorted(stored_now.items())},
                "discharged_kwh": {k: round(v, 9) for k, v in sorted(discharged_now.items())},
                "conversion_loss_kwh": round(
                    sum(drawn_now.values()) - sum(stored_now.values()), 9),
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
        # `ticks`, not `settled_ticks`. settled_ticks already has the discharge
        # subtracted from load, which RAISES the computed surplus by exactly the
        # discharge — and then `discharged_now` was added on top, counting the
        # same kWh twice. `ticks` is charge-adjusted but pre-discharge, which is
        # the state the "surplus plus what you discharged" sum is written for.
        result.trades[:], shortfalls = _reconcile_delivery(
            result.trades, ticks, discharged_now, self.config)
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

        # Claims opened by this block's reshape go to settlement so the storage
        # fee is actually paid. `plan.new_claims` used to be built, returned, and
        # dropped on the floor here — the whole owner/custodian economic layer
        # produced objects nobody ever settled.
        new_claims = list(getattr(plan, "new_claims", []) or []) if plan else []
        summary.claims_opened += len(new_claims)
        bills = (self.settlement.settle(result.trades, ageing, block,
                                        claims=new_claims)
                 if self.settlement else None)

        # Agents learn here and nowhere else (PR3). Fanning out after settlement
        # means their history includes this block's outcome, never this block's
        # own decision.
        if hasattr(self.orders, "on_settled"):
            # raw_ticks: agents learn the street's underlying generation and
            # load, which is what they forecast. Feeding them the battery- and
            # reshape-adjusted ticks would teach every prosumer that its own
            # storage decisions were weather.
            self.orders.on_settled(block, raw_ticks, result.trades,
                                   result.clearing_price, bills or [])

        if self.persist is not None:
            # Accepts a Persistence instance or any callable with the same shape.
            writer = getattr(self.persist, "write_block", self.persist)
            writer(block, raw_ticks, orders, result.trades, bills, ageing)

        summary.record(block, raw_ticks, orders, result, passes)
        if self.on_block is not None:
            self.on_block(_BlockView(
                block=block, raw_ticks=raw_ticks, settled_ticks=settled_ticks,
                orders=orders, trades=list(result.trades),
                clearing_price=result.clearing_price, breach=breach,
                predicted_transformer=predicted_tid, ageing=ageing,
                bills=list(bills or []), passes=passes,
                battery_drawn=drawn_now, battery_stored=stored_now,
                battery_discharged=discharged_now, claims=new_claims,
                shortfalls=shortfalls))
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


def _apply_charge(ticks: list[MeterTick], drawn: dict[str, float]) -> list[MeterTick]:
    """Ticks as the meters would read them once batteries started charging.

    Charging is additional consumption at that premises. Without this the energy
    left the street's surplus and entered a battery while every downstream
    reader — the sentinel, the health agent, the delivery reconciliation — went
    on seeing the unmodified meter. The seller could then sell the very kWh it
    had just stored.
    """
    if not drawn:
        return ticks
    out = []
    for tick in ticks:
        delta = drawn.get(tick.house_id, 0.0)
        if delta == 0.0:
            out.append(tick)
            continue
        out.append(MeterTick(block=tick.block, house_id=tick.house_id,
                             load_kwh=round(tick.load_kwh + delta, 9),
                             gen_kwh=tick.gen_kwh, ambient_c=tick.ambient_c))
    return out


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
        self.battery_conversion_loss_kwh = 0.0
        self.delivery_shortfall_kwh = 0.0
        self.claims_opened = 0

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
            # Post-efficiency, so charged - discharged equals what the
            # BatteryBook actually holds. The conversion loss is reported beside
            # them rather than folded into either, so FL4 has all three terms.
            "battery_charged_kwh": round(self.battery_charged_kwh, 6),
            "battery_discharged_kwh": round(self.battery_discharged_kwh, 6),
            "battery_conversion_loss_kwh": round(self.battery_conversion_loss_kwh, 6),
            "delivery_shortfall_kwh": round(self.delivery_shortfall_kwh, 6),
            "storage_claims_opened": self.claims_opened,
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
            summary["platform_fee_inr"] = round(
                sum(l.platform_inr for l in settlement.ledger), 6)
            summary["gst_inr"] = round(
                sum(l.gst_inr for l in settlement.ledger), 6)
            summary["storage_fees_paid_inr"] = round(
                sum(l.storage_fee_inr for l in settlement.ledger
                    if l.role == "owner"), 6)
            # Nonzero means the charge stack is pressing against retail and the
            # ageing signal is being held back to keep CN2 true. Visible on
            # purpose: a cap that hides how often it fires is a cap you cannot
            # reason about.
            summary["ageing_adder_trimmed_inr"] = round(
                getattr(settlement, "adder_trimmed_inr", 0.0), 6)

        if health is not None:
            life = getattr(health, "_cumulative_life_hours", {})
            summary["loss_of_life_hours"] = {
                k: round(v, 9) for k, v in sorted(life.items())}
            summary["loss_of_life_hours_total"] = round(sum(life.values()), 9)

        if baseline is not None:
            summary["baseline"] = baseline

        return summary


# --------------------------------------------------------- block snapshot

from dataclasses import dataclass as _dataclass, field as _field  # noqa: E402


@_dataclass(frozen=True)
class _BlockView:
    """Everything one block did, handed to `Runner.on_block`.

    Frozen, and every collection is already a copy, so a recorder cannot reach
    back and change what the engine is doing. That is the same one-way rule the
    bus ring buffer states: the UI reads what the engine published, and nothing
    outside the engine writes engine state.

    Both tick lists are here on purpose. `raw_ticks` is what the meters would
    have read with no batteries and no reshaping — what the agents forecast
    against. `settled_ticks` is what actually happened once charging and
    discharging moved energy. A UI that draws `raw_ticks` shows a street where
    the protection agents did nothing.
    """
    block: int
    raw_ticks: list
    settled_ticks: list
    orders: list
    trades: list
    clearing_price: float | None
    breach: Any
    predicted_transformer: str | None
    ageing: Any
    bills: list
    passes: int
    battery_drawn: dict
    battery_stored: dict
    battery_discharged: dict
    claims: list = _field(default_factory=list)
    shortfalls: dict = _field(default_factory=dict)
