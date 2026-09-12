"""Contract smoke tests over the locked dataset.

Runs under pytest, or standalone with `python tests/test_feed_contracts.py` —
stdlib only, so all four tracks can run it at hour 0 before installing anything.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.config import Config
from engine.feed import WhitefieldFeed

FEED = WhitefieldFeed()


def test_topology_matches_dataset():
    houses = FEED.houses()
    metered = [h for h in houses if not h.house_id.startswith("EVHUB")]
    assert len(metered) == 60
    assert len({h.transformer_id for h in houses}) == 4
    assert sum(h.has_pv for h in houses) == 18
    assert sum(h.has_battery for h in houses) == 8


def test_every_house_field_is_populated():
    for h in FEED.houses():
        assert h.phase in ("A", "B", "C")
        assert h.distance_m > 0
        assert h.retail_tariff > 0
        assert (h.battery_max_kw > 0) == h.has_battery
        assert (h.pv_kw > 0) == h.has_pv


def test_phases_are_balanced_within_each_transformer():
    counts = defaultdict(lambda: defaultdict(int))
    for h in FEED.houses():
        counts[h.transformer_id][h.phase] += 1
    for tid, by_phase in counts.items():
        spread = max(by_phase.values()) - min(by_phase.values())
        assert spread <= 2, f"{tid} phase spread {spread}"


def test_transformer_ratings_come_from_config_not_the_json():
    cfg = Config()
    for t in FEED.transformers():
        assert t.rating_kva == cfg.rating_kva[t.transformer_id]
        assert t.rated_life_hours > 0 and t.replacement_cost_inr > 0


def test_ticks_are_gross_and_non_negative():
    for b in (0, 12, 19, 719):
        ticks = FEED.ticks(b)
        assert len(ticks) == len(FEED.houses())
        assert all(t.load_kwh >= 0 and t.gen_kwh >= 0 for t in ticks)
        assert all(t.block == b for t in ticks)
    noon = {t.house_id: t for t in FEED.ticks(12)}
    # a solar premises generates AND consumes at noon — consumption is gross
    assert noon["10006"].gen_kwh > 0 and noon["10006"].load_kwh > 0


def test_thirty_days_are_not_identical():
    """The measured day is replayed, but scaled — otherwise the 30-day ageing
    comparison would be one day multiplied by thirty."""
    totals = {d: sum(t.gen_kwh for t in FEED.ticks(d * 24 + 12)) for d in range(30)}
    assert len(set(round(v, 3) for v in totals.values())) > 10


def test_no_generation_at_night():
    for b in (0, 2, 23, 24 * 7 + 3):
        assert all(t.gen_kwh == 0 for t in FEED.ticks(b))


def test_breaches_are_reachable_and_not_constant():
    """The point of the rating override: without it nothing ever breaches and
    the sentinel, flow agent and ageing signal are all dead code."""
    houses = {h.house_id: h for h in FEED.houses()}
    ratings = {t.transformer_id: t.rating_kva for t in FEED.transformers()}
    breaches = 0
    for b in range(FEED.total_blocks()):
        agg = defaultdict(float)
        for t in FEED.ticks(b):
            agg[houses[t.house_id].transformer_id] += abs(t.load_kwh - t.gen_kwh) / 0.95
        breaches += sum(1 for tid, kva in agg.items() if kva / ratings[tid] > 1.0)
    assert 60 <= breaches <= 250, f"{breaches} breach blocks in 720 — retune ratings"


def test_feed_is_deterministic():
    a = WhitefieldFeed(Config())
    b = WhitefieldFeed(Config())
    assert [t.__dict__ for t in a.ticks(42)] == [t.__dict__ for t in b.ticks(42)]
    assert ([h.__dict__ for h in a.houses()] == [h.__dict__ for h in b.houses()])


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
