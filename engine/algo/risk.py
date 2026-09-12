"""LightGBM transformer risk ranking. Cuttable — do not start before reshape_lp."""
from __future__ import annotations

from engine.domain import TransformerState


def rank(states: list[TransformerState]) -> list[tuple[str, float]]:
    """STUB: sort by life_used_frac descending. This is also the documented
    fallback when config.lightgbm_enabled is False, so the stub is shippable."""
    return sorted(((s.transformer_id, s.life_used_frac) for s in states),
                  key=lambda p: -p[1])
