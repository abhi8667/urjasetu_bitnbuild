"""Meter feed validation — PRD §12.

    | Meter feed gap | Raise at startup during feed validation, not mid-run |

    "The engine validates the entire meter feed before block 0. A run that
     starts must be able to finish."

No validation existed. A gap in the feed surfaced as a KeyError at whatever
block it happened to hit — block 400, say, by which point four hundred blocks
of compute are spent and the database holds a partial run. The cause is then
hundreds of blocks behind the symptom.

Every test here injects a specific defect and checks it is caught BEFORE block 0
rather than during it.

Run standalone: `python3 tests/test_feed_validation.py`
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.config import DEFAULT as CONFIG
from engine.domain import MeterTick
from engine.feed import FeedValidationError, WhitefieldFeed
from engine.sim.pool import AgentPool
from engine.sim.runner import Runner

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    return ok


def _expect_rejection(feed, label, expect_in=""):
    """The feed must be rejected by validate(), with a message that says why."""
    try:
        feed.validate()
        return check(label, False, "validate() accepted a broken feed")
    except FeedValidationError as exc:
        ok = expect_in.lower() in str(exc).lower() if expect_in else True
        return check(label, ok, str(exc)[:90])


class _GappyFeed(WhitefieldFeed):
    """Drops one premises from one block — the classic feed gap."""

    def __init__(self, config, drop_house, drop_block):
        super().__init__(config)
        self._drop_house, self._drop_block = drop_house, drop_block

    def ticks(self, block):
        rows = super().ticks(block)
        if block == self._drop_block:
            return [t for t in rows if t.house_id != self._drop_house]
        return rows


class _CorruptFeed(WhitefieldFeed):
    """Injects one bad value at one block."""

    def __init__(self, config, block, field, value):
        super().__init__(config)
        self._block, self._field, self._value = block, field, value

    def ticks(self, block):
        rows = super().ticks(block)
        if block != self._block:
            return rows
        first = rows[0]
        return [replace(first, **{self._field: self._value})] + list(rows[1:])


# ---------------------------------------------------- the headline case

def test_a_missing_premises_is_caught_before_block_zero():
    feed = _GappyFeed(CONFIG, drop_house="10000", drop_block=400)
    _expect_rejection(feed, "a gap at block 400 is caught at validation",
                      "missing")


def test_the_gap_is_caught_before_the_run_does_any_work():
    """The point of §12: the run must refuse to start, not fail partway."""
    feed = _GappyFeed(CONFIG, drop_house="10000", drop_block=400)
    runner = Runner(feed, AgentPool(feed.houses(), CONFIG), CONFIG,
                    include_baseline=False)
    try:
        runner.run()
        check("a run on a gappy feed refuses to start", False, "it ran")
    except FeedValidationError:
        check("a run on a gappy feed refuses to start", True,
              f"summary is {runner.summary!r}, so no blocks were settled")


def test_an_unknown_premises_in_the_readings_is_caught():
    class ExtraFeed(WhitefieldFeed):
        def ticks(self, block):
            rows = list(super().ticks(block))
            if block == 10:
                rows.append(MeterTick(block, "GHOST-1", 1.0, 0.0, 25.0))
            return rows
    _expect_rejection(ExtraFeed(CONFIG), "a reading for an unknown premises is caught",
                      "does not know")


# ------------------------------------------------- physically impossible

def test_negative_energy_is_caught():
    _expect_rejection(_CorruptFeed(CONFIG, 5, "load_kwh", -1.0),
                      "negative energy is caught", "negative")


def test_nan_is_caught():
    _expect_rejection(_CorruptFeed(CONFIG, 5, "gen_kwh", float("nan")),
                      "NaN is caught", "nan")


def test_an_absurd_ambient_temperature_is_caught():
    _expect_rejection(_CorruptFeed(CONFIG, 5, "ambient_c", 500.0),
                      "an impossible ambient temperature is caught", "ambient")


# ------------------------------------------------------ registry defects

def test_a_premises_on_an_undefined_transformer_is_caught():
    class OrphanFeed(WhitefieldFeed):
        def houses(self):
            rows = list(super().houses())
            return [replace(rows[0], transformer_id="DT-99")] + rows[1:]
    _expect_rejection(OrphanFeed(CONFIG),
                      "a premises on an undefined transformer is caught",
                      "does not define")


def test_equipment_flags_must_match_capacities():
    class LyingFeed(WhitefieldFeed):
        def houses(self):
            rows = list(super().houses())
            return [replace(rows[0], has_pv=True, pv_kw=0.0)] + rows[1:]
    _expect_rejection(LyingFeed(CONFIG),
                      "has_pv with zero capacity is caught", "has_pv")


def test_a_zero_retail_tariff_is_caught():
    class FreeFeed(WhitefieldFeed):
        def houses(self):
            rows = list(super().houses())
            return [replace(rows[0], retail_tariff=0.0)] + rows[1:]
    _expect_rejection(FreeFeed(CONFIG), "a zero retail tariff is caught", "tariff")


def test_an_empty_feed_is_caught():
    class EmptyFeed(WhitefieldFeed):
        def houses(self):
            return []
    _expect_rejection(EmptyFeed(CONFIG), "a feed with no premises is caught",
                      "no premises")


# -------------------------------------------------------- the good case

def test_the_real_feed_validates_completely():
    """Not a sample — every block of the shipped dataset."""
    report = WhitefieldFeed(CONFIG).validate()
    check("the shipped feed validates end to end",
          report["complete"] and report["blocks_checked"] == report["blocks_total"],
          f"{report['blocks_checked']} blocks, {report['premises']} premises")


def test_validation_is_cheap_enough_to_always_run():
    import time
    started = time.perf_counter()
    WhitefieldFeed(CONFIG).validate()
    elapsed = time.perf_counter() - started
    check("validating 720 blocks costs under a second", elapsed < 1.0,
          f"{elapsed:.3f}s — and it warms the tick cache the run then reuses")


def test_a_runner_records_that_validation_happened():
    feed = WhitefieldFeed(CONFIG)
    runner = Runner(feed, AgentPool(feed.houses(), CONFIG), CONFIG,
                    include_baseline=False)
    runner.run(blocks=24)
    check("the runner records the validation it performed",
          runner.feed_validation is not None
          and runner.feed_validation["complete"],
          str(runner.feed_validation))


def test_validation_can_be_disabled_for_stub_feeds():
    """Tests that drive a deliberately partial feed must still be able to."""
    feed = _GappyFeed(CONFIG, drop_house="10000", drop_block=400)
    runner = Runner(feed, AgentPool(feed.houses(), CONFIG), CONFIG,
                    include_baseline=False, validate_feed=False)
    summary = runner.run(blocks=24)
    check("validation is skippable for stub feeds",
          summary["blocks"] == 24 and runner.feed_validation is None)


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
