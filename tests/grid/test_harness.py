"""Track C test harness and reusable fixtures.

Adheres strictly to docs/person-c-implementation-plan.md Phase 1.
Provides isolated fixtures matching reference topology:
  1. Balanced 3-phase case (hand-built; never breaches regardless of magnitude)
  2. Voltage violation case (remote premises capped at dataset max 187.3m, plus synthetic long feeder)
  3. Real overload block (ticks = feed.ticks(19), 19:00 on day 0)
  4. Battery fixture (8 houses, 10 kWh, +/-5 kW, varying SoC)
"""
from __future__ import annotations

from typing import Any
from engine.config import DEFAULT as config, Config
from engine.domain import House, MeterTick, Trade, Transformer
from engine.feed import WhitefieldFeed


def get_default_feed(cfg: Config = config) -> WhitefieldFeed:
    """Returns standard Whitefield feed."""
    return WhitefieldFeed(cfg)


def balanced_three_phase_fixture(
    feed: WhitefieldFeed,
    transformer_id: str = "DT-1",
    total_load_kwh_per_phase: float = 30.0,
    block: int = 12,
) -> tuple[list[Transformer], list[House], list[MeterTick], list[Trade]]:
    """Hand-built balanced three-phase fixture.
    
    Assigns symmetric loads to phases A, B, and C on the given transformer.
    Imbalance = max(P_A, P_B, P_C) / mean(P_A, P_B, P_C) == 1.0.
    Must never breach phase limit (default 1.15), regardless of magnitude.
    """
    transformers = [t for t in feed.transformers() if t.transformer_id == transformer_id]
    houses = [h for h in feed.houses() if h.transformer_id == transformer_id]
    
    by_phase: dict[str, list[House]] = {"A": [], "B": [], "C": []}
    for h in houses:
        by_phase[h.phase].append(h)
        
    ticks: list[MeterTick] = []
    for phase_name, phase_houses in by_phase.items():
        if not phase_houses:
            continue
        load_per_house = total_load_kwh_per_phase / len(phase_houses)
        for h in phase_houses:
            ticks.append(
                MeterTick(
                    block=block,
                    house_id=h.house_id,
                    load_kwh=load_per_house,
                    gen_kwh=0.0,
                    ambient_c=25.0,
                )
            )
            
    trades: list[Trade] = []  # No trades needed for base balanced check
    return transformers, houses, ticks, trades


def voltage_violation_fixture(
    feed: WhitefieldFeed,
    transformer_id: str = "DT-1",
    block: int = 12,
    synthetic_long_distance: bool = False,
) -> tuple[list[Transformer], list[House], list[MeterTick], list[Trade]]:
    """Voltage violation fixture.
    
    Remote premises at feeder's far end. In dataset, maximum distance_m is 187.3m.
    If synthetic_long_distance is True, distance is set to 350m to test extreme
    drop scenarios, explicitly labeled as synthetic.
    """
    transformers = [t for t in feed.transformers() if t.transformer_id == transformer_id]
    houses_raw = [h for h in feed.houses() if h.transformer_id == transformer_id]
    
    # Identify the most remote premises
    houses: list[House] = []
    max_dist_house = max(houses_raw, key=lambda h: h.distance_m)
    
    for h in houses_raw:
        if synthetic_long_distance and h.house_id == max_dist_house.house_id:
            # Explicitly synthetic extension
            modified_house = House(
                house_id=h.house_id,
                transformer_id=h.transformer_id,
                phase=h.phase,
                distance_m=350.0,  # Synthetic
                has_pv=h.has_pv,
                pv_kw=h.pv_kw,
                has_battery=h.has_battery,
                battery_kwh=h.battery_kwh,
                battery_max_kw=h.battery_max_kw,
                retail_tariff=h.retail_tariff,
            )
            houses.append(modified_house)
        else:
            houses.append(h)

    # Put heavy net load/export on the remote premises
    ticks: list[MeterTick] = []
    for h in houses:
        if h.house_id == max_dist_house.house_id:
            # Extreme load to induce voltage drop
            ticks.append(
                MeterTick(
                    block=block,
                    house_id=h.house_id,
                    load_kwh=35.0,
                    gen_kwh=0.0,
                    ambient_c=25.0,
                )
            )
        else:
            ticks.append(
                MeterTick(
                    block=block,
                    house_id=h.house_id,
                    load_kwh=1.0,
                    gen_kwh=0.0,
                    ambient_c=25.0,
                )
            )
            
    trades: list[Trade] = []
    return transformers, houses, ticks, trades


def real_overload_block_fixture(
    feed: WhitefieldFeed,
    block: int = 19,
) -> tuple[list[Transformer], list[House], list[MeterTick], list[Trade]]:
    """Real overload block pulled directly from feed (Day 0, 19:00).
    
    DT-1 (125 kVA) and DT-3 (63 kVA) exceed 1.00 loading limit.
    DT-2 (63 kVA) reaches K ≈ 0.78-0.95 and does NOT breach (healthy control).
    """
    transformers = feed.transformers()
    houses = feed.houses()
    ticks = feed.ticks(block)
    trades: list[Trade] = []
    return transformers, houses, ticks, trades


def battery_fixture(
    feed: WhitefieldFeed,
) -> dict[str, dict[str, float]]:
    """Battery fixture for the 8 houses with home batteries.
    
    Returns house_id -> {'capacity_kwh': 10.0, 'max_kw': 5.0, 'soc_kwh': initial_soc}
    """
    battery_houses = [h for h in feed.houses() if h.has_battery]
    states: dict[str, dict[str, float]] = {}
    soc_levels = [2.0, 3.5, 5.0, 6.5, 8.0, 1.5, 4.0, 7.0]
    
    for i, h in enumerate(battery_houses):
        soc = soc_levels[i % len(soc_levels)]
        states[h.house_id] = {
            "capacity_kwh": h.battery_kwh,
            "max_kw": h.battery_max_kw,
            "soc_kwh": soc,
            "headroom_kwh": h.battery_kwh - soc,
        }
    return states
