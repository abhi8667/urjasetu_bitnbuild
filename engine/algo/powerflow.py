"""Linearised LinDistFlow voltage deviation. Target hour 12."""
from __future__ import annotations

import math

from engine.config import DEFAULT as _DEFAULT_CONFIG

# Per-metre LV conductor constants for 415V/230V aluminium LV distribution
# cable. These were wrapped in a `_cfg_get` that read module attributes off
# engine.config — names that have never existed there — so every one of them
# always took its default and the "override in engine/config.py" the comment
# promised was not possible. They are plain module constants now, which is what
# they always were in fact, and the power factor (the one that genuinely has to
# agree with the rest of the system) comes from Config.
R_OHM_PER_M = 0.0003
X_OHM_PER_M = 0.0002
V_NOM_V = 230.0
POWER_FACTOR = _DEFAULT_CONFIG.power_factor


def _distance_m(topology, house_id: str) -> float:
    """Look up a house's electrical distance from its transformer.
    Accepts topology as either {house_id: House} (attribute access) or
    {house_id: {"distance_m": ...}} (dict access) — whichever your
    topology object turns out to be."""
    entry = topology[house_id]
    return entry.distance_m if hasattr(entry, "distance_m") else entry["distance_m"]


def voltage_dev(house_id: str, net_kw_by_house: dict[str, float], topology) -> float:
    """Per-unit voltage deviation from nominal at this house.

    Simplification per spec: each house is radially connected to its own
    transformer by a single segment of length distance_m — no shared
    multi-segment feeder tree. So "downstream" power on that one segment
    is just this house's own net_kw; nothing else flows through it.

    deviation = -(R_seg * P_w + X_seg * Q_w) / V_nom_v**2

    R_seg, X_seg = per-metre R, X * distance_m.
    P_w = net_kw_by_house[house_id] * 1000 (convert kW -> W for the ohm-based formula).
    Q_w approximated from P_w via a configured power factor.

    Sign convention: net_kw_by_house is net LOAD (import positive, export
    negative) — the same convention as MeterTick.load_kwh - gen_kwh. A net
    IMPORT draws current toward the house and sags voltage (negative
    deviation). A net EXPORT (excess solar pushed back upstream) raises
    voltage at that point (positive deviation) — this is the real, well-known
    "over-voltage from rooftop solar" effect utilities worry about. Hence
    the leading minus sign in front of an otherwise-positive R/X formula.
    """
    distance_m = _distance_m(topology, house_id)
    r_seg = R_OHM_PER_M * distance_m
    x_seg = X_OHM_PER_M * distance_m

    p_kw = net_kw_by_house.get(house_id, 0.0)
    p_w = p_kw * 1000.0
    q_w = p_w * math.tan(math.acos(POWER_FACTOR))

    return -(r_seg * p_w + x_seg * q_w) / (V_NOM_V ** 2)