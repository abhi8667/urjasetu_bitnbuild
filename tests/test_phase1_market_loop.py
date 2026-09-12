"""Phase 1 — bus, market agent, tick loop.

Runs under pytest, or standalone with `python3 tests/test_phase1_market_loop.py`.
Stdlib only, so it runs on a clean machine.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.bus import TOPICS, Bus
from engine.agents.market import MarketAgent
from engine.config import Config
from engine.domain import Breach, InvariantError, Order, ReshapePlan
from engine.feed import WhitefieldFeed
from engine.sim.naive import NaiveOrderSource
from engine.sim.runner import MAX_CLEARING_PASSES, Runner

CONFIG = Config()
FEED = WhitefieldFeed(CONFIG)


def _book(offers, bids, block=0):
    """offers/bids are (price, qty) pairs."""
    orders = [Order(f"o{i}", block, f"S{i}", "offer", q, p)
              for i, (p, q) in enumerate(offers)]
    orders += [Order(f"b{i}", block, f"B{i}", "bid", q, p)
               for i, (p, q) in enumerate(bids)]
    return orders


def _runner(**kw):
    return Runner(FEED, NaiveOrderSource(FEED.houses(), CONFIG), CONFIG, **kw)


# ------------------------------------------------------------------- bus

def test_bus_dispatches_in_subscription_order():
    bus, seen = Bus(), []
    bus.subscribe("market_cleared", lambda e: seen.append("first"))
    bus.subscribe("market_cleared", lambda e: seen.append("second"))
    bus.publish("market_cleared", 0, "market", {})
    assert seen == ["first", "second"]


def test_bus_ring_is_bounded_and_ordered():
    bus = Bus(ring_size=10)
    for i in range(25):
        bus.publish("block_opened", i, "runner", {})
    assert len(bus.ring) == 10
    assert [e["block"] for e in bus.recent_events()] == list(range(15, 25))


def test_bus_keeps_the_event_when_a_subscriber_raises():
    """A crash that vanishes from the trace is far harder to diagnose than one
    that does not — so the event is appended before dispatch."""
    bus = Bus()
    bus.subscribe("block_opened", lambda e: (_ for _ in ()).throw(RuntimeError("boom")))
    try:
        bus.publish("block_opened", 7, "runner", {})
    except RuntimeError:
        pass
    assert [e["block"] for e in bus.ring] == [7]


def test_every_event_carries_block_agent_and_payload():
    bus = Bus()
    bus.publish("block_settled", 3, "runner", {"trades": 2})
    event = bus.ring[0]
    assert set(event) == {"topic", "block", "agent_id", "payload"}


# --------------------------------------------------- hour-6 gate: clearing

def test_hour6_gate_five_by_five_book_clears_at_the_analytic_price():
    """Offers at 2,3,4,5,6 and bids at 7,6.5,5.5,3.5,2.5, all 2 kWh.

    Matching stops when 3.5 < 5.0, so the last matched pair is offer 4.0 against
    bid 5.5 and the uniform price is their midpoint, 4.75. Three pairs match, so
    6 kWh trades and every trade settles at 4.75 — not at its own limit.
    """
    orders = _book([(2.0, 2), (3.0, 2), (4.0, 2), (5.0, 2), (6.0, 2)],
                   [(7.0, 2), (6.5, 2), (5.5, 2), (3.5, 2), (2.5, 2)])
    result = MarketAgent(Bus()).clear(0, orders)
    assert result.clearing_price == 4.75
    assert len(result.trades) == 3
    assert abs(sum(t.quantity_kwh for t in result.trades) - 6.0) < 1e-9
    assert all(t.clearing_price == 4.75 for t in result.trades)


def test_partial_fill_splits_the_larger_order():
    orders = _book([(3.0, 5)], [(6.0, 3), (5.0, 3)])
    result = MarketAgent(Bus()).clear(0, orders)
    assert sorted(round(t.quantity_kwh, 6) for t in result.trades) == [2.0, 3.0]
    assert result.clearing_price == 4.0


def test_no_overlap_yields_no_trades_and_no_price():
    result = MarketAgent(Bus()).clear(0, _book([(6.0, 2)], [(3.0, 2)]))
    assert result.trades == [] and result.clearing_price is None


def test_empty_book_does_not_raise():
    result = MarketAgent(Bus()).clear(0, [])
    assert result.trades == [] and result.clearing_price is None


def test_mk3_shuffled_input_gives_identical_output():
    import random
    orders = _book([(2.0, 2), (3.0, 2), (4.0, 2)], [(7.0, 2), (6.5, 2), (5.5, 2)])
    baseline = MarketAgent(Bus()).clear(0, orders)
    shuffled = list(orders)
    random.Random(1).shuffle(shuffled)
    other = MarketAgent(Bus()).clear(0, shuffled)
    assert [t.__dict__ for t in baseline.trades] == [t.__dict__ for t in other.trades]


def test_mk1_violation_raises_at_the_call_site():
    """B owns the guarantee that what D returned is usable, so a bad auction
    upgrade surfaces here with the order book in hand."""
    from engine import algo
    from engine.domain import ClearingResult, Trade
    real = algo.auction.clear
    algo.auction.clear = lambda orders: ClearingResult(
        trades=[Trade("bogus", 0, "S0", "B0", 999.0, 4.0, 0.0)],
        clearing_price=4.0, unmatched_offers=[], unmatched_bids=[])
    try:
        MarketAgent(Bus()).clear(0, _book([(3.0, 2)], [(6.0, 2)]))
        raise AssertionError("MK1 violation was not caught")
    except InvariantError as exc:
        assert "MK1" in str(exc)
    finally:
        algo.auction.clear = real


def test_market_publishes_a_cleared_event():
    bus = Bus()
    MarketAgent(bus).clear(4, _book([(3.0, 2)], [(6.0, 2)]))
    events = bus.recent_events("market_cleared")
    assert len(events) == 1 and events[0]["block"] == 4
    assert events[0]["payload"]["clearing_price"] == 4.5


# ----------------------------------------------------------- the tick loop

def test_loop_runs_a_full_day_without_raising():
    summary = _runner().run(blocks=CONFIG.blocks_per_day)
    assert summary["blocks"] == 24
    assert summary["trades"] > 0


def test_loop_runs_all_720_blocks():
    summary = _runner().run()
    assert summary["blocks"] == 720
    assert summary["days"] == 30.0
    assert summary["traded_kwh"] > 0


def test_ring_holds_plausible_events():
    runner = _runner()
    runner.run(blocks=CONFIG.blocks_per_day)
    topics = {e["topic"] for e in runner.bus.ring}
    assert {"block_opened", "order_submitted", "market_cleared", "block_settled"} <= topics
    assert topics <= set(TOPICS)


def test_trading_only_happens_in_daylight():
    """18 of 64 nodes generate, and only in hours 09-15. A trade at 03:00 means
    the order source is inventing energy."""
    runner = _runner()
    runner.run(blocks=CONFIG.blocks_per_day)
    traded_hours = {e["block"] for e in runner.bus.recent_events("block_settled")
                    if e["payload"]["trades"] > 0}
    assert traded_hours and all(6 <= h <= 18 for h in traded_hours), traded_hours


def test_p3_median_tick_is_well_under_50ms():
    runner = _runner()
    runner.run(blocks=CONFIG.blocks_per_day)
    assert runner.median_tick_ms < 50.0


#: Key SEGMENTS that would mean a wall-clock figure reached the summary.
#: Matched per underscore-separated segment, not as substrings: the substring
#: form flagged "storage_claims_opened" because "claims" contains "ms", which is
#: a false positive on a field that is a count of storage claims.
_WALL_CLOCK_SEGMENTS = {"ms", "seconds", "secs", "elapsed", "duration",
                        "wall", "runtime", "latency", "timestamp"}


def test_run_summary_carries_no_wall_clock_value():
    """D1 can only hold if nothing timing-dependent reaches the summary."""
    summary = _runner().run(blocks=24)
    offending = [k for k in summary
                 if _WALL_CLOCK_SEGMENTS & set(k.split("_"))]
    assert not offending, f"wall-clock keys reached run_summary: {offending}"


def test_d1_two_identical_runs_produce_identical_summaries():
    """Run twice and diff at hour 12, not hour 30."""
    assert _runner().run(blocks=48) == _runner().run(blocks=48)


# --------------------------------------------- P1: the two-pass bound

class _AlwaysBreaching:
    def check(self, trades, ticks):
        return Breach("DT-1", "loading", 1.2, {})


class _UselessFlow:
    """Reshapes, never fixes anything — forces the fallback path."""
    def __init__(self):
        self.fallbacks = 0

    def reshape(self, trades, ticks, breach, batteries):
        return ReshapePlan(constrained_orders=[], battery_charges={},
                           new_claims=[], feasible=True, objective_value=0.0)

    def fallback_curtail(self, trades, breach):
        self.fallbacks += 1
        return []


def test_p1_a_persistent_breach_ends_in_the_fallback_not_a_third_pass():
    flow = _UselessFlow()
    runner = _runner(sentinel=_AlwaysBreaching(), flow=flow)
    runner.run(blocks=6)
    assert flow.fallbacks == 6
    cleared = [e for e in runner.bus.ring if e["topic"] == "market_cleared"]
    assert len(cleared) == 6 * MAX_CLEARING_PASSES
    assert len(runner.bus.recent_events("fallback_curtailed")) == 6


def test_grid_agents_are_optional_in_phase_1():
    """C's modules are not written yet; the loop must not care."""
    runner = _runner(sentinel=None, flow=None, health=None, settlement=None)
    summary = runner.run(blocks=24)
    assert summary["reshaped_blocks"] == 0
    assert not runner.bus.recent_events("breach_detected")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {fn.__name__}: {exc}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
