#!/usr/bin/env python3
"""Run the whole system once and print what it did.

    python3 demo.py            30 days (default)
    python3 demo.py --days 1   one day, fast
    python3 demo.py --derate 0.8   force harder breaches (the demo control)
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine.agents.settlement import SettlementAgent
from engine.bus import Bus
from engine.config import DEFAULT
from engine.feed import WhitefieldFeed
from engine.sim.baseline import Baseline, compare, p2p_economics
from engine.sim.pool import AgentPool
from engine.sim.runner import Runner


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--derate", type=float, default=1.0,
                    help="transformer derate factor; 0.8 forces breaches")
    ap.add_argument("--no-protection", action="store_true",
                    help="run without C's grid agents, to compare")
    ap.add_argument("--summary", default="run_summary.json",
                    help="where to write the run summary (PRD §10)")
    args = ap.parse_args()

    config = replace(DEFAULT, derate_factor=args.derate)
    blocks = args.days * config.blocks_per_day

    counts = Counter()

    class CountingBus(Bus):
        def publish(self, topic, block, agent_id, payload=None):
            counts[topic] += 1
            if topic == "reshape_applied" and payload:
                counts["discharge_kwh"] += payload.get("battery_discharge_kwh", 0.0)
            if topic == "breach_detected" and payload:
                counts["breach_" + payload.get("kind", "?")] += 1
            return super().publish(topic, block, agent_id, payload)

    feed = WhitefieldFeed(config)
    houses, transformers = feed.houses(), feed.transformers()
    pool = AgentPool(houses, config)
    settle = SettlementAgent(houses, config, consumers=pool.consumers, feed=feed)

    grid_kwargs, health = {}, None
    if not args.no_protection:
        from grid import BatteryBook, FlowAgent, GridSentinel, TransformerHealthAgent
        health = TransformerHealthAgent(transformers, config, houses)
        grid_kwargs = dict(
            sentinel=GridSentinel(transformers, houses, config),
            flow=FlowAgent(transformers, houses, config),
            health=health,
            batteries=BatteryBook(houses, config),
        )

    bus = CountingBus()
    # The settlement agent gets the bus so its own events reach the ring. It was
    # constructed without one, so every event it published went nowhere.
    settle.bus = bus
    # include_baseline=False: the runner computes the counterfactual for the run
    # summary, and this script computed it AGAIN below for the compare screen —
    # the same 720-block loop twice. Run it once, here, and hand it to both.
    runner = Runner(feed, pool, config, bus=bus, settlement=settle,
                    include_baseline=False, **grid_kwargs)
    started = time.perf_counter()
    summary = runner.run(blocks=blocks)
    wall = time.perf_counter() - started

    bar = "=" * 62
    print(f"\n{bar}\n  UrjaSetu — {args.days} day run"
          f"{' (protection OFF)' if args.no_protection else ''}"
          f"{f', derate {args.derate}' if args.derate != 1.0 else ''}\n{bar}")
    print(f"  {len(houses)} premises on {len(transformers)} transformers, "
          f"{summary['blocks']} hourly blocks")
    print(f"  completed in {wall:.2f}s, median tick {runner.median_tick_ms:.2f} ms\n")

    print("  MARKET")
    print(f"    orders submitted     {summary['orders_submitted']:>10,}")
    print(f"    trades cleared       {summary['trades']:>10,}")
    print(f"    energy traded        {summary['traded_kwh']:>10,.1f} kWh")
    sold = sum(l.quantity_kwh for l in settle.ledger if l.role == "seller")
    delivered = sum(l.quantity_kwh for l in settle.ledger if l.role == "buyer")
    lost = sum(l.loss_kwh for l in settle.ledger)
    print(f"    delivered to buyers  {delivered:>10,.1f} kWh")
    print(f"    lost in the wires    {lost:>10,.1f} kWh "
          f"({100 * lost / sold if sold else 0:.1f}%)")
    print(f"    mean clearing price  {summary['mean_clearing_price_inr'] or 0:>10.2f} INR/kWh")
    print(f"    P2P share of demand  {summary['p2p_share_of_demand_pct']:>10.2f} %")

    if not args.no_protection:
        print("\n  GRID PROTECTION")
        print(f"    breaches detected    {counts['breach_detected']:>10}")
        for kind in ("loading", "phase", "voltage"):
            if counts.get("breach_" + kind):
                print(f"      {kind:<17}  {counts['breach_' + kind]:>10}")
        print(f"    reshapes applied     {counts['reshape_applied']:>10}")
        print(f"    fallback curtails    {counts['fallback_curtailed']:>10}")
        print(f"    battery discharged   {counts['discharge_kwh']:>10,.1f} kWh")
        print(f"    breaches PREDICTED   {counts['breach_predicted']:>10}"
              f"   ({summary['breach_prediction']['precision_pct'] or 0:.0f}% precision)")

    print("\n  MONEY")
    net = sum(l.net_inr for l in settle.ledger)
    print(f"    bill lines           {len(settle.ledger):>10,}")
    print(f"    DISCOM charges       {settle.charges_collected:>10,.2f} INR")
    print(f"    ST1 (money conserved){'  OK' if abs(net - settle.charges_collected) < 1e-6 else '  VIOLATED':>10}")

    if health is not None:
        life = sum(health._cumulative_life_hours.values())
        baseline_result = Baseline(feed, config).run(blocks=blocks)
        result = compare(p2p_economics(feed, config, settle.ledger, blocks=blocks,
                                       loss_of_life_hours=life),
                         baseline_result)
        summary["baseline"] = {
            "label": baseline_result.label,
            "household_bills_inr": baseline_result.household_bills_inr,
            "discom_energy_revenue_inr": baseline_result.discom_energy_revenue_inr,
            "discom_charge_revenue_inr": baseline_result.discom_charge_revenue_inr,
            "export_credits_inr": baseline_result.export_credits_inr,
            "loss_of_life_hours_total": baseline_result.loss_of_life_hours,
        }
        t = result["transformer_life_hours"]
        print("\n  VS NET METERING (same feed, same seed)")
        print(f"    baseline life used   {t['baseline']:>10,.1f} h")
        print(f"    P2P life used        {t['p2p']:>10,.1f} h")
        print(f"    life SAVED           {t['saved_hours']:>10,.1f} h  "
              f"({100 * t['saved_hours'] / t['baseline'] if t['baseline'] else 0:.1f}%)")
        h = result["household_bills_inr"]
        d = result["discom_revenue_inr"]
        print(f"    household bills      {h['saved_inr']:>+10,.0f} INR")
        print(f"    DISCOM revenue       {d['gained_inr']:>+10,.0f} INR")
    path = runner.write_run_summary(args.summary)
    print(f"  run summary written to {path}")
    print(f"{bar}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
