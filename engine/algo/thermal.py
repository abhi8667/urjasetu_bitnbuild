"""IEEE C57.91 hot-spot temperature and insulation ageing. Target hour 16."""
from __future__ import annotations

import math

from engine.domain import ThermalParams

try:
    from engine import config as _cfg
except ImportError:
    _cfg = None


def _cfg_get(name, default):
    return getattr(_cfg, name, default) if _cfg is not None else default


# R (load-loss / no-load-loss ratio) is not a field on ThermalParams in
# domain.py — kept here as a plain configurable constant, the same pattern
# as R_OHM_PER_M / X_OHM_PER_M / V_NOM_V / POWER_FACTOR in powerflow.py.
# Override via engine.config.THERMAL_R; defaults to the IEEE C57.91 typical
# distribution-transformer value.
THERMAL_R = _cfg_get("THERMAL_R", 5.0)

DEFAULT_PARAMS = ThermalParams()


def hotspot_c(load_kva: float, rating_kva: float, ambient_c: float,
              params: ThermalParams = DEFAULT_PARAMS) -> float:
    """IEEE C57.91 clause 7: top-oil rise + winding hot-spot gradient.

    K   = load_kva / rating_kva
    dTO = rated_top_oil_rise_c * ((K**2 * THERMAL_R + 1) / (THERMAL_R + 1)) ** oil_exponent_n
    dH  = rated_hotspot_rise_c * K ** (2 * winding_exponent_m)
    hotspot_c = ambient_c + dTO + dH

    rated_top_oil_rise_c, rated_hotspot_rise_c, oil_exponent_n, and
    winding_exponent_m all come from the real domain.py ThermalParams as-is
    (including its rated_hotspot_rise_c = 80.0 default — not the 25.0 the
    written spec mentioned; domain.py is the source of truth). THERMAL_R is
    the one physical constant ThermalParams doesn't carry, so it's a module
    constant here instead, same treatment as powerflow.py's R/X/V_nom.

    Zero or negative rating returns ambient unchanged rather than raising —
    matches the "never raises for bad domain input" rule.
    """
    if rating_kva <= 0:
        return ambient_c

    K = load_kva / rating_kva
    d_top_oil = params.rated_top_oil_rise_c * ((K**2 * THERMAL_R + 1) / (THERMAL_R + 1)) ** params.oil_exponent_n
    d_hotspot = params.rated_hotspot_rise_c * K ** (2 * params.winding_exponent_m)
    return ambient_c + d_top_oil + d_hotspot


def ageing_factor(hotspot_c: float) -> float:
    """Arrhenius ageing acceleration factor.

    1.0 at 110°C reference for 65°C-rise insulation (the IEEE C57.91
    reference point). Purely algebraic — no iteration, no ODE.
    """
    return math.exp(15000 / 383 - 15000 / (hotspot_c + 273))


def loss_of_life_hours(hotspot_c: float, block_hours: float = 0.25) -> float:
    """Insulation life consumed during one block at this hot-spot temperature."""
    return ageing_factor(hotspot_c) * block_hours