"""Integration check 6 — breach resolution under an injected derate.

PRD §10.6: "Inject a transformer derate to 60% at a known block. The engine
resolves within the 2-pass bound in 100% of injected cases; fallback fires at
most once per injection."

Taken alone that check is close to unfalsifiable: fallback_curtail applies a
uniform retention fraction computed to land exactly on the limit, so it ALWAYS
resolves. A green tick here says nothing about whether the reshape — the LP,
the batteries, the actual mechanism the project is about — did any work at all.
It passed for months while the LP contributed literally nothing.

So this file checks the letter of §10.6 AND the thing it was meant to protect:
how the resolution was reached, and whether every LP refusal was physically
justified rather than a formulation bug.

Run standalone: `python3 tests/test_breach_resolution.py`
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.agents.market import MarketAgent
from engine.agents.settlement import SettlementAgent
from engine.bus import Bus
from engine.config import DEFAULT as BASE_CONFIG
from engine.feed import WhitefieldFeed
from engine.sim.pool import AgentPool
from engine.sim.runner import MAX_CLEARING_PASSES, Runner
from grid import BatteryBook, FlowAgent, GridSentinel, TransformerHealthAgent

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    return ok


def _run(derate, blocks=None):
    config = replace(BASE_CONFIG, derate_factor=derate)
    counts = Counter()

    class CountingBus(Bus):
        def publish(self, topic, block, agent_id, payload=None):
            counts[topic] += 1
            if topic == "reshape_applied" and payload:
                counts["discharge_kwh"] += payload.get("battery_discharge_kwh", 0.0)
            return super().publish(topic, block, agent_id, payload)

    feed = WhitefieldFeed(config)
    houses, transformers = feed.houses(), feed.transformers()
    pool = AgentPool(houses, config)
    settle = SettlementAgent(houses, config, consumers=pool.consumers, feed=feed)
    runner = Runner(feed, pool, config, bus=CountingBus(),
                    sentinel=GridSentinel(transformers, houses, config),
                    flow=FlowAgent(transformers, houses, config),
                    health=TransformerHealthAgent(transformers, config, houses),
                    settlement=settle, batteries=BatteryBook(houses, config))
    runner.run(blocks=blocks)
    return config, feed, runner, counts


# ------------------------------------------------- the letter of §10.6

def test_every_breach_resolves_within_the_two_pass_bound():
    """The run completing at all is the proof: the runner raises InvariantError
    if any block exceeds MAX_CLEARING_PASSES, so 720 clean blocks means the
    bound held everywhere."""
    _, _, runner, counts = _run(0.6)
    check("§10.6  every block stays inside the 2-pass bound",
          MAX_CLEARING_PASSES == 2,
          f"{counts['breach_detected']} breaches, run completed without raising")


def test_fallback_fires_at_most_once_per_breach():
    _, _, _, counts = _run(0.6)
    check("§10.6  fallback fires at most once per loading breach",
          counts["fallback_curtailed"] <= counts["reshape_proposed"],
          f"{counts['fallback_curtailed']} fallbacks for "
          f"{counts['reshape_proposed']} loading breaches")


# ------------------------------------- what §10.6 was meant to protect

def test_the_reshape_actually_contributes_not_just_the_fallback():
    """The failure this catches: a 100%-resolved run where the LP did nothing
    and uniform curtailment carried every case."""
    _, _, _, counts = _run(0.6)
    check("the LP contributes real reshapes, not just fallback",
          counts["reshape_applied"] > 0,
          f"{counts['reshape_applied']} reshapes applied, "
          f"{counts['discharge_kwh']:.1f} kWh discharged by the flow agent")


def test_resolution_split_is_reported_at_every_derate():
    """Both counts, at three derates, so a regression that quietly shifts work
    from the LP to the fallback is visible instead of hiding behind a tick."""
    print()
    ok = True
    for derate in (1.0, 0.8, 0.6):
        _, _, _, counts = _run(derate)
        reshaped, fallback = counts["reshape_applied"], counts["fallback_curtailed"]
        print(f"       derate {derate}: {counts['reshape_proposed']:3} loading breaches -> "
              f"{reshaped:3} reshaped, {fallback:3} fallback, "
              f"{counts['discharge_kwh']:7.1f} kWh discharged")
        ok &= reshaped > 0
    check("the LP contributes at every derate level", ok)


def test_every_lp_refusal_is_physically_justified():
    """An infeasible LP must mean the batteries genuinely could not cover the
    overload — not that the formulation is wrong.

    Compares, for each loading breach, the kW that must leave the transformer
    against the kWh its batteries could actually discharge. A refusal with
    ample battery headroom would be a formulation bug; a refusal with the
    overload far beyond the batteries is the honest answer.
    """
    config, feed, _, _ = _run(0.6, blocks=240)
    houses, transformers = feed.houses(), feed.transformers()
    of = {h.house_id: h.transformer_id for h in houses}
    rating = {t.transformer_id: t.rating_kva for t in transformers}
    sentinel = GridSentinel(transformers, houses, config)
    flow = FlowAgent(transformers, houses, config)
    batteries = BatteryBook(houses, config)
    pool = AgentPool(houses, config)
    market = MarketAgent(Bus(), houses, config)
    for house in houses:
        if house.has_battery:
            batteries.store_own_energy(house.house_id, house.battery_kwh * 0.8)

    def live_capacity(transformer_id):
        """Discharge available on this DT RIGHT NOW. Must be read per breach,
        not snapshotted: reshape() drains the batteries as it goes, so a
        capacity figure taken at block 0 makes every later refusal look
        unjustified when the batteries were simply empty."""
        total = 0.0
        for house in houses:
            if house.has_battery and of[house.house_id] == transformer_id:
                total += min(house.battery_max_kw * config.block_hours,
                             batteries.total_stored_kwh(house.house_id))
        return total

    unjustified = []
    refusals = 0
    for block in range(240):
        ticks = feed.ticks(block)
        result = market.clear(block, pool.build(block, ticks, feed))
        breach = sentinel.check(result.trades, ticks)
        if breach is None or breach.kind != "loading":
            continue
        tid = breach.transformer_id
        load_kva = sum(abs(t.load_kwh - t.gen_kwh) for t in ticks
                       if of[t.house_id] == tid) / config.block_hours / 0.95
        must_shed = load_kva - rating[tid] * config.loading_limit
        available = live_capacity(tid)
        plan = flow.reshape(result.trades, ticks, breach, batteries)
        if not plan.feasible:
            refusals += 1
            # generous margin: only flag refusals where the batteries had
            # clearly more than enough to cover the whole overload
            if available > must_shed * 1.5:
                unjustified.append((block, tid, round(must_shed, 2),
                                    round(available, 2)))
    check("every LP refusal is justified by a real physical shortfall",
          not unjustified,
          f"{refusals} refusals audited"
          + (f", {len(unjustified)} unexplained: {unjustified[:3]}" if unjustified
             else ", all had an overload beyond their batteries"))


def test_lp_uses_the_same_limits_the_sentinel_enforces():
    """Regression guard. The LP hardcoded BLOCK_HOURS=0.25 (15-minute blocks)
    and VOLTAGE_BAND=0.05, and its _cfg_get() override read attributes off the
    engine.config MODULE that never existed, so every lookup silently fell
    through to a stale default. The battery bound was therefore a quarter of the
    real one, and the LP solved a strictly harder problem than the sentinel
    enforces while reporting more 'successes' that moved half the energy.
    """
    config = replace(BASE_CONFIG, derate_factor=0.6)
    feed = WhitefieldFeed(config)
    flow = FlowAgent(feed.transformers(), feed.houses(), config)
    from engine.domain import Breach
    limits = flow._build_limits(Breach("DT-3", "loading", 1.2, {}), feed.ticks(19))
    ok = abs(limits.block_hours - config.block_hours) < 1e-9
    ok &= abs(limits.voltage_band - config.voltage_band) < 1e-9
    check("LP limits match engine config (block_hours, voltage_band)", ok,
          f"block_hours {limits.block_hours} vs {config.block_hours}, "
          f"voltage_band {limits.voltage_band} vs {config.voltage_band}")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        try:
            fn()
        except Exception as exc:
            RESULTS.append((fn.__name__, False))
            print(f"  ERROR  {fn.__name__}: {type(exc).__name__}: {exc}")
    failed = sum(1 for _, ok in RESULTS if not ok)
    print(f"\n{len(RESULTS) - failed}/{len(RESULTS)} checks passed")
    sys.exit(1 if failed else 0)
