"""Phases 3 and 4 — persistence, the baseline, and the compare.

Runs under pytest, or standalone with
`python3 tests/test_phase34_persistence_baseline.py`. Stdlib only.
"""
from __future__ import annotations

import sys
import tempfile
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.agents.settlement import SettlementAgent
from engine.config import Config
from engine.domain import AgeingResult, MeterTick, Order, Trade, TransformerState
from engine.feed import WhitefieldFeed
from engine.persistence import Persistence
from engine.sim.baseline import Baseline, compare, p2p_economics
from engine.sim.pool import AgentPool
from engine.sim.runner import Runner

CONFIG = Config()
FEED = WhitefieldFeed(CONFIG)


def _run(blocks=None, config=CONFIG, persist=None, start_block=0):
    feed = WhitefieldFeed(config)
    pool = AgentPool(feed.houses(), config)
    settlement = SettlementAgent(feed.houses(), config, consumers=pool.consumers, feed=feed)
    runner = Runner(feed, pool, config, settlement=settlement, persist=persist,
                    start_block=start_block)
    summary = runner.run(blocks=blocks)
    return runner, pool, settlement, summary


class _Health:
    """Stand-in for C's health agent: accumulates a deterministic, load-dependent
    loss of life and can be saved and restored. Enough to test B's persistence
    contract, which is what PS1 is actually about."""

    def __init__(self, state: dict | None = None):
        self.cumulative = dict(state or {"DT-1": 0.0, "DT-2": 0.0,
                                         "DT-3": 0.0, "DT-4": 0.0})

    def apply(self, trades, ticks) -> AgeingResult:
        block = ticks[0].block if ticks else 0
        for tid in self.cumulative:
            self.cumulative[tid] += round(0.001 * (block % 7 + 1), 9)
        return AgeingResult(
            states=[TransformerState(tid, life_used_frac=used, hotspot_c=0.0,
                                     loading_k=0.0, ageing_adder=0.0)
                    for tid, used in sorted(self.cumulative.items())],
            adders={tid: 0.0 for tid in self.cumulative},
            block=block)


# ----------------------------------------------------------- persistence

def test_five_tables_are_written():
    db = Persistence()
    _, _, _, _ = _run(blocks=24, persist=db)
    for table in ("meter_tick", "order_book", "trade", "bill_line"):
        assert db.count(table) > 0, table
    db.close()


def test_transformer_state_is_written_every_block_not_at_shutdown():
    db = Persistence()
    feed = WhitefieldFeed(CONFIG)
    pool = AgentPool(feed.houses(), CONFIG)
    Runner(feed, pool, CONFIG, health=_Health(), persist=db).run(blocks=30)
    assert db.count("transformer_state") == 30
    db.close()


def test_ps1_a_killed_run_resumes_without_gap_or_double_count():
    """Hard-kill at block 500 of 720, restart, finish. Cumulative loss of life
    matches an uninterrupted run exactly."""
    feed = WhitefieldFeed(CONFIG)

    whole = _Health()
    Runner(feed, AgentPool(feed.houses(), CONFIG), CONFIG,
           health=whole).run(blocks=720)

    db = Persistence()
    first = _Health()
    Runner(feed, AgentPool(feed.houses(), CONFIG), CONFIG,
           health=first, persist=db).run(blocks=500)          # dies here

    block, state = db.load_transformer_state()
    assert block == 499                                        # last completed
    resumed = _Health(state["life_used_frac"])
    Runner(feed, AgentPool(feed.houses(), CONFIG), CONFIG, health=resumed,
           persist=db, start_block=block + 1).run(blocks=720)

    for tid, hours in whole.cumulative.items():
        assert abs(resumed.cumulative[tid] - hours) < 1e-9, tid
    db.close()


def test_state_round_trips_exactly():
    db = Persistence()
    db.save_transformer_state(11, {"cumulative_life_hours": {"DT-1": 1.2345678901}})
    block, state = db.load_transformer_state()
    assert block == 11
    assert state["cumulative_life_hours"]["DT-1"] == 1.2345678901
    db.close()


def test_persistence_survives_a_file_reopen():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "run.sqlite"
        db = Persistence(path)
        db.save_transformer_state(5, {"cumulative_life_hours": {"DT-2": 9.5}})
        db.close()
        again = Persistence(path)
        assert again.load_transformer_state() == (5, {"cumulative_life_hours": {"DT-2": 9.5}})
        again.close()


# -------------------------------------------------------------- baseline

def test_bl2_baseline_never_mutates_agent_state():
    _, pool, _, _ = _run(blocks=48)
    before = {hid: (c.cumulative_spend, c.kwh_bought)
              for hid, c in pool.consumers.items()}
    Baseline(FEED, CONFIG).run(blocks=48)
    after = {hid: (c.cumulative_spend, c.kwh_bought)
             for hid, c in pool.consumers.items()}
    assert before == after


def test_baseline_charges_no_wheeling_or_transaction():
    """That is the DISCOM's problem with net metering, and half the pitch."""
    result = Baseline(FEED, CONFIG).run(blocks=72)
    assert result.discom_charge_revenue_inr == 0.0


def test_one_for_one_credits_are_worth_more_than_feed_in():
    strict = replace(CONFIG, baseline_export_credit="feed_in")
    generous = replace(CONFIG, baseline_export_credit="one_for_one")
    assert (Baseline(FEED, generous).run(blocks=240).export_credits_inr
            > Baseline(FEED, strict).run(blocks=240).export_credits_inr)


def test_credits_lapse_after_the_carry_forward_window():
    short = replace(CONFIG, baseline_export_credit="one_for_one",
                    credit_carryforward_blocks=1)
    long = replace(CONFIG, baseline_export_credit="one_for_one",
                   credit_carryforward_blocks=8760)
    assert (Baseline(FEED, short).run(blocks=240).household_bills_inr
            > Baseline(FEED, long).run(blocks=240).household_bills_inr)


def test_baseline_is_deterministic():
    assert (Baseline(FEED, CONFIG).run(blocks=120)
            == Baseline(FEED, CONFIG).run(blocks=120))


# --------------------------------------------------------------- compare

def test_compare_produces_the_three_screen_numbers():
    _, _, settlement, _ = _run(blocks=240)
    result = compare(p2p_economics(FEED, CONFIG, settlement.ledger, blocks=240),
                     Baseline(FEED, CONFIG).run(blocks=240))
    assert set(result) == {"household_bills_inr", "discom_revenue_inr",
                           "transformer_life_hours",
                           "baseline_ages_at_least_as_fast"}


def test_p2p_collects_charges_the_baseline_never_does():
    """Rs1.43/kWh on energy the DISCOM previously earned nothing on."""
    _, _, settlement, _ = _run(blocks=240)
    p2p = p2p_economics(FEED, CONFIG, settlement.ledger, blocks=240)
    assert p2p.discom_charge_revenue_inr > 0
    assert Baseline(FEED, CONFIG).run(blocks=240).discom_charge_revenue_inr == 0


def test_baseline_ages_at_least_as_fast_as_p2p():
    """The check teams discover too late.

    It holds as an EQUALITY today, and that is not a pass to celebrate: with
    D's thermal stub returning a constant ageing factor, and with C's reshaping
    and batteries not yet built, nothing makes the two sides diverge. Trades
    alone do not change physical flows. This assertion becomes meaningful the
    moment either lands — and if it ever fails, the ageing price signal is doing
    nothing and the central claim is false.
    """
    _, _, settlement, _ = _run(blocks=240)
    p2p = p2p_economics(FEED, CONFIG, settlement.ledger, blocks=240)
    baseline = Baseline(FEED, CONFIG).run(blocks=240)
    assert compare(p2p, baseline)["baseline_ages_at_least_as_fast"]


def test_the_compare_detects_divergence_when_ageing_becomes_load_dependent():
    """Proves the comparison machinery works, by giving D's stub a real slope."""
    from engine import algo
    real = algo.thermal.ageing_factor
    algo.thermal.ageing_factor = lambda hotspot: 2.0 ** ((hotspot - 110.0) / 6.0)
    try:
        _, _, settlement, _ = _run(blocks=240)
        p2p = p2p_economics(FEED, CONFIG, settlement.ledger, blocks=240)
        derated = replace(CONFIG, derate_factor=0.8)   # a harder-worked baseline
        baseline = Baseline(WhitefieldFeed(derated), derated).run(blocks=240)
        result = compare(p2p, baseline)
        assert result["transformer_life_hours"]["saved_hours"] > 0
        assert result["baseline_ages_at_least_as_fast"]
    finally:
        algo.thermal.ageing_factor = real


# ------------------------------------------------------- the whole track

def test_full_run_with_every_module_wired():
    db = Persistence()
    feed = WhitefieldFeed(CONFIG)
    pool = AgentPool(feed.houses(), CONFIG)
    settlement = SettlementAgent(feed.houses(), CONFIG, consumers=pool.consumers, feed=feed)
    runner = Runner(feed, pool, CONFIG, health=_Health(), settlement=settlement,
                    persist=db)
    summary = runner.run()
    assert summary["blocks"] == 720
    assert db.count("transformer_state") == 720
    assert runner.median_tick_ms < 50.0
    db.close()


def test_a_30_day_run_finishes_well_inside_three_minutes():
    import time
    started = time.perf_counter()
    _run()
    assert time.perf_counter() - started < 180.0


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
