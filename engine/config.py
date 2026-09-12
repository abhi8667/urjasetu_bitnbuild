"""Configuration. Every default declared here, nothing hard-coded in modules.

Owner: B. Charges, limits and topology overrides are configuration values,
not literals in code (PRD §6.7).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _rating_kva() -> dict[str, float]:
    """Transformer ratings used by the engine, overriding transformer_registry.json.

    The locked dataset rates these DTs at 250-500 kVA, which puts measured peak
    loading at 21-29%. No breach is reachable at any defensible loading_limit,
    so the sentinel, the flow agent and the ageing signal would never fire —
    that is the whole project.

    These are standard Indian LT distribution sizings for the connection count
    on each DT (12-17 premises). Measured against them over a 30-day run, peak
    loading reaches 127-132% and breaches land in the 18:00-21:00 evening peak
    on every simulated day, 1-3 transformers at a time. DT-2 never breaches,
    which gives the compare screen a healthy control. See DECISIONS.md D1.
    """
    return {"DT-1": 125.0, "DT-2": 63.0, "DT-3": 63.0, "DT-4": 63.0}


@dataclass(frozen=True)
class Config:
    # -- determinism -------------------------------------------------------
    seed: int = 20250912

    # -- time base ---------------------------------------------------------
    # The locked telemetry is hourly, so a block is an hour and a day is 24
    # blocks, not the 96 the PRD assumed. Anything expressed per-block scales
    # off block_hours — never off a literal 0.25. See DECISIONS.md D2.
    block_minutes: int = 60
    days: int = 30
    evening_blocks: tuple[int, int] = (17, 21)   # was blocks 68-84 at 15 min

    @property
    def block_hours(self) -> float:
        return self.block_minutes / 60.0

    @property
    def blocks_per_day(self) -> int:
        return int(24 * 60 / self.block_minutes)

    # -- topology ----------------------------------------------------------
    rating_kva: dict[str, float] = field(default_factory=_rating_kva)
    include_ev_hubs_as_houses: bool = True  # the 4 shared hubs load their DT

    # -- market ------------------------------------------------------------
    min_order_kwh: float = 0.10
    max_bid_kwh_per_block: float = 3.0   # keeps buyers competing for scarce surplus

    # -- grid limits -------------------------------------------------------
    loading_limit: float = 1.00
    phase_limit: float = 1.15
    voltage_band: float = 0.06
    derate_factor: float = 1.00          # demo control: 0.8 forces a breach

    # -- batteries ---------------------------------------------------------
    round_trip_efficiency: float = 0.90
    storage_fee_inr_per_kwh: float = 0.35
    battery_max_kw: float = 5.0          # dataset shows +/-5 kW, not the README's +/-11

    # -- charges (KERC, from bangalore_reference_constants.json) -----------
    wheeling_charge: float = 1.01
    transaction_charge: float = 0.42
    platform_fee: float = 0.25
    gst_pct: float = 5.0
    feed_in_tariff: float = 2.25
    cross_subsidy: float = 0.0
    cross_subsidy_enabled: bool = False
    credit_carryforward_blocks: int = 8760   # 12 months of hourly blocks

    # -- ageing ------------------------------------------------------------
    max_ageing_adder: float = 2.00
    rated_life_hours: float = 180_000.0
    replacement_cost_inr: float = 250_000.0

    # -- optional layers ---------------------------------------------------
    lightgbm_enabled: bool = False
    llm_enabled: bool = False

    # -- feed --------------------------------------------------------------
    data_dir: Path = DATA_DIR
    forecast_noise_frac: float = 0.10    # 0.0 == perfect foresight, test only


DEFAULT = Config()
