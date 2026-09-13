"""Configuration. Every default declared here, nothing hard-coded in modules.

Owner: B. Charges, limits and topology overrides are configuration values,
not literals in code (PRD §6.7).
"""
from __future__ import annotations

import os
from dataclasses import dataclass as _stdlib_dataclass, field, fields
from pathlib import Path

# PRD §8 asks for a pydantic model. DECISIONS.md D7 commits the engine to
# running on a clean machine with nothing installed. Both hold: pydantic's
# dataclass is a drop-in that keeps dataclasses.replace() and fields() working
# and adds validation, and when it is absent the stdlib dataclass takes over
# unchanged. Validation is a bonus when available, never a dependency.
try:
    from pydantic.dataclasses import dataclass as _dataclass
    PYDANTIC_AVAILABLE = True
except ImportError:                                     # pragma: no cover
    _dataclass = _stdlib_dataclass
    PYDANTIC_AVAILABLE = False

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:                                     # pragma: no cover
    yaml = None
    YAML_AVAILABLE = False

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


@_dataclass(frozen=True)
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

    # -- forecasting -------------------------------------------------------
    forecast_alpha: float = 0.3      # EWMA weight on the most recent same-hour day
    forecast_blend: float = 0.5      # own EWMA vs the feed's forecast
    forecast_history_days: int = 7

    # -- market ------------------------------------------------------------
    min_order_kwh: float = 0.10
    # KERC P2P Solar Energy Transaction Regulations 2024: energy cannot be
    # routed meter-to-meter across transformers, so each DT clears its own book.
    enforce_same_transformer: bool = True
    max_bid_kwh_per_block: float = 3.0   # keeps buyers competing for scarce surplus

    # -- grid limits -------------------------------------------------------
    loading_limit: float = 1.00
    phase_limit: float = 1.15
    voltage_band: float = 0.06
    derate_factor: float = 1.00          # demo control: 0.8 forces a breach
    # Displacement power factor of the aggregate LT load. A transformer is rated
    # in kVA, and the meters report kW, so EVERY module that compares the two has
    # to divide by this. It was a module-level literal in three files and absent
    # from a fourth (the sentinel), which put the sentinel's loading 5.3% below
    # the health agent's on the same street. One config field, one helper in
    # engine/physics.py, no literals — see DECISIONS.md D16.
    power_factor: float = 0.95

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
    # How the counterfactual credits exported surplus.
    #   "one_for_one" offsets kWh against consumption, worth the full retail
    #     tariff. This IS net metering, and it is the scheme the project argues
    #     against, so it is the default.
    #   "feed_in" pays the KERC rate the dataset records (Rs2.25/kWh), which is
    #     closer to gross metering.
    # The choice flips who wins: see DECISIONS.md D13. It is a pitch decision,
    # not a tuning knob.
    baseline_export_credit: str = "one_for_one"
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
    groq_primary_model: str = "qwen/qwen3.8-27b"
    groq_fallback_model: str = "openai/gpt-oss-120b"
    groq_timeout_seconds: float = 8.0
    risk_enabled: bool = True
    risk_horizon_blocks: int = 3
    risk_training_days: int = 14

    # -- feed --------------------------------------------------------------
    data_dir: Path = DATA_DIR
    forecast_noise_frac: float = 0.10    # 0.0 == perfect foresight, test only


#: Where load_config() looks when given no explicit path.
CONFIG_YAML = Path(__file__).resolve().parent.parent / "config.yaml"


_REPO_ROOT = Path(__file__).resolve().parent.parent


def _coerce(name: str, value):
    """YAML gives strings and lists where the model wants paths and tuples.

    A relative data_dir resolves against the repo root, so the shipped
    config.yaml stays portable — an absolute path baked in on one machine would
    fail on every other one.
    """
    if name == "data_dir":
        path = Path(value)
        return path if path.is_absolute() else (_REPO_ROOT / path)
    if name == "evening_blocks" and isinstance(value, list):
        return tuple(value)
    return value


def load_config(path: str | Path | None = None, **overrides) -> "Config":
    """Build a Config from config.yaml, falling back to the declared defaults.

    Invariant CF1: the engine starts and completes a full run with the shipped
    defaults and zero arguments. That holds in every combination — no yaml
    module, no config.yaml, an empty config.yaml, or a partial one. A file that
    is present but unreadable raises rather than silently running on defaults,
    because a config someone edited and expected to take effect is worse than
    no config at all.
    """
    values: dict = {}
    target = Path(path) if path is not None else CONFIG_YAML
    if path is not None and not target.exists():
        raise FileNotFoundError(f"config file not found: {target}")

    if YAML_AVAILABLE and target.exists():
        loaded = yaml.safe_load(target.read_text()) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"{target} must contain a mapping at the top level")
        known = {f.name for f in fields(Config)}
        unknown = set(loaded) - known
        if unknown:
            raise ValueError(
                f"{target} has keys the engine does not recognise: "
                f"{sorted(unknown)}. A typo in a config key would otherwise run "
                f"silently on the default.")
        values = {k: _coerce(k, v) for k, v in loaded.items()}

    values.update(overrides)
    config = Config(**values)
    _validate(config)
    return config


def _validate(config: "Config") -> None:
    """Range checks that hold with or without pydantic.

    pydantic validates types; nothing validates that a loading limit of -3 is
    nonsense. These are the bounds that would produce a silently wrong run
    rather than a crash.
    """
    problems = []
    if config.block_minutes <= 0 or 1440 % config.block_minutes:
        problems.append(f"block_minutes={config.block_minutes} must divide 1440")
    if config.days <= 0:
        problems.append(f"days={config.days} must be positive")
    if not 0 < config.loading_limit <= 2:
        problems.append(f"loading_limit={config.loading_limit} outside (0, 2]")
    if not 0 < config.derate_factor <= 1:
        problems.append(f"derate_factor={config.derate_factor} outside (0, 1]")
    if not 0 < config.round_trip_efficiency <= 1:
        problems.append(
            f"round_trip_efficiency={config.round_trip_efficiency} outside (0, 1]")
    if not 0 <= config.forecast_noise_frac < 1:
        problems.append(
            f"forecast_noise_frac={config.forecast_noise_frac} outside [0, 1)")
    if config.baseline_export_credit not in ("one_for_one", "feed_in"):
        problems.append(
            f"baseline_export_credit={config.baseline_export_credit!r} must be "
            f"'one_for_one' or 'feed_in'")
    if not config.rating_kva:
        problems.append("rating_kva is empty — no transformer would be rated")
    if config.groq_timeout_seconds <= 0:
        problems.append("groq_timeout_seconds must be positive")
    if config.risk_horizon_blocks <= 0:
        problems.append("risk_horizon_blocks must be positive")
    if config.risk_training_days <= 0:
        problems.append("risk_training_days must be positive")
    if problems:
        raise ValueError("invalid configuration: " + "; ".join(problems))


def to_yaml(config: "Config" = None) -> str:
    """Serialise a config as YAML, for regenerating the shipped config.yaml."""
    config = config or Config()
    out = {}
    for f in fields(config):
        value = getattr(config, f.name)
        if isinstance(value, Path):
            # Relative to the repo where possible, so the file works on any
            # machine rather than only the one that generated it.
            try:
                value = str(value.relative_to(_REPO_ROOT))
            except ValueError:
                value = str(value)
        elif isinstance(value, tuple):
            value = list(value)
        out[f.name] = value
    if not YAML_AVAILABLE:
        raise RuntimeError("PyYAML is required to serialise a config")
    return yaml.safe_dump(out, sort_keys=True, default_flow_style=False)


DEFAULT = Config()
