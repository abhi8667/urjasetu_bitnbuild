"""Contract 3 — domain dataclasses. Published hour 2, frozen thereafter.

Owner: B. Nobody edits this file without announcing it; C and D build against it.
Every dataclass here is frozen: state changes create a new instance, which is
what makes the tick loop debuggable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


class InvariantError(AssertionError):
    """Raised when an invariant is violated. Never caught, never downgraded to a
    warning — the assertion IS the bug report."""


# ---------------------------------------------------------------- entities

@dataclass(frozen=True)
class House:
    house_id: str
    transformer_id: str
    phase: Literal["A", "B", "C"]
    distance_m: float          # electrical distance from transformer, for voltage calc
    has_pv: bool
    pv_kw: float               # 0.0 if has_pv is False
    has_battery: bool
    battery_kwh: float         # usable capacity, 0.0 if has_battery is False
    battery_max_kw: float      # charge/discharge power limit
    retail_tariff: float       # INR/kWh, the household's DISCOM slab rate


@dataclass(frozen=True)
class Transformer:
    transformer_id: str
    rating_kva: float
    rated_top_oil_rise_c: float
    rated_hotspot_rise_c: float
    rated_life_hours: float
    replacement_cost_inr: float


@dataclass(frozen=True)
class MeterTick:
    block: int
    house_id: str
    load_kwh: float            # gross consumption this block, >= 0
    gen_kwh: float             # gross PV generation this block, >= 0
    ambient_c: float


@dataclass(frozen=True)
class Order:
    order_id: str
    block: int
    house_id: str
    side: Literal["offer", "bid"]
    quantity_kwh: float        # > 0
    limit_price: float         # offer: floor. bid: ceiling.


@dataclass(frozen=True)
class Trade:
    trade_id: str
    block: int
    seller_id: str
    buyer_id: str
    quantity_kwh: float
    clearing_price: float
    curtailed_fraction: float  # 0.0 if untouched by flow agent


@dataclass(frozen=True)
class BillLine:
    """One party's side of one trade, itemised. Never aggregate at source.

    `net_inr` is signed from the party's point of view: positive means the party
    pays, negative means it receives. ST1 falls out of that convention — energy
    cancels between buyer and seller, so the sum of every party's net position
    equals the charges collected by the DISCOM.

    The storage fee is the exception: it moves between two participants, so it
    nets to zero across the ledger and is NOT a collected charge.
    """
    line_id: str
    block: int
    trade_id: str
    house_id: str
    role: Literal["buyer", "seller", "owner", "custodian"]
    quantity_kwh: float
    unit_price_inr: float      # the clearing price this line settled at
    energy_inr: float
    transaction_inr: float
    wheeling_inr: float
    cross_subsidy_inr: float
    storage_fee_inr: float
    ageing_inr: float
    net_inr: float

    @property
    def components(self) -> tuple[float, ...]:
        return (self.energy_inr, self.transaction_inr, self.wheeling_inr,
                self.cross_subsidy_inr, self.storage_fee_inr, self.ageing_inr)


@dataclass(frozen=True)
class StorageClaim:
    claim_id: str
    owner_id: str              # who owns the energy
    custodian_id: str          # whose battery holds it
    quantity_kwh: float        # remaining, after round-trip loss
    cost_basis: float          # INR/kWh the owner effectively paid
    opened_block: int


# ------------------------------------------------------------ result types
# Returned by D's algorithms and C's agents. Shapes frozen with Contract 1.

@dataclass(frozen=True)
class ClearingResult:
    trades: list[Trade]
    clearing_price: float | None
    unmatched_offers: list[Order]
    unmatched_bids: list[Order]


@dataclass(frozen=True)
class Breach:
    transformer_id: str
    kind: Literal["loading", "phase", "voltage"]
    severity: float            # 1.0 == exactly at limit
    detail: dict


@dataclass(frozen=True)
class ReshapeSolution:
    retention: dict[str, float]       # trade_id -> c_t, in [0, 1]
    battery_charge: dict[str, float]  # house_id -> kWh absorbed this block
    feasible: bool
    objective_value: float


@dataclass(frozen=True)
class ReshapePlan:
    constrained_orders: list[Order]
    battery_charges: dict[str, float]  # house_id -> kWh to absorb this block
    new_claims: list[StorageClaim]
    feasible: bool
    objective_value: float


@dataclass(frozen=True)
class TransformerState:
    transformer_id: str
    life_used_frac: float
    hotspot_c: float
    loading_k: float
    ageing_adder: float


@dataclass(frozen=True)
class AgeingResult:
    """Returned by C's TransformerHealthAgent.apply() for one block.

    There is exactly one of these, here, so that B and C cannot drift apart:
    `grid/health.py` imports it rather than defining its own. C constructs it
    from `states` and `adders`; B reads the dict views below. `adders` is
    already lagged by one block (HL4) — settlement must not lag it again.
    """
    states: list[TransformerState]
    adders: dict[str, float]          # transformer_id -> INR/kWh for block t+1
    block: int = 0

    @property
    def ageing_adder(self) -> dict[str, float]:
        return dict(self.adders)

    @property
    def hotspot_c(self) -> dict[str, float]:
        return {s.transformer_id: s.hotspot_c for s in self.states}

    @property
    def life_used_frac(self) -> dict[str, float]:
        return {s.transformer_id: s.life_used_frac for s in self.states}

    @property
    def loading_k(self) -> dict[str, float]:
        return {s.transformer_id: s.loading_k for s in self.states}


@dataclass(frozen=True)
class StrategyParams:
    """Set once per simulated day by the LLM layer; these are the LLM-disabled
    defaults, and the engine must run correctly on them (PRD integration check 10)."""
    discount: float = 0.85
    margin: float = 0.10            # consumer bids retail_tariff * (1 - margin)
    battery_reserve_frac: float = 0.20
    bid_aggression: float = 1.00


@dataclass(frozen=True)
class Site:
    """Static presentation and settlement metadata that sits beside a House.

    NOT part of Contract 3 — `House` stays exactly as frozen at hour 2. These
    are registry fields the engine needs but no agent reasons over: real
    coordinates for A's isometric layout, building type for its sprites, and the
    per-premises transmission loss that settlement and the FL4 energy-conservation
    check both need. Looked up by house_id, never re-sent with a reading.
    """
    house_id: str
    transformer_id: str
    lat: float
    lon: float
    building_type: Literal["res", "apt", "com", "evhub"]
    transmission_loss_pct: float


@dataclass(frozen=True)
class TransformerSite:
    transformer_id: str
    name: str
    lat: float
    lon: float
    registry_kva: float        # as installed, per transformer_registry.json
    feeder_id: str


@dataclass(frozen=True)
class ThermalParams:
    """IEEE C57.91 parameters. Defaults are the standard's oil-immersed
    distribution-transformer values — see DECISIONS.md D6."""
    rated_top_oil_rise_c: float = 55.0
    rated_hotspot_rise_c: float = 80.0     # TOTAL rise over ambient at rated load
    oil_resistance_ratio_R: float = 5.0    # ONAN distribution units
    oil_exponent_n: float = 0.8
    winding_exponent_m: float = 0.8
    tau_oil_hours: float = 3.0

    @property
    def hotspot_gradient_c(self) -> float:
        """Winding-to-oil gradient at rated load.

        `rated_hotspot_rise_c` is the TOTAL hot-spot rise over ambient (80 C),
        which already contains the top-oil rise (55 C). Adding both terms to
        ambient double-counts the oil and puts the hot spot at 165 C at rated
        load, where IEEE C57.91 calibrates F_AA = 1.0 at 110 C. The gradient is
        the difference, 25 C, and using it reproduces the standard's reference
        point exactly. See the PRD §6.6 acceptance check.
        """
        return self.rated_hotspot_rise_c - self.rated_top_oil_rise_c
