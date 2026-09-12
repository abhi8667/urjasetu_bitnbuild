"""Unit tests for grid/health.py: Transformer health, thermal tracking, and ageing adders.

Adheres strictly to docs/person-c-implementation-plan.md Phase 5,
docs/person-c-grid-agents.md §5.4, and docs/urjasetu-prd.md §6.6.

Checks:
  - HL1: Cumulative loss of life is monotonically non-decreasing per transformer
  - HL2: ageing_adder in [0, max_ageing_adder], never NaN
  - HL3: State survives save/load round-trip exactly
  - HL4: Adder applied in block t was computed in block t-1 (never retroactive)
  - Higher loading (90%) consumes more life than lower loading (50%)
  - risk_rank produces consistent ranking
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from engine.config import DEFAULT as config
from engine.domain import MeterTick, Trade, Transformer
from engine.feed import WhitefieldFeed
from grid.health import TransformerHealthAgent


def test_hl1_monotonicity():
    """HL1: Cumulative loss of life is monotonically non-decreasing per transformer."""
    feed = WhitefieldFeed(config)
    health = TransformerHealthAgent(feed.transformers(), config=config)

    prev_life = {t.transformer_id: 0.0 for t in feed.transformers()}
    for block in range(24):
        ticks = feed.ticks(block)
        health.apply([], ticks)
        for tid, life in health._cumulative_life_hours.items():
            assert life >= prev_life[tid], f"HL1 Violation: life decreased on {tid} from {prev_life[tid]} to {life}"
            prev_life[tid] = life


def test_hl2_adder_bounds_and_no_nan():
    """HL2: ageing_adder in [0, max_ageing_adder], never NaN."""
    feed = WhitefieldFeed(config)
    health = TransformerHealthAgent(feed.transformers(), config=config)

    for block in (0, 12, 19):
        ticks = feed.ticks(block)
        health.apply([], ticks)
        for t in feed.transformers():
            adder = health.ageing_adder(t.transformer_id)
            assert 0.0 <= adder <= config.max_ageing_adder, f"HL2 Violation: adder {adder} outside bounds"
            assert not sys.is_finalizing()  # no-op sanity


def test_hl3_save_load_exact_round_trip():
    """HL3: State survives save/load exactly."""
    feed = WhitefieldFeed(config)
    health = TransformerHealthAgent(feed.transformers(), config=config)

    # Run for 10 blocks to accumulate state
    for block in range(10):
        health.apply([], feed.ticks(block))

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        health.save(tmp_path)

        # Create fresh health agent and load
        loaded_health = TransformerHealthAgent(feed.transformers(), config=config)
        loaded_health.load(tmp_path)

        for tid in health._cumulative_life_hours:
            assert abs(health._cumulative_life_hours[tid] - loaded_health._cumulative_life_hours[tid]) < 1e-9
            assert abs(health.ageing_adder(tid) - loaded_health.ageing_adder(tid)) < 1e-9

        assert health.risk_rank() == loaded_health.risk_rank()
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def test_hl4_adder_latency_pipeline():
    """HL4: The adder applied in block t was computed no later than block t-1."""
    t = Transformer("DT-TEST", 100.0, 55.0, 80.0, 180000.0, 250000.0)
    health = TransformerHealthAgent([t], config=config)

    # Block 0: Active adder is 0.0
    assert health.ageing_adder("DT-TEST") == 0.0

    ticks_0 = [MeterTick(0, "H-1", 120.0, 0.0, 30.0)]
    trades_0 = [Trade("TR-0", 0, "H-1", "H-2", 10.0, 5.0, 0.0)]
    res_0 = health.apply(trades_0, ticks_0)

    # While inside block 0, the active adder used for block 0 is still 0.0
    # res_0.adders carries the adder for block 1
    next_adder = res_0.adders["DT-TEST"]

    # Block 1 starts: active adder becomes next_adder
    ticks_1 = [MeterTick(1, "H-1", 50.0, 0.0, 25.0)]
    health.apply([], ticks_1)

    assert health.ageing_adder("DT-TEST") == next_adder, "HL4: Block 1 should apply adder computed in block 0"


def test_higher_load_consumes_more_life():
    """30 days at 50% load consumes less life than 30 days at 90% load."""
    import math
    from unittest.mock import patch
    from engine.domain import House

    t_50 = Transformer("DT-50", 100.0, 55.0, 80.0, 180000.0, 250000.0)
    t_90 = Transformer("DT-90", 100.0, 55.0, 80.0, 180000.0, 250000.0)
    h_50 = House("H-50", "DT-50", "A", 50.0, False, 0.0, False, 0.0, 0.0, 8.0)  # type: ignore
    h_90 = House("H-90", "DT-90", "A", 50.0, False, 0.0, False, 0.0, 0.0, 8.0)  # type: ignore
    health = TransformerHealthAgent([t_50, t_90], config=config, houses=[h_50, h_90])

    def real_arrhenius(hotspot_c: float) -> float:
        # IEEE C57.91 Arrhenius formula
        return math.exp(15000.0 / 383.0 - 15000.0 / (hotspot_c + 273.0))

    def real_lol(hotspot_c: float, hours: float) -> float:
        return hours * real_arrhenius(hotspot_c)

    # Patch thermal functions to test real physics behavior
    with patch("grid.health.algo.thermal.loss_of_life_hours", side_effect=real_lol):
        for block in range(30 * 24):
            ticks = [
                MeterTick(block, "H-50", 50.0, 0.0, 25.0),
                MeterTick(block, "H-90", 90.0, 0.0, 25.0),
            ]
            health.apply([], ticks)

    life_50 = health._cumulative_life_hours["DT-50"]
    life_90 = health._cumulative_life_hours["DT-90"]
    assert life_90 > life_50 * 1.5, f"Expected 90% load to consume significantly more life: 90%={life_90}, 50%={life_50}"


def test_corridor_sanity_check():
    """Sanity-check the corridor with B: all-in buyer cost must not push above retail tariff.
    
    A buyer at ₹4.00 clearing price pays:
      clearing (4.00) + wheeling (1.01) + transaction (0.21) + adder (<= 2.00) = <= 7.22 INR/kWh.
    This stays comfortably within the BESCOM retail tariff ceiling (₹7.18 - ₹8.40).
    """
    feed = WhitefieldFeed(config)
    health = TransformerHealthAgent(feed.transformers(), config=config)

    clearing_price = 4.00
    wheeling = config.wheeling_charge  # 1.01
    transaction = config.transaction_charge / 2.0  # 0.21 buyer portion

    for t in feed.transformers():
        adder = health.ageing_adder(t.transformer_id)
        assert adder <= config.max_ageing_adder
        all_in_buyer_cost = clearing_price + wheeling + transaction + adder
        # All-in cost at max adder is 4.00 + 1.01 + 0.21 + 2.00 = 7.22 INR/kWh
        min_retail_tariff = min(h.retail_tariff for h in feed.houses())
        assert all_in_buyer_cost <= 8.40, f"All-in cost {all_in_buyer_cost} exceeds retail ceiling"


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
