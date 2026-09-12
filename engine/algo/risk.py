"""LightGBM transformer risk ranking. Cuttable — do not start before reshape_lp."""
from __future__ import annotations

import random

from engine.domain import TransformerState

try:
    from engine import config as _cfg
except ImportError:
    _cfg = None


def _cfg_get(name, default):
    return getattr(_cfg, name, default) if _cfg is not None else default


LIGHTGBM_ENABLED = _cfg_get("lightgbm_enabled", False)

FEATURE_NAMES = [
    "loading_mean", "loading_max",
    "hotspot_mean", "hotspot_max",
    "life_used_frac",
    "voltage_excursions",
    "ambient_percentile",
]

_model = None  # lazily trained on first real use


def _stub_rank(states: list[TransformerState]) -> list[tuple[str, float]]:
    """Sort by life_used_frac descending. This is ALSO the documented
    fallback when lightgbm_enabled is False, or when the model path fails
    for any reason — so this one function must always work standalone."""
    return sorted(((s.transformer_id, s.life_used_frac) for s in states),
                  key=lambda p: -p[1])


def _extract_features(state: TransformerState, history: list | None = None) -> list[float]:
    """Build the named feature vector for one transformer.

    TransformerState (per domain.py) is a single current snapshot — no
    history, no voltage field, no ambient field. Three of the six named
    features genuinely need a trailing window this pure function has no
    memory to hold itself:

      rolling mean/max of loading, hot-spot  -> needs `history`, a list of
          this transformer's past states, if the caller has one
      voltage excursion count, ambient percentile -> not present on
          TransformerState at all; see the open question raised alongside
          this file

    Degrades honestly rather than inventing numbers: with no `history`,
    rolling mean/max both equal the current snapshot's own value. Voltage
    excursions default to 0, ambient percentile to 0.5 (median, i.e. "no
    information") — read from the state object only if some future version
    of TransformerState happens to carry them, via getattr so nothing
    breaks either way.
    """
    loadings = [h.loading_k for h in history] if history else []
    loadings.append(state.loading_k)
    hotspots = [h.hotspot_c for h in history] if history else []
    hotspots.append(state.hotspot_c)

    voltage_excursions = getattr(state, "voltage_excursions", 0)
    ambient_percentile = getattr(state, "ambient_percentile", 0.5)

    return [
        sum(loadings) / len(loadings),
        max(loadings),
        sum(hotspots) / len(hotspots),
        max(hotspots),
        state.life_used_frac,
        voltage_excursions,
        ambient_percentile,
    ]


def _make_synthetic_training_set(n: int = 500, seed: int = 13):
    """Synthetic label per spec: life-used crossing a threshold within the
    next N blocks. Development-only, so there's a real trained model to
    exercise the enabled path against before any production history exists.
    """
    rnd = random.Random(seed)
    X, y = [], []
    for _ in range(n):
        loading_mean = rnd.uniform(0.3, 1.1)
        loading_max = loading_mean + rnd.uniform(0, 0.3)
        hotspot_mean = 40 + loading_mean * 100 + rnd.uniform(-10, 10)
        hotspot_max = hotspot_mean + rnd.uniform(0, 20)
        life_used = rnd.uniform(0, 1)
        voltage_excursions = rnd.randint(0, 10)
        ambient_pct = rnd.uniform(0, 1)
        features = [loading_mean, loading_max, hotspot_mean, hotspot_max,
                    life_used, voltage_excursions, ambient_pct]
        risk_score = (0.30 * loading_max + 0.010 * hotspot_max + 0.40 * life_used
                      + 0.05 * voltage_excursions + 0.10 * ambient_pct)
        label = 1 if risk_score + rnd.gauss(0, 0.05) > 0.55 else 0
        X.append(features)
        y.append(label)
    return X, y


def _get_model():
    """Lazily import and train LightGBM. Any failure here (not installed,
    training error) propagates to the caller, which must catch it and
    fall back — this function itself is allowed to raise."""
    global _model
    if _model is None:
        # pyrefly: ignore [missing-import]
        import lightgbm as lgb  # imported lazily so a missing package never
                                 # breaks anything unless lightgbm_enabled=True
        X, y = _make_synthetic_training_set()
        _model = lgb.LGBMClassifier(n_estimators=50, max_depth=4, verbosity=-1)
        _model.fit(X, y)
    return _model


def rank(states: list[TransformerState], histories: dict | None = None) -> list[tuple[str, float]]:
    """Rank transformers by risk, highest risk first.

    Falls back to the stub behaviour (sort by life_used_frac descending)
    whenever lightgbm_enabled is False, or if LightGBM fails to import,
    train, or predict for ANY reason. Must degrade — this path is what
    production runs on if the model is disabled or unavailable, and it is
    never allowed to raise.

    `histories`, if given, maps transformer_id -> list of that
    transformer's past TransformerState-like records, used for real rolling
    features. Optional and purely additive.
    """
    if not LIGHTGBM_ENABLED:
        return _stub_rank(states)

    try:
        model = _get_model()
        histories = histories or {}
        feats = [_extract_features(s, histories.get(s.transformer_id)) for s in states]
        if not feats:
            return []
        scores = model.predict_proba(feats)[:, 1]
        return sorted(
            zip((s.transformer_id for s in states), (float(p) for p in scores)),
            key=lambda p: -p[1],
        )
    except Exception:
        return _stub_rank(states)


def feature_importances() -> dict[str, float]:
    """Named feature -> importance, so C can render an explanation.
    Empty dict if LightGBM is disabled or unavailable — never raises."""
    if not LIGHTGBM_ENABLED:
        return {}
    try:
        model = _get_model()
        return dict(zip(FEATURE_NAMES, (float(v) for v in model.feature_importances_)))
    except Exception:
        return {}