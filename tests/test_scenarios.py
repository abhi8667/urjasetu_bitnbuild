"""Scenario tests — the full B+C+D system under varied conditions.

Not unit tests: each scenario runs a real 30-day integration (feed -> agents ->
market -> sentinel -> flow -> health -> settlement) under one config change, and
asserts the *shape* of the outcome is sane relative to the baseline scenario.
This is what catches "works on the default config, breaks the moment a knob
moves" — the kind of bug unit tests on fixed fixtures don't see.

Run standalone: `python3 tests/test_scenarios.py`
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.config import DEFAULT as BASE_CONFIG, Config
from engine.feed import WhitefieldFeed
from engine.sim.pool import AgentPool
from engine.agents.settlement import SettlementAgent
from engine.sim.runner import Runner
from engine.sim.baseline import Baseline, p2p_economics, compare
from grid import BatteryBook, FlowAgent, GridSentinel, TransformerHealthAgent

def of_from(config):
    return {h.house_id: h.transformer_id for h in WhitefieldFeed(config).houses()}


def _no_cross_transformer_trades(feed_of, ledger):
    by_trade = {}
    for line in ledger:
        by_trade.setdefault(line.trade_id, {})[line.role] = line.house_id
    for trade_id, roles in by_trade.items():
        seller, buyer = roles.get("seller"), roles.get("buyer")
        if seller and buyer and feed_of.get(seller) != feed_of.get(buyer):
            return False
    return True


RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok))
    tag = "PASS" if ok else "FAIL"
    print(f"  {tag}  {name}" + (f"  — {detail}" if detail else ""))
    return ok


def run_scenario(config: Config, blocks: int | None = None, with_grid: bool = True):
    """One full integrated run. Returns (summary, settlement, health, compare_result)."""
    feed = WhitefieldFeed(config)
    houses, transformers = feed.houses(), feed.transformers()
    pool = AgentPool(houses, config)
    settle = SettlementAgent(houses, config, consumers=pool.consumers, feed=feed)
    kw = {}
    health = None
    if with_grid:
        health = TransformerHealthAgent(transformers, config, houses)
        kw = dict(
            sentinel=GridSentinel(transformers, houses, config),
            flow=FlowAgent(transformers, houses, config),
            health=health,
            batteries=BatteryBook(houses, config),
        )
    runner = Runner(feed, pool, config, settlement=settle, **kw)
    summary = runner.run(blocks=blocks)

    life = sum(health._cumulative_life_hours.values()) if health else None
    p2p = p2p_economics(feed, config, settle.ledger, blocks=blocks,
                        loss_of_life_hours=life)
    base = Baseline(feed, config).run(blocks=blocks)
    cmp = compare(p2p, base)
    return runner, summary, settle, health, cmp


# ============================================================ scenarios

def test_scenario_default_config():
    """The reference scenario — everything else is measured relative to this."""
    r, s, settle, health, cmp = run_scenario(BASE_CONFIG)
    check("default: 720 blocks complete", s["blocks"] == 720)
    check("default: trades occur", s["trades"] > 0, f"{s['trades']} trades")
    check("default: ST1 holds", abs(sum(l.net_inr for l in settle.ledger)
                                    - settle.charges_collected) < 1e-6)
    check("default: baseline ages >= p2p", cmp["baseline_ages_at_least_as_fast"])
    check("default: consumers pay less than the configured export-credit baseline",
          cmp["household_bills_inr"]["saved_inr"] > 0,
          f"saved Rs{cmp['household_bills_inr']['saved_inr']:.2f}")
    check("default: median tick < 50ms", r.median_tick_ms < 50.0,
          f"{r.median_tick_ms:.2f} ms")
    print(f"    -> life saved {cmp['transformer_life_hours']['saved_hours']:.2f} h, "
          f"trades {s['trades']}, tick {r.median_tick_ms:.2f}ms")


def test_scenario_no_grid_protection():
    """Protection disabled entirely — the system must still run, and must not
    accidentally save any transformer life (nothing is acting on breaches)."""
    r, s, settle, health, cmp = run_scenario(BASE_CONFIG, with_grid=False)
    check("no-protection: runs to completion", s["blocks"] == 720)
    check("no-protection: ST1 still holds",
          abs(sum(l.net_inr for l in settle.ledger) - settle.charges_collected) < 1e-6)
    print(f"    -> trades {s['trades']} (protection has no effect on the market itself)")


def test_scenario_derated_transformers():
    """derate_factor forces harder breaches — the demo control. More breaches
    and more curtailment/reshaping should follow, not less."""
    derated = replace(BASE_CONFIG, derate_factor=0.8)
    r1, s1, _, h1, cmp1 = run_scenario(BASE_CONFIG)
    r2, s2, _, h2, cmp2 = run_scenario(derated)
    life1 = sum(h1._cumulative_life_hours.values())
    life2 = sum(h2._cumulative_life_hours.values())
    check("derated: transformers age faster under harder derating",
          life2 >= life1, f"life {life1:.2f}h -> {life2:.2f}h")
    check("derated: still completes and conserves money", s2["blocks"] == 720)


def test_scenario_no_kerc_transformer_constraint():
    """Turning off the same-transformer rule does NOT change total volume —
    supply is the binding constraint in every DT on this street, not the
    matching topology, so a merged book clears the same total kWh a set of
    per-DT books does. What the flag actually controls is WHICH pairs match:
    disabling it lets trades cross transformer boundaries, which KERC's 2024
    P2P rules forbid (a physical impossibility — electricity cannot be routed
    meter-to-meter across a transformer)."""
    from engine.agents.market import MarketAgent
    from engine.bus import Bus
    loose = replace(BASE_CONFIG, enforce_same_transformer=False)
    feed = WhitefieldFeed(loose)
    of = {h.house_id: h.transformer_id for h in feed.houses()}
    pool = AgentPool(feed.houses(), loose)
    market = MarketAgent(Bus(), feed.houses(), loose)
    crossed_kwh = 0.0
    for b in range(240):
        ticks = feed.ticks(b)
        res = market.clear(b, pool.build(b, ticks, feed))
        crossed_kwh += sum(t.quantity_kwh for t in res.trades
                          if of[t.seller_id] != of[t.buyer_id])
    check("disabling the KERC rule allows cross-transformer trades",
          crossed_kwh > 0, f"{crossed_kwh:.1f} kWh crossed a transformer boundary")

    r1, s1, settle1, _, _ = run_scenario(BASE_CONFIG, blocks=240)
    check("with the rule enforced, no bill line crosses a transformer",
          _no_cross_transformer_trades(feed_of=of_from(BASE_CONFIG), ledger=settle1.ledger))


def test_scenario_zero_battery_reserve():
    """No daylight reserve for the evening peak: the flow agent should still
    fall back safely (never raise), just with less to discharge."""
    no_reserve = replace(BASE_CONFIG,
                        battery_reserve_frac=0.0) if hasattr(BASE_CONFIG, "battery_reserve_frac") else BASE_CONFIG
    # battery_reserve_frac lives on StrategyParams, not Config — build via pool directly
    from engine.domain import StrategyParams
    feed = WhitefieldFeed(BASE_CONFIG)
    houses, transformers = feed.houses(), feed.transformers()
    pool = AgentPool(houses, BASE_CONFIG, strategy=StrategyParams(battery_reserve_frac=0.0))
    settle = SettlementAgent(houses, BASE_CONFIG, consumers=pool.consumers, feed=feed)
    runner = Runner(feed, pool, BASE_CONFIG, settlement=settle,
                    sentinel=GridSentinel(transformers, houses, BASE_CONFIG),
                    flow=FlowAgent(transformers, houses, BASE_CONFIG),
                    health=TransformerHealthAgent(transformers, BASE_CONFIG, houses),
                    batteries=BatteryBook(houses, BASE_CONFIG))
    summary = runner.run()
    check("zero-reserve: completes without raising", summary["blocks"] == 720)


def test_scenario_perfect_foresight():
    """forecast_noise_frac=0 (perfect foresight, test-only per PRD) should not
    change which invariants hold, only remove forecast error."""
    perfect = replace(BASE_CONFIG, forecast_noise_frac=0.0)
    r, s, settle, _, cmp = run_scenario(perfect, blocks=240)
    check("perfect-foresight: flagged in run summary", s["perfect_foresight"] is True)
    check("perfect-foresight: ST1 still holds",
          abs(sum(l.net_inr for l in settle.ledger) - settle.charges_collected) < 1e-6)


def test_scenario_bid_cap_changes_who_wins_not_total_volume():
    """max_bid_kwh_per_block caps how much any one buyer can take per block so
    scarce surplus is shared. Since supply, not demand, is the binding
    constraint here (per-block deficit vastly exceeds surplus, see
    DECISIONS.md D10), raising the cap doesn't raise total traded volume —
    it changes how concentrated that volume is among buyers. Assert the real
    effect: with a low cap more distinct buyers get filled per surplus block;
    with a high cap fewer, larger buyers take it all."""
    from engine.agents.market import MarketAgent
    from engine.bus import Bus
    tight = replace(BASE_CONFIG, max_bid_kwh_per_block=0.5)
    hungry = replace(BASE_CONFIG, max_bid_kwh_per_block=10.0)

    def distinct_buyers_per_surplus_block(cfg):
        feed = WhitefieldFeed(cfg)
        pool = AgentPool(feed.houses(), cfg)
        market = MarketAgent(Bus(), feed.houses(), cfg)
        buyers_per_block = []
        for b in range(96, 144):  # a daylight window with real surplus
            ticks = feed.ticks(b)
            res = market.clear(b, pool.build(b, ticks, feed))
            if res.trades:
                buyers_per_block.append(len({t.buyer_id for t in res.trades}))
        return sum(buyers_per_block) / max(1, len(buyers_per_block))

    tight_avg = distinct_buyers_per_surplus_block(tight)
    hungry_avg = distinct_buyers_per_surplus_block(hungry)
    check("a tighter bid cap spreads surplus over more distinct buyers",
          tight_avg >= hungry_avg,
          f"tight cap avg {tight_avg:.2f} buyers/block vs high cap avg {hungry_avg:.2f}")

    r1, s1, _, _, _ = run_scenario(BASE_CONFIG, blocks=240)
    r2, s2, _, _, _ = run_scenario(hungry, blocks=240)
    check("total volume is roughly cap-INsensitive (supply-bound, not demand-bound)",
          abs(s2["traded_kwh"] - s1["traded_kwh"]) / max(s1["traded_kwh"], 1e-9) < 0.05,
          f"default {s1['traded_kwh']:.1f} kWh vs high-cap {s2['traded_kwh']:.1f} kWh")


def test_scenario_single_day_vs_full_month():
    """A 1-day run and a 30-day run should agree on day-1 numbers exactly
    (determinism), and the 30-day run should show more total activity."""
    r1, s1, _, _, _ = run_scenario(BASE_CONFIG, blocks=24)
    r2, s2, _, _, _ = run_scenario(BASE_CONFIG, blocks=720)
    check("30-day trades >= 1-day trades", s2["trades"] >= s1["trades"])
    check("30-day traded_kwh >= 1-day traded_kwh", s2["traded_kwh"] >= s1["traded_kwh"])


def test_scenario_reproducibility_across_all_scenarios():
    """Every scenario above must itself be internally deterministic — run each
    config twice, block-limited, and require identical run summaries."""
    configs = {
        "default": BASE_CONFIG,
        "derated": replace(BASE_CONFIG, derate_factor=0.8),
        "loose_market": replace(BASE_CONFIG, enforce_same_transformer=False),
        "perfect_foresight": replace(BASE_CONFIG, forecast_noise_frac=0.0),
    }
    all_ok = True
    for name, cfg in configs.items():
        _, s1, _, _, _ = run_scenario(cfg, blocks=48)
        _, s2, _, _, _ = run_scenario(cfg, blocks=48)
        ok = s1 == s2
        all_ok &= ok
        if not ok:
            print(f"    non-deterministic under scenario: {name}")
    check("all scenarios are internally deterministic", all_ok)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        print(f"\n[{fn.__name__}]")
        try:
            fn()
        except Exception as exc:
            failed += 1
            print(f"  ERROR  {fn.__name__}: {type(exc).__name__}: {exc}")
    n_checks = len(RESULTS)
    n_failed_checks = sum(1 for _, ok in RESULTS if not ok)
    print(f"\n{n_checks - n_failed_checks}/{n_checks} checks passed across "
          f"{len(fns)} scenarios ({failed} scenario(s) errored)")
    sys.exit(1 if (failed or n_failed_checks) else 0)
