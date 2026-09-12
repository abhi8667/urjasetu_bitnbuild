"""IEEE C57.91 hot-spot temperature and insulation ageing. Target hour 16."""
from __future__ import annotations

from engine.domain import ThermalParams

DEFAULT_PARAMS = ThermalParams()


def hotspot_c(load_kva: float, rating_kva: float, ambient_c: float,
              params: ThermalParams = DEFAULT_PARAMS) -> float:
    """STUB: ambient + 40 * (load / rating).

    Real implementation: C57.91 clause 7 top-oil rise and winding gradient.
    """
    if rating_kva <= 0:
        return ambient_c
    return ambient_c + 40.0 * (load_kva / rating_kva)


def ageing_factor(hotspot_c: float) -> float:
    """STUB: 1.0. Real: F_AA = exp(15000/383 - 15000/(hotspot + 273))."""
    return 1.0


def loss_of_life_hours(hotspot_c: float, hours: float) -> float:
    """STUB: hours * ageing_factor(hotspot_c)."""
    return hours * ageing_factor(hotspot_c)
