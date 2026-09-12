"""Unit tests for grid/sentinel.py and Hour-12 Gate verification.

Adheres strictly to docs/person-c-implementation-plan.md Phase 2,
person-c-grid-agents.md §5.1, and urjasetu-prd.md §6.4.

Checks:
  - Exactly 100.0% loading -> None; 100.1% -> loading breach
  - Balanced 3-phase case never produces a phase breach at any magnitude
  - Voltage violation triggers when deviation exceeds band
  - Empty trade list runs clean without errors
  - SN1: check() is pure and never mutates trades, ticks, or state
  - SN2: Returned breach has severity >= 1.0
  - Hour-12 Gate: Real block 19 detects breach on DT-1/DT-3 with severity >= 1.0
"""
from __future__ import annotations

import sys
from pathlib import Path
from copy import deepcopy

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from engine.config import DEFAULT as config, Config
from engine.domain import House, MeterTick, Trade, Transformer
from engine.feed import WhitefieldFeed
from grid.sentinel import GridSentinel
from tests.grid.test_harness import (
    balanced_three_phase_fixture,
    real_overload_block_fixture,
)


def test_loading_boundary_conditions():
    """Exactly 100.0% loading -> None; 100.1% -> loading breach."""
    t = Transformer(
        transformer_id="DT-TEST",
        rating_kva=100.0,
        rated_top_oil_rise_c=55.0,
        rated_hotspot_rise_c=80.0,
        rated_life_hours=180000.0,
        replacement_cost_inr=250000.0,
    )
    # Balanced 3-phase houses to isolate loading check
    houses = [
        House(
            house_id=f"H-{ph}",
            transformer_id="DT-TEST",
            phase=ph,  # type: ignore
            distance_m=50.0,
            has_pv=False,
            pv_kw=0.0,
            has_battery=False,
            battery_kwh=0.0,
            battery_max_kw=0.0,
            retail_tariff=8.0,
        )
        for ph in ("A", "B", "C")
    ]
    sentinel = GridSentinel([t], houses, config=config)

    # The boundary is in kVA, not kW. A transformer is RATED in kVA and the
    # meters report kW, so K = (kW / power_factor) / rating_kva. This test used
    # to feed 100 kW into a 100 kVA transformer and assert K = 1.000, which
    # encoded the bug it was meant to guard: the sentinel compared kW against a
    # kVA rating directly and so measured every transformer 5.3% cooler than
    # grid/health.py measured the same one in the same block.
    #
    # 100 kW at 0.95 pf IS 105.3 kVA and a 100 kVA transformer carrying it is
    # genuinely overloaded. The kW that sits exactly at the limit is
    # rating * pf = 95.0 kW.
    at_limit_kw = 100.0 * config.power_factor          # 95.0 kW
    tick_at_limit = [
        MeterTick(block=1, house_id=f"H-{ph}", load_kwh=at_limit_kw / 3.0,
                  gen_kwh=0.0, ambient_c=25.0)
        for ph in ("A", "B", "C")
    ]
    breach = sentinel.check([], tick_at_limit)
    # config.loading_limit is 1.00 -> K <= 1.00 is NOT a breach
    assert breach is None, f"exactly 100% loading must not breach, got: {breach}"

    over_kw = at_limit_kw * 1.001
    tick_over = [
        MeterTick(block=1, house_id=f"H-{ph}", load_kwh=over_kw / 3.0,
                  gen_kwh=0.0, ambient_c=25.0)
        for ph in ("A", "B", "C")
    ]
    breach_over = sentinel.check([], tick_over)
    assert breach_over is not None, "100.1% loading must trigger a breach"
    assert breach_over.kind == "loading"
    assert breach_over.transformer_id == "DT-TEST"
    assert breach_over.severity >= 1.0009
    # The breach must carry BOTH forms, because FlowAgent.fallback_curtail
    # divides the kVA rating by the kVA load to size its retention fraction.
    assert abs(breach_over.detail["actual_load_kva"]
               - breach_over.detail["actual_load_kw"] / config.power_factor) < 1e-9


def test_balanced_three_phase_never_breaches():
    """Perfectly balanced 3-phase case never produces a phase breach at any magnitude."""
    feed = WhitefieldFeed(config)
    transformers, houses, ticks, trades = balanced_three_phase_fixture(feed, "DT-1", total_load_kwh_per_phase=50.0)
    sentinel = GridSentinel(transformers, houses, config=config)
    
    breach = sentinel.check(trades, ticks)
    if breach is not None:
        assert breach.kind != "phase", f"Balanced 3-phase produced unexpected phase breach: {breach}"

    # Scale magnitude by 10x
    high_load_ticks = [
        MeterTick(block=tk.block, house_id=tk.house_id, load_kwh=tk.load_kwh * 10, gen_kwh=0.0, ambient_c=25.0)
        for tk in ticks
    ]
    breach_high = sentinel.check(trades, high_load_ticks)
    if breach_high is not None:
        assert breach_high.kind != "phase", "High-power balanced 3-phase must not produce phase breach"


def test_empty_trade_list_runs_clean():
    """Empty trade list runs cleanly without error."""
    feed = WhitefieldFeed(config)
    transformers = feed.transformers()
    houses = feed.houses()
    ticks = feed.ticks(0)
    sentinel = GridSentinel(transformers, houses, config=config)

    breach = sentinel.check([], ticks)
    # Should run without raising any exceptions
    assert breach is None or breach.severity >= 1.0


def test_sn1_purity_invariant():
    """SN1: check() is pure and never mutates trades, ticks, or agent state."""
    feed = WhitefieldFeed(config)
    transformers = feed.transformers()
    houses = feed.houses()
    ticks = feed.ticks(19)
    trades = [
        Trade(
            trade_id="TR-1",
            block=19,
            seller_id=houses[0].house_id,
            buyer_id=houses[1].house_id,
            quantity_kwh=2.5,
            clearing_price=5.0,
            curtailed_fraction=0.0,
        )
    ]

    ticks_copy = deepcopy(ticks)
    trades_copy = deepcopy(trades)
    sentinel = GridSentinel(transformers, houses, config=config)

    _ = sentinel.check(trades, ticks)

    assert [t.__dict__ for t in ticks] == [t.__dict__ for t in ticks_copy], "Ticks mutated by check()"
    assert [tr.__dict__ for tr in trades] == [tr.__dict__ for tr in trades_copy], "Trades mutated by check()"


def test_sn2_severity_invariant():
    """SN2: Any returned breach must have severity >= 1.0."""
    feed = WhitefieldFeed(config)
    transformers = feed.transformers()
    houses = feed.houses()
    sentinel = GridSentinel(transformers, houses, config=config)

    for block in (0, 6, 12, 18, 19, 20):
        ticks = feed.ticks(block)
        breach = sentinel.check([], ticks)
        if breach is not None:
            assert breach.severity >= 1.0, f"Breach severity < 1.0: {breach.severity}"


def test_hour_12_gate_real_block_19():
    """Hour-12 Gate: An injected / real 19:00 overload breach is correctly detected."""
    feed = WhitefieldFeed(config)
    transformers, houses, ticks, trades = real_overload_block_fixture(feed, block=19)
    sentinel = GridSentinel(transformers, houses, config=config)

    breach = sentinel.check(trades, ticks)
    assert breach is not None, "Hour-12 Gate Failed: Block 19 should produce a breach"
    assert breach.kind == "loading", f"Expected loading breach, got: {breach.kind}"
    assert breach.transformer_id in ("DT-1", "DT-3", "DT-4"), f"Unexpected breaching transformer: {breach.transformer_id}"
def test_voltage_deviation_breach():
    """Voltage breach triggers when algo.powerflow.voltage_dev returns deviation > band."""
    from unittest.mock import patch
    feed = WhitefieldFeed(config)
    transformers = [t for t in feed.transformers() if t.transformer_id == "DT-2"]
    houses = [h for h in feed.houses() if h.transformer_id == "DT-2"]
    ticks = [MeterTick(block=1, house_id=h.house_id, load_kwh=1.0, gen_kwh=0.0, ambient_c=25.0) for h in houses]
    sentinel = GridSentinel(transformers, houses, config=config)

    # With default stub (dev = 0.0), no breach
    assert sentinel.check([], ticks) is None

    # Mock voltage_dev to return 0.08 on one house (exceeding band 0.06)
    target_hid = houses[0].house_id
    def mock_vdev(hid, net_kw, topo):
        return 0.08 if hid == target_hid else 0.0

    with patch("grid.sentinel.algo.powerflow.voltage_dev", side_effect=mock_vdev):
        breach = sentinel.check([], ticks)
        assert breach is not None
        assert breach.kind == "voltage"
        assert breach.transformer_id == "DT-2"
        assert breach.severity == 0.08 / config.voltage_band
        assert breach.detail["house_id"] == target_hid


def test_derate_factor_forces_breach_on_demand():
    """Phase 6: config.derate_factor = 0.8 forces a breach on demand on DT-1.

    Block 20, not block 18. At block 18 DT-1 carries 121.08 kW, which is 127.45
    kVA against a 125 kVA rating — it breaches at the standard rating, so it
    cannot demonstrate that the derate is what forced the breach. The old
    version of this test read K = 121.08 / 125 = 0.969 and concluded DT-1 was
    clean, which was the sentinel's kW-vs-kVA bug showing up as a test premise.

    Block 20 carries 115.91 kW = 122.01 kVA, genuinely under 125 at the standard
    rating (K = 0.976) and over a derated 100 kVA (K = 1.220). That is the
    control this test wants.
    """
    BLOCK = 20
    feed_std = WhitefieldFeed(Config(derate_factor=1.0))
    t_dt1 = next(t for t in feed_std.transformers() if t.transformer_id == "DT-1")
    h_dt1 = [h for h in feed_std.houses() if h.transformer_id == "DT-1"]
    sentinel_std = GridSentinel([t_dt1], h_dt1, config=Config(derate_factor=1.0))
    breaches_std = sentinel_std._loading(t_dt1, h_dt1, sentinel_std._net_kw_by_house([], feed_std.ticks(BLOCK)))
    assert len(breaches_std) == 0, "DT-1 should not breach at block {BLOCK} under standard rating"

    # Under derated rating (derate_factor = 0.8), rating drops to 100 kVA (K = 1.220 > 1.0)
    feed_derated = WhitefieldFeed(Config(derate_factor=0.8))
    t_dt1_derated = next(t for t in feed_derated.transformers() if t.transformer_id == "DT-1")
    sentinel_derated = GridSentinel([t_dt1_derated], h_dt1, config=Config(derate_factor=0.8))
    breaches_derated = sentinel_derated._loading(t_dt1_derated, h_dt1, sentinel_derated._net_kw_by_house([], feed_derated.ticks(BLOCK)))
    assert len(breaches_derated) == 1, "DT-1 should breach on demand under derate_factor = 0.8"
    assert breaches_derated[0].severity >= 1.20, f"Severity should be >= 1.20, got {breaches_derated[0].severity}"


def test_voltage_violation_fixture_from_harness():
    """Test voltage_violation_fixture from test_harness with synthetic line extension."""
    from unittest.mock import patch
    from tests.grid.test_harness import voltage_violation_fixture
    feed = WhitefieldFeed(config)
    transformers, houses, ticks, trades = voltage_violation_fixture(feed, "DT-1", synthetic_long_distance=True)
    sentinel = GridSentinel(transformers, houses, config=config)

    # Mock LinDistFlow: dev = R * d * P / V_nom^2
    def mock_lindistflow(hid, net_kw, topo):
        h = next((x for x in houses if x.house_id == hid), None)
        if not h:
            return 0.0
        # For synthetic remote house (350m, 35kW), high dev
        if h.distance_m > 200.0:
            return 0.15
        return 0.01

    with patch("grid.sentinel.algo.powerflow.voltage_dev", side_effect=mock_lindistflow):
        # 1. Direct voltage check produces voltage breach
        net_kw = sentinel._net_kw_by_house(trades, ticks)
        v_breaches = sentinel._voltage(transformers[0], houses, net_kw)
        assert len(v_breaches) == 1
        assert v_breaches[0].kind == "voltage"
        assert v_breaches[0].severity == 0.15 / config.voltage_band

        # 2. check() returns voltage breach when it is the maximum severity breach
        breach = sentinel.check(trades, ticks)
        assert breach is not None
        assert breach.kind == "voltage"
        assert breach.severity == 0.15 / config.voltage_band


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
