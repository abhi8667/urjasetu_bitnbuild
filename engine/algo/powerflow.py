"""Linearised LinDistFlow voltage deviation. Target hour 12."""
from __future__ import annotations


def voltage_dev(house_id: str, net_kw_by_house: dict[str, float], topology) -> float:
    """STUB: 0.0 — no house ever violates the band.

    Real implementation: per-unit deviation at house_id from the linearised
    LinDistFlow approximation over the radial feeder in topology.
    """
    return 0.0
