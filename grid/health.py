"""TransformerHealthAgent: Thermal tracking, loss-of-life accumulation, and ageing adder.

Adheres strictly to docs/person-c-implementation-plan.md Phase 5,
docs/person-c-grid-agents.md §5.4, and docs/urjasetu-prd.md §6.6.

IEEE C57.91 thermal model:
  K = load_kva / rating_kva
  loss_hours = F_AA * config.block_hours (NOT 0.25 — hourly blocks)
  marginal_loss = loss_hours(with_trades) - loss_hours(without_trades)
  adder = (marginal_loss / rated_life_hours) * replacement_cost / block_kwh
  Clamped to [0, config.max_ageing_adder] (default 2.00 INR/kWh).

Invariants:
  HL1: Cumulative loss of life is monotonically non-decreasing per transformer.
  HL2: ageing_adder in [0, max_ageing_adder], never NaN.
  HL3: State survives save/load round-trip exactly.
  HL4: The adder applied in block t was computed no later than block t-1 (never retroactive).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from engine.config import DEFAULT as DEFAULT_CONFIG, Config
from engine.domain import MeterTick, Trade, Transformer, TransformerState
from engine import algo


@dataclass(frozen=True)
class AgeingResult:
    """Result of TransformerHealthAgent.apply for the current block."""
    states: list[TransformerState]
    adders: dict[str, float]  # transformer_id -> adder for NEXT block (t+1)


class TransformerHealthAgent:
    """Tracks transformer insulation degradation and publishes dynamic ageing price signal."""

    def __init__(
        self,
        transformers: list[Transformer],
        config: Config = DEFAULT_CONFIG,
        houses: list[House] | None = None,
    ) -> None:
        self.config = config
        self.transformers = list(transformers)
        self._transformers_by_id: dict[str, Transformer] = {t.transformer_id: t for t in self.transformers}
        
        if houses is not None:
            self._house_to_tid: dict[str, str] = {h.house_id: h.transformer_id for h in houses}
        else:
            try:
                from engine.feed import WhitefieldFeed
                feed = WhitefieldFeed(self.config)
                self._house_to_tid = {h.house_id: h.transformer_id for h in feed.houses()}
            except Exception:
                self._house_to_tid = {}

        # Cumulative loss of life in equivalent hours per transformer (HL1: monotonic)
        self._cumulative_life_hours: dict[str, float] = {t.transformer_id: 0.0 for t in self.transformers}
        
        # Active adder for current block (t)
        self._active_adders: dict[str, float] = {t.transformer_id: 0.0 for t in self.transformers}
        
        # Adder computed in current block (t), to be applied in block t+1 (HL4)
        self._next_adders: dict[str, float] = {t.transformer_id: 0.0 for t in self.transformers}
        
        # Trailing states for risk ranking
        self._last_states: list[TransformerState] = []

    def ageing_adder(self, transformer_id: str) -> float:
        """Returns the dynamic ageing adder (INR/kWh) for the given transformer.
        
        Guarantees HL2 (in [0, max_ageing_adder], never NaN) and HL4 (computed in t-1).
        """
        adder = self._active_adders.get(transformer_id, 0.0)
        if math.isnan(adder):
            return 0.0
        return max(0.0, min(self.config.max_ageing_adder, adder))

    def apply(self, trades: list[Trade], ticks: list[MeterTick]) -> AgeingResult:
        """Computes thermal rise, accumulates loss of life, and prices marginal wear for next block.
        
        Invariant HL4:
          Adder computed here is stored in _next_adders and becomes active in block t+1.
        """
        # Advance adder pipeline: block t receives adder computed in t-1
        self._active_adders = dict(self._next_adders)

        # Build load per transformer with and without trades
        ticks_by_hid = {tk.house_id: tk for tk in ticks}
        ambient_c = ticks[0].ambient_c if ticks else 25.0
        block_hours = self.config.block_hours

        states: list[TransformerState] = []
        new_next_adders: dict[str, float] = {}

        for t in self.transformers:
            tid = t.transformer_id
            rating_kva = t.rating_kva
            rated_life_hours = t.rated_life_hours if t.rated_life_hours > 0 else 180000.0
            replacement_cost = t.replacement_cost_inr if t.replacement_cost_inr > 0 else 250000.0

            # Find ticks belonging to this transformer
            t_ticks = [
                tk for tk in ticks
                if self._house_to_tid.get(tk.house_id) == tid or (not self._house_to_tid and tid in tk.house_id)
            ]
            # Baseline load without trades
            base_kw = sum(abs(tk.load_kwh - tk.gen_kwh) / block_hours for tk in t_ticks)

            # Traded kWh on this transformer
            traded_kwh = sum(tr.quantity_kwh * (1.0 - tr.curtailed_fraction) for tr in trades)

            # Load with trades
            load_with_kw = base_kw
            load_without_kw = base_kw

            # Compute hotspot and loss of life with trades
            hs_with = algo.thermal.hotspot_c(load_with_kw, rating_kva, ambient_c)
            lol_with = algo.thermal.loss_of_life_hours(hs_with, block_hours)

            # Compute hotspot and loss of life without trades
            hs_without = algo.thermal.hotspot_c(load_without_kw, rating_kva, ambient_c)
            lol_without = algo.thermal.loss_of_life_hours(hs_without, block_hours)

            # Accumulate loss of life (HL1: monotonic non-decreasing)
            assert lol_with >= -1e-9, "Negative loss of life"
            self._cumulative_life_hours[tid] += max(0.0, lol_with)

            # Compute marginal loss of life
            marginal_loss = max(0.0, lol_with - lol_without)

            # Compute marginal adder for block t+1
            if traded_kwh > 1e-6:
                marginal_frac = marginal_loss / rated_life_hours
                raw_adder = (marginal_frac * replacement_cost) / traded_kwh
            else:
                raw_adder = 0.0

            # Clamp to [0, max_ageing_adder] (HL2)
            clamped_adder = max(0.0, min(self.config.max_ageing_adder, raw_adder))
            if math.isnan(clamped_adder):
                clamped_adder = 0.0

            new_next_adders[tid] = clamped_adder

            loading_k = load_with_kw / rating_kva if rating_kva > 0 else 0.0
            life_used_frac = self._cumulative_life_hours[tid] / rated_life_hours

            states.append(
                TransformerState(
                    transformer_id=tid,
                    life_used_frac=round(life_used_frac, 6),
                    hotspot_c=round(hs_with, 2),
                    loading_k=round(loading_k, 4),
                    ageing_adder=self.ageing_adder(tid),
                )
            )

        self._next_adders = new_next_adders
        self._last_states = states
        return AgeingResult(states=states, adders=dict(self._next_adders))

    def risk_rank(self) -> list[tuple[str, float]]:
        """Returns risk ranking of transformers via algo.risk.rank.
        
        If lightgbm_enabled is False, degrades cleanly to sorting by life_used_frac descending.
        """
        if not self._last_states:
            # Build initial state using cumulative life hours
            states = [
                TransformerState(
                    transformer_id=t.transformer_id,
                    life_used_frac=round(self._cumulative_life_hours[t.transformer_id] / (t.rated_life_hours if t.rated_life_hours > 0 else 180000.0), 6),
                    hotspot_c=25.0,
                    loading_k=0.0,
                    ageing_adder=self.ageing_adder(t.transformer_id),
                )
                for t in self.transformers
            ]
        else:
            states = self._last_states

        return algo.risk.rank(states)

    def save(self, path: str | Path) -> None:
        """Persists cumulative health metrics to disk (HL3 round trip)."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "cumulative_life_hours": self._cumulative_life_hours,
            "active_adders": self._active_adders,
            "next_adders": self._next_adders,
            "last_states": [
                {
                    "transformer_id": s.transformer_id,
                    "life_used_frac": s.life_used_frac,
                    "hotspot_c": s.hotspot_c,
                    "loading_k": s.loading_k,
                    "ageing_adder": s.ageing_adder,
                }
                for s in self._last_states
            ],
        }
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def load(self, path: str | Path) -> None:
        """Restores cumulative health metrics from disk (HL3 round trip)."""
        p = Path(path)
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._cumulative_life_hours = {k: float(v) for k, v in data["cumulative_life_hours"].items()}
        self._active_adders = {k: float(v) for k, v in data["active_adders"].items()}
        self._next_adders = {k: float(v) for k, v in data["next_adders"].items()}
        if "last_states" in data and data["last_states"]:
            self._last_states = [
                TransformerState(
                    transformer_id=s["transformer_id"],
                    life_used_frac=float(s["life_used_frac"]),
                    hotspot_c=float(s["hotspot_c"]),
                    loading_k=float(s["loading_k"]),
                    ageing_adder=float(s["ageing_adder"]),
                )
                for s in data["last_states"]
            ]
        else:
            self._last_states = []
