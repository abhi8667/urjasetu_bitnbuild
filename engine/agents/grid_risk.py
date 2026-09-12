"""Small supervised-ML agent for transformer overload risk.

It learns a logistic-regression classifier from the project's meter feed.  The
implementation is dependency-free so it can run unchanged on Render.
"""
from __future__ import annotations

import math

from engine.config import Config
from engine.domain import GridRiskPrediction, House, MeterTick, Transformer

POWER_FACTOR = 0.95


class GridFailureRiskAgent:
    def __init__(self, transformers: list[Transformer], houses: list[House],
                 config: Config):
        self.config = config
        self.transformers = {t.transformer_id: t for t in transformers}
        self.house_to_transformer = {h.house_id: h.transformer_id for h in houses}
        self.weights = [0.0] * 6
        self.fitted = False
        self.training_examples = 0
        self._previous_loading: dict[str, float] = {}

    def fit(self, feed, training_blocks: int | None = None) -> "GridFailureRiskAgent":
        """Learn from historical blocks; labels are overloads in the next N blocks."""
        horizon = self.config.risk_horizon_blocks
        default_blocks = self.config.risk_training_days * self.config.blocks_per_day
        stop = min(training_blocks or default_blocks, feed.total_blocks() - horizon)
        rows: list[tuple[list[float], float]] = []
        previous = {tid: 0.0 for tid in self.transformers}
        for block in range(max(1, stop)):
            current = self._loading(feed.ticks(block))
            future = [self._loading(feed.ticks(b))
                      for b in range(block + 1, min(block + horizon + 1,
                                                   feed.total_blocks()))]
            ambient = feed.ticks(block)[0].ambient_c if feed.ticks(block) else 25.0
            for tid, loading in current.items():
                peak = max((x[tid] for x in future), default=loading)
                rows.append((self._features(block, loading, loading - previous[tid],
                                            ambient),
                             1.0 if peak >= self.config.loading_limit else 0.0))
                previous[tid] = loading
        self._train(rows)
        self.training_examples = len(rows)
        self.fitted = bool(rows)
        self._previous_loading = {}
        return self

    def predict(self, block: int, ticks: list[MeterTick]) -> list[GridRiskPrediction]:
        if not self.fitted:
            raise RuntimeError("fit() must be called before predict()")
        loading = self._loading(ticks)
        ambient = ticks[0].ambient_c if ticks else 25.0
        out = []
        for tid in sorted(self.transformers):
            trend = loading[tid] - self._previous_loading.get(tid, loading[tid])
            x = self._features(block, loading[tid], trend, ambient)
            score = self._sigmoid(sum(w * v for w, v in zip(self.weights, x)))
            # A conservative peak estimate makes the output useful to the UI,
            # while the learned probability remains the actual decision score.
            peak = loading[tid] * (1.0 + 0.18 * score)
            out.append(GridRiskPrediction(
                transformer_id=tid, block=block,
                horizon_blocks=self.config.risk_horizon_blocks,
                risk_score=round(score, 4),
                predicted_peak_loading=round(peak, 4),
                likely_breach=score >= 0.5,
            ))
        self._previous_loading = loading
        return out

    def _loading(self, ticks: list[MeterTick]) -> dict[str, float]:
        kw = {tid: 0.0 for tid in self.transformers}
        for tick in ticks:
            tid = self.house_to_transformer.get(tick.house_id)
            if tid in kw:
                kw[tid] += abs(tick.load_kwh - tick.gen_kwh) / self.config.block_hours
        return {tid: kw[tid] / POWER_FACTOR / self.transformers[tid].rating_kva
                for tid in kw}

    def _features(self, block: int, loading: float, trend: float,
                  ambient: float) -> list[float]:
        hour = block % self.config.blocks_per_day
        angle = 2 * math.pi * hour / self.config.blocks_per_day
        return [1.0, loading, trend, (ambient - 25.0) / 10.0,
                math.sin(angle), math.cos(angle)]

    def _train(self, rows: list[tuple[list[float], float]]) -> None:
        if not rows:
            return
        positives = sum(y for _, y in rows)
        positive_weight = max(1.0, (len(rows) - positives) / max(1.0, positives))
        weights = [0.0] * len(rows[0][0])
        rate = 0.18
        for _ in range(500):
            gradient = [0.0] * len(weights)
            for x, y in rows:
                p = self._sigmoid(sum(w * v for w, v in zip(weights, x)))
                importance = positive_weight if y else 1.0
                for i, value in enumerate(x):
                    gradient[i] += importance * (p - y) * value
            scale = 1.0 / len(rows)
            for i in range(len(weights)):
                weights[i] -= rate * gradient[i] * scale
        self.weights = weights

    @staticmethod
    def _sigmoid(value: float) -> float:
        value = max(-30.0, min(30.0, value))
        return 1.0 / (1.0 + math.exp(-value))
