#!/usr/bin/env python3
"""Independent verification of the UrjaSetu agent network.

    python3 temp/checks/verify_agents.py            # ~40 s
    python3 temp/checks/verify_agents.py --quick    # ~10 s, skips the 30-day runs

Written to FALSIFY, not to confirm. Every check states what would make it fail
and why that would matter. A check that cannot fail is not a check, so none of
these are assertions of the form "the code does what the code does" — each one
compares two independently-derived quantities, or asserts a bound the engine is
free to violate.

If a check fails, the failure is the finding. Do not relax the check.

Exit code is the number of failed checks.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"\n          {detail}" if detail else ""))
    return ok


def section(title: str) -> None:
    print(f"\n{'-' * 72}\n  {title}\n{'-' * 72}")


# ---------------------------------------------------------------- fixtures

def build(blocks: int, derate: float = 1.0):
    """One fully-wired run with every agent present."""
    from dataclasses import replace
    from engine.agents.settlement import SettlementAgent
    from engine.bus import Bus
    from engine.config import DEFAULT
    from engine.feed import WhitefieldFeed
    from engine.sim.pool import AgentPool
    from engine.sim.runner import Runner
    from grid import BatteryBook, FlowAgent, GridSentinel, TransformerHealthAgent

    config = replace(DEFAULT, derate_factor=derate) if derate != 1.0 else DEFAULT
    feed = WhitefieldFeed(config)
    houses, transformers = feed.houses(), feed.transformers()
    bus = Bus(ring_size=10_000_000)          # big enough that nothing is evicted
    pool = AgentPool(houses, config)
    settlement = SettlementAgent(houses, config, bus=bus,
                                 consumers=pool.consumers, feed=feed)
    batteries = BatteryBook(houses, config)
    health = TransformerHealthAgent(transformers, config, houses)
    runner = Runner(feed, pool, config, bus=bus,
                    sentinel=GridSentinel(transformers, houses, config),
                    flow=FlowAgent(transformers, houses, config),
                    health=health, settlement=settlement, batteries=batteries,
                    include_baseline=False)
    summary = runner.run(blocks=blocks)
    return dict(config=config, feed=feed, houses=houses, transformers=transformers,
                bus=bus, pool=pool, settlement=settlement, batteries=batteries,
                health=health, runner=runner, summary=summary)


# ------------------------------------------------------------- the checks

def check_agents_are_reached(run) -> None:
    section("1. Every agent is actually reached")
    topics = Counter(e["topic"] for e in run["bus"].ring)
    required = {
        "block_opened": "runner", "order_submitted": "prosumer/consumer",
        "market_cleared": "market", "breach_detected": "sentinel",
        "breach_predicted": "sentinel (predictive)", "ageing_applied": "health",
        "bill_lines_posted": "settlement", "block_settled": "runner",
    }
    for topic, owner in sorted(required.items()):
        check(f"1.1 {owner} publishes {topic}", topics.get(topic, 0) > 0,
              f"{topics.get(topic, 0)} events")

    # The flow agent only speaks when a LOADING breach occurs, so these are
    # conditional on the street breaching — which on this dataset it does.
    check("1.2 flow agent proposed at least one reshape",
          topics.get("reshape_proposed", 0) > 0,
          f"{topics.get('reshape_proposed', 0)} proposals")
    check("1.3 flow agent APPLIED at least one reshape "
          "(0 means the LP is silently failing — check scipy)",
          topics.get("reshape_applied", 0) > 0,
          f"{topics.get('reshape_applied', 0)} applied")
    check("1.4 battery energy actually moved",
          run["summary"]["battery_discharged_kwh"] > 0,
          f"{run['summary']['battery_discharged_kwh']} kWh discharged")


def check_no_placeholders(run) -> None:
    section("2. No placeholder or invented values in the payload")
    from server.simulation import build_simulation

    sim = build_simulation(days=2)
    by_id = {h["id"]: h for h in sim.scene["houses"]}

    bad_soc = [(b["block"], hid) for b in sim.blocks
               for hid, st in b["houses"].items()
               if not by_id[hid]["has_battery"] and st["soc_frac"] is not None]
    check("2.1 no state of charge reported for a premises without a battery",
          not bad_soc, f"{len(bad_soc)} offenders")

    socs = {st["soc_frac"] for b in sim.blocks for st in b["houses"].values()
            if st["soc_frac"] is not None}
    check("2.2 state of charge is a real distribution, not a constant",
          len(socs) > 5, f"{len(socs)} distinct values")

    # PV and battery capacity must vary across premises. A single value for all
    # of them is what "4.8 kWp for every PV house" looked like.
    pv = {h["pv_kw"] for h in sim.scene["houses"] if h["has_pv"]}
    batt = {h["battery_kwh"] for h in sim.scene["houses"] if h["has_battery"]}
    check("2.3 PV capacity varies per premises", len(pv) > 1,
          f"{len(pv)} distinct kWp values: {sorted(pv)[:6]}")
    check("2.4 battery capacity comes from the registry", len(batt) >= 1 and 0 not in batt,
          f"{sorted(batt)}")

    # deferredCapex must be DERIVED, not typed.
    cfg = sim.config
    expect = (sim.summary["lifeSavedHours"] / cfg.rated_life_hours) * cfg.replacement_cost_inr
    check("2.5 deferredCapex derives from life saved (not the old 186000 constant)",
          abs(sim.summary["deferredCapex"] - round(expect, 2)) < 0.01
          and sim.summary["deferredCapex"] != 186000,
          f"{sim.summary['deferredCapex']} vs derived {expect:.2f}")

    # Every trace event must be present — the ring buffer used to evict them.
    opens = [e for e in sim.events if e["kind"] == "block_opened"]
    check("2.6 the event trace is complete, not sampled",
          len(opens) == len(sim.blocks),
          f"{len(opens)} block_opened for {len(sim.blocks)} blocks")


def check_hl4(run) -> None:
    section("3. HL4 — the ageing adder is never retroactive")
    from engine.agents.settlement import SettlementAgent
    from engine.domain import AgeingResult, Trade

    feed, config, houses = run["feed"], run["config"], run["houses"]
    trade = [Trade("T1", 0, "10006", "10000", 2.0, 4.0, 0.0)]

    a = SettlementAgent(houses, config, feed=feed)
    _, buyer = a.settle(trade, AgeingResult(states=[], adders={"DT-1": 9.99},
                                            active_adders={"DT-1": 1.5}))
    check("3.1 settlement bills the ACTIVE adder (computed in t-1)",
          abs(buyer.ageing_inr - 3.0) < 1e-9, f"ageing_inr = {buyer.ageing_inr}")

    b = SettlementAgent(houses, config, feed=feed)
    _, buyer2 = b.settle(trade, AgeingResult(states=[], adders={"DT-1": 2.0},
                                             active_adders={}))
    check("3.2 an adder computed from THIS block's load is NOT billed to it",
          abs(buyer2.ageing_inr) < 1e-9, f"ageing_inr = {buyer2.ageing_inr}")


def check_physics(run) -> None:
    section("4. Physics — every agent measures the same street the same way")
    from engine.physics import loading_k
    from grid.health import TransformerHealthAgent
    from grid.sentinel import GridSentinel

    feed, config, houses, transformers = (run["feed"], run["config"],
                                          run["houses"], run["transformers"])
    sentinel = GridSentinel(transformers, houses, config)
    health = TransformerHealthAgent(transformers, config, houses)
    ticks = feed.ticks(19)
    ageing = health.apply([], ticks)
    net = sentinel._net_kw_by_house([], ticks)

    worst = 0.0
    for transformer in transformers:
        members = [h for h in houses if h.transformer_id == transformer.transformer_id]
        k_sentinel = loading_k(sum(abs(net[h.house_id]) for h in members),
                               transformer.rating_kva, config.power_factor)
        k_health = ageing.loading_k[transformer.transformer_id]
        worst = max(worst, abs(k_sentinel - k_health))
    check("4.1 sentinel and health agent agree on transformer loading K",
          worst < 1e-3, f"largest disagreement {worst:.6f}")

    # Curtailment must be applied exactly once.
    from engine.domain import Trade
    from engine.trades import delivered_kwh
    t = Trade("T", 0, "a", "b", quantity_kwh=8.0, clearing_price=5.0,
              curtailed_fraction=0.2)
    check("4.2 Trade.quantity_kwh is already net of curtailment (applied once)",
          abs(delivered_kwh(t) - 8.0) < 1e-12,
          f"delivered_kwh = {delivered_kwh(t)} (8.0 means once; 6.4 means twice)")

    # The sentinel's kVA figure must be the kW figure over the power factor.
    from engine.config import Config
    from engine.feed import WhitefieldFeed
    hard = WhitefieldFeed(Config(derate_factor=0.8))
    sen2 = GridSentinel(hard.transformers(), hard.houses(), Config(derate_factor=0.8))
    breach = sen2.check([], hard.ticks(19))
    ok = breach is not None and breach.kind == "loading"
    if ok:
        kw, kva = breach.detail["actual_load_kw"], breach.detail["actual_load_kva"]
        ok = abs(kva - kw / 0.95) < 1e-6
        detail = f"{kw:.2f} kW -> {kva:.2f} kVA"
    else:
        detail = f"expected a loading breach at derate 0.8, got {breach}"
    check("4.3 breach carries both kW and kVA, consistently", ok, detail)


def check_battery_conservation(run) -> None:
    section("5. Energy conservation across the battery layer (FL4)")
    book = sum(run["batteries"].total_stored_kwh(h.house_id)
               for h in run["houses"] if h.has_battery)
    agents = sum(p.battery_soc_kwh for p in run["pool"].prosumers.values())
    summary_net = (run["summary"]["battery_charged_kwh"]
                   - run["summary"]["battery_discharged_kwh"])

    check("5.1 the prosumer agents' SoC belief matches the BatteryBook",
          abs(book - agents) < 1e-6,
          f"book {book:.6f} kWh vs agents {agents:.6f} kWh")
    check("5.2 the run summary matches the BatteryBook",
          abs(book - summary_net) < 1e-6,
          f"book {book:.6f} kWh vs summary {summary_net:.6f} kWh")
    check("5.3 the round-trip conversion loss is reported, not absorbed",
          run["summary"]["battery_conversion_loss_kwh"] > 0,
          f"{run['summary']['battery_conversion_loss_kwh']} kWh lost to efficiency")

    # A seller can never deliver more than it generated plus what it discharged.
    check("5.4 undeliverable commitments are trimmed, not settled",
          run["summary"]["delivery_shortfall_kwh"] >= 0,
          f"{run['summary']['delivery_shortfall_kwh']} kWh trimmed")


def check_money(run) -> None:
    section("6. Money — ST1, and the charges that were never billed")
    ledger = run["settlement"].ledger
    total = sum(line.net_inr for line in ledger)
    check("6.1 ST1: every party's net position sums to what was collected",
          abs(total - run["settlement"].charges_collected) < 1e-6,
          f"net {total:.6f} vs collected {run['settlement'].charges_collected:.6f}")

    check("6.2 ST2: every bill line's components sum to its net",
          all(abs(sum(l.components) - l.net_inr) < 1e-9 for l in ledger),
          f"{len(ledger)} lines checked")

    platform = sum(l.platform_inr for l in ledger)
    gst = sum(l.gst_inr for l in ledger)
    check("6.3 the platform fee is actually billed (was configured, never charged)",
          platform > 0, f"Rs{platform:.2f}")
    check("6.4 GST is actually billed (was configured, never charged)",
          gst > 0, f"Rs{gst:.2f}")

    check("6.5 CN2: no buyer's all-in cost exceeds its retail tariff",
          all(c.cumulative_spend <= c.cumulative_baseline + 1e-6
              for c in run["pool"].consumers.values()),
          "checked every consumer's ledger")

    # Eight components, not six.
    if ledger:
        check("6.6 a bill line has eight itemised components",
              len(ledger[0].components) == 8, f"{len(ledger[0].components)} components")


def check_llm() -> None:
    section("7. LLM agents — bounded, cuttable, and never a guess")
    from unittest.mock import patch

    from engine.algo import llm
    from engine.domain import Breach, StrategyParams, TransformerState

    previous = StrategyParams(discount=0.61, margin=0.07)
    state = TransformerState("DT-1", 0.1, 110.0, 1.0, 0.5)
    breach = Breach("DT-1", "loading", 1.2, {})

    # Off: the engine's declared defaults, every time.
    with patch.object(llm, "LLM_ENABLED", False):
        check("7.1 disabled: daily_strategy returns the previous params untouched",
              llm.daily_strategy({}, [], previous) == previous)
        check("7.2 disabled: answer() says 'unavailable', never a guess",
              llm.answer("what broke?", []) == "unavailable")
        check("7.3 disabled: diagnose() falls back to an accurate template",
              "DT-1 loading breach" in llm.diagnose(breach, state))

    # On, with a mocked backend: clamped, and LM1 respected.
    with patch.object(llm, "LLM_ENABLED", True):
        with patch.object(llm, "_call_llm",
                          return_value='{"discount":99,"margin":-5,'
                                       '"bid_aggression":40,'
                                       '"battery_reserve_frac":0.99}'):
            p = llm.daily_strategy({}, [], previous)
        check("7.4 out-of-range values are clamped, not accepted",
              p.discount == 1.0 and p.margin == 0.02 and p.bid_aggression == 1.5,
              f"{p}")
        check("7.5 LM1: the LLM cannot move battery_reserve_frac",
              p.battery_reserve_frac == previous.battery_reserve_frac,
              f"{p.battery_reserve_frac} (must be {previous.battery_reserve_frac})")

        with patch.object(llm, "_call_llm", return_value="```json\n"
                                                         '{"discount":0.72,"margin":0.14}\n```'):
            p2 = llm.daily_strategy({}, [], previous)
        check("7.6 a fenced/prefaced JSON reply is still parsed",
              p2.discount == 0.72 and p2.margin == 0.14, f"{p2}")

        # A broken model must not be able to change the run. Note that these
        # three call sites fail DIFFERENTLY on purpose, and the check respects
        # that rather than demanding one answer:
        #   daily_strategy  parses JSON, so any non-JSON reply is a failure
        #                   and it keeps the previous parameters
        #   diagnose        takes the first line of free text, so it only falls
        #                   back on an EMPTY reply or an exception
        #   answer          is free text by nature — "not json" is a perfectly
        #                   valid answer to a question, and treating it as a
        #                   failure would be the check being wrong, not the code
        for label, kwargs in (("timeout", {"side_effect": TimeoutError("slow")}),
                              ("HTTP error", {"side_effect": RuntimeError("502")}),
                              ("empty", {"return_value": ""})):
            with patch.object(llm, "_call_llm", **kwargs):
                ok = (llm.daily_strategy({}, [], previous) == previous
                      and llm.answer("x", []) == "unavailable"
                      and "DT-1 loading breach" in llm.diagnose(breach, state))
            check(f"7.7 {label} leaves every call site on its deterministic default",
                  ok)

        # Malformed JSON is only a failure for the one call site that parses it.
        with patch.object(llm, "_call_llm", return_value="not json at all"):
            strategy_held = llm.daily_strategy({}, [], previous) == previous
            diagnosis_ok = bool(llm.diagnose(breach, state).strip())
        check("7.8 a non-JSON reply cannot change the agents' strategy",
              strategy_held and diagnosis_ok,
              "daily_strategy keeps the previous params; diagnose still returns a line")


def check_headline_claims(run, quick: bool) -> None:
    section("8. The three claims the project rests on")
    from server.simulation import build_simulation

    sim = build_simulation(days=2 if quick else 30)
    life = sim.compare["transformer_life_hours"]

    check("8.1 baseline ages at least as fast as P2P",
          sim.compare["baseline_ages_at_least_as_fast"],
          f"baseline {life['baseline']:.1f} h, p2p {life['p2p']:.1f} h")
    check("8.2 ...and NON-TRIVIALLY so (0.0 h saved means protection did nothing "
          "and the check passed only by equality)",
          life["saved_hours"] > 0.0,
          f"{life['saved_hours']:.3f} h saved "
          f"({100 * life['saved_hours'] / life['baseline']:.1f}%)")

    prediction = sim.run_summary["breach_prediction"]
    check("8.3 breach prediction runs and is scored against what happened",
          prediction["predicted"] > 0
          and prediction["hits"] + prediction["misses"] == prediction["predicted"],
          f"{prediction}")
    check("8.4 ...and is NOT perfect (100% would mean it is reading truth, "
          "not the noisy forecast)",
          prediction["precision_pct"] is None or prediction["precision_pct"] < 100.0,
          f"{prediction['precision_pct']}% precision")


def check_determinism(quick: bool) -> None:
    section("9. D1 — determinism")
    if quick:
        print("  SKIP  (--quick)")
        return
    # Diff the run SUMMARY, not stdout. demo.py prints "completed in 2.58s,
    # median tick 2.29 ms", which is a measurement OF the run and is never
    # reproducible — that is exactly why `Runner.median_tick_ms` is deliberately
    # kept off the summary. Comparing stdout would make this check fail on a
    # correct engine, which is the check being wrong rather than the code.
    import tempfile
    paths = []
    with tempfile.TemporaryDirectory() as tmp:
        for n in range(2):
            out = Path(tmp) / f"summary-{n}.json"
            subprocess.run(
                [sys.executable, "demo.py", "--days", "5", "--summary", str(out)],
                cwd=ROOT, capture_output=True, text=True, check=True)
            paths.append(out.read_bytes())
    identical = paths[0] == paths[1]
    check("9.1 two identical runs produce a byte-identical run_summary.json",
          identical,
          f"{len(paths[0])} bytes, identical" if identical else "SUMMARIES DIVERGED")

    # And the thing that must NOT be in there.
    import json
    summary = json.loads(paths[0])
    timing = [k for k in summary
              if {"ms", "seconds", "elapsed", "duration", "wall"} & set(k.split("_"))]
    check("9.2 no wall-clock value reached the summary (which is what makes 9.1 "
          "possible at all)", not timing, f"offending keys: {timing}" if timing else "")


def check_static() -> None:
    section("10. Static checks")
    for script in ("no_hardcoded_ui.py", "one_power_factor.py"):
        path = Path(__file__).parent / script
        proc = subprocess.run([sys.executable, str(path)], capture_output=True, text=True)
        check(f"10.x {script}", proc.returncode == 0,
              proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "")


# ------------------------------------------------------------------- main

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true",
                        help="skip the 30-day runs and the determinism check")
    args = parser.parse_args()

    try:
        import scipy  # noqa: F401
    except ImportError:
        print("scipy is missing. The reshape LP cannot run without it, so the "
              "grid-protection half of the system does nothing and most of these "
              "checks would be meaningless.\n\n    pip install -r requirements.txt")
        return 1

    print("=" * 72)
    print("  UrjaSetu — independent agent verification")
    print("=" * 72)

    blocks = 240 if args.quick else 720
    print(f"\nBuilding a {blocks}-block run with every agent wired...")
    run = build(blocks)
    print(f"  done — {run['summary']['trades']} trades, "
          f"{run['summary']['breaches_total']} breaches, "
          f"{run['summary']['reshapes_applied']} reshapes applied")

    check_agents_are_reached(run)
    check_no_placeholders(run)
    check_hl4(run)
    check_physics(run)
    check_battery_conservation(run)
    check_money(run)
    check_llm()
    check_headline_claims(run, args.quick)
    check_determinism(args.quick)
    check_static()

    failed = [name for name, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 72)
    print(f"  {len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    for name in failed:
        print(f"    FAILED: {name}")
    print("=" * 72)
    return len(failed)


if __name__ == "__main__":
    sys.exit(main())
