"""GridSentinel: Protection half constraint checking.

Adheres strictly to docs/person-c-implementation-plan.md Phase 2,
docs/person-c-grid-agents.md §5.1, and docs/urjasetu-prd.md §6.4.

Evaluates three physical constraints per transformer:
  1. Loading: K = apparent_kva(sum|net_kw|) / rating_kva > config.loading_limit
  2. Phase Imbalance: max(P_A, P_B, P_C) / mean(P_A, P_B, P_C) > config.phase_limit (1.15)
  3. Voltage Deviation: |dev| > config.voltage_band (0.06) via algo.powerflow.voltage_dev

Loading goes through `engine.physics.loading_k`, which divides by the configured
power factor. This file used to compare kW against a kVA rating directly, which
put its K 5.3% below the figure `grid/health.py` and `engine/sim/baseline.py`
computed for the same transformer in the same block — so the sentinel would pass
a transformer the health agent was already ageing hard. One helper now, and the
power factor is a config field rather than a literal in four places.

Invariants:
  SN1: check() is pure — never mutates trades, ticks, or agent state.
  SN2: Returned breach has severity >= 1.0; None means all checks within limits.
"""
from __future__ import annotations

from typing import Any
from engine.config import DEFAULT as DEFAULT_CONFIG, Config
from engine.domain import Breach, House, MeterTick, Trade, Transformer
from engine.physics import apparent_kva, loading_k
from engine import algo
from engine.trades import delivered_kwh


class GridSentinel:
    """Stateless constraint checker evaluating grid physical feasibility."""

    def __init__(
        self,
        transformers: list[Transformer],
        houses: list[House],
        config: Config = DEFAULT_CONFIG,
        topology: Any = None,
    ) -> None:
        self.transformers = list(transformers)
        self.houses = list(houses)
        self.config = config
        # D's powerflow indexes topology[house_id].distance_m, so default to the
        # house registry rather than None — passing None crashed every block.
        self.topology = topology if topology is not None else {
            h.house_id: h for h in houses}
        self._houses_by_id: dict[str, House] = {h.house_id: h for h in self.houses}
        self._houses_by_transformer: dict[str, list[House]] = {}
        for h in self.houses:
            self._houses_by_transformer.setdefault(h.transformer_id, []).append(h)

    def check(self, trades: list[Trade], ticks: list[MeterTick]) -> Breach | None:
        """Pure evaluation of network physical constraints across all transformers.
        
        Returns the highest-severity breach with severity >= 1.0, or None.
        Guarantees SN1 (no mutations) and SN2 (severity >= 1.0).
        """
        net_kw = self._net_kw_by_house(trades, ticks)
        breaches: list[Breach] = []

        for t in self.transformers:
            t_houses = self._houses_by_transformer.get(t.transformer_id, [])
            breaches.extend(self._loading(t, t_houses, net_kw))
            breaches.extend(self._phase(t, t_houses, net_kw))
            breaches.extend(self._voltage(t, t_houses, net_kw))

        if not breaches:
            return None

        # Return the breach with maximum severity (SN2: severity >= 1.0)
        highest_breach = max(breaches, key=lambda b: b.severity)
        return highest_breach if highest_breach.severity >= 1.0 else None

    def _net_kw_by_house(
        self, trades: list[Trade], ticks: list[MeterTick]
    ) -> dict[str, float]:
        """Calculates net active power (kW) per house from meter ticks and cleared trades.
        
        Sign convention:
          Positive (+): Net import / load drawn from distribution grid.
          Negative (-): Net export into distribution grid.
          
        Formula:
          net_kw[h] = (load_kwh - gen_kwh) / block_hours + sold_kw - bought_kw
          
        Rationale:
          A house consuming 3 kW and generating 8 kW has baseline -5 kW (exporting).
          Selling 5 kWh in P2P (+sold_kw) brings its unaccounted net exchange to 0.
          A buyer consuming 5 kW buying 5 kWh P2P (-bought_kw) brings its grid draw to 0.
        """
        block_hours = self.config.block_hours

        # Baseline net from ticks
        net_kw: dict[str, float] = {}
        for tk in ticks:
            # gross load minus gross PV generation scaled by block duration
            net_kw[tk.house_id] = (tk.load_kwh - tk.gen_kwh) / block_hours

        # Ensure all known houses exist in map (defaulting to 0.0 if missing tick)
        for h in self.houses:
            if h.house_id not in net_kw:
                net_kw[h.house_id] = 0.0

        # Adjust for trades.
        #
        # `delivered_kwh` is the ONE place that decides what a Trade's quantity
        # means. This used to read `quantity_kwh * (1 - curtailed_fraction)`,
        # but FlowAgent.fallback_curtail already scales quantity_kwh down AND
        # records curtailed_fraction, so every curtailed trade was being
        # discounted twice — rho squared instead of rho. Settlement read the
        # quantity raw and was right; the sentinel was wrong, which meant any
        # re-check after a curtailment measured a street that did not exist.
        for tr in trades:
            kw_rate = delivered_kwh(tr) / block_hours
            if tr.seller_id in net_kw:
                net_kw[tr.seller_id] += kw_rate
            if tr.buyer_id in net_kw:
                net_kw[tr.buyer_id] -= kw_rate

        return net_kw

    def _loading(
        self, t: Transformer, houses: list[House], net_kw: dict[str, float]
    ) -> list[Breach]:
        """Loading check: K = apparent_kva(sum|net_kw|) / rating_kva > limit."""
        if t.rating_kva <= 0:
            return []

        actual_load_kw = sum(abs(net_kw.get(h.house_id, 0.0)) for h in houses)
        # Meters report kW; a transformer is rated in kVA. engine.physics does
        # the conversion so every agent gets the same K for the same street.
        k = loading_k(actual_load_kw, t.rating_kva, self.config.power_factor)
        limit = self.config.loading_limit

        if k > limit:
            severity = k / limit
            return [
                Breach(
                    transformer_id=t.transformer_id,
                    kind="loading",
                    severity=severity,
                    detail={
                        "loading_k": k,
                        "limit": limit,
                        "actual_load_kw": actual_load_kw,
                        # The same load expressed the way the rating is. The
                        # flow agent's fallback divides by this, not by the kW
                        # figure, or its retention fraction is off by 1/pf.
                        "actual_load_kva": apparent_kva(
                            actual_load_kw, self.config.power_factor),
                        "rating_kva": t.rating_kva,
                        "power_factor": self.config.power_factor,
                    },
                )
            ]
        return []

    def _phase(
        self, t: Transformer, houses: list[House], net_kw: dict[str, float]
    ) -> list[Breach]:
        """Phase imbalance check: max(P_A, P_B, P_C) / mean(P_A, P_B, P_C) > phase_limit.
        
        Note on EV Hubs: EV hubs are 3-phase commercial chargers (as documented in
        feed.py: 'three-phase load; carried on A by convention'). Their power is drawn
        symmetrically across all three phases (1/3 each), preserving street balance.
        """
        phase_power: dict[str, float] = {"A": 0.0, "B": 0.0, "C": 0.0}
        for h in houses:
            kw = abs(net_kw.get(h.house_id, 0.0))
            if h.house_id.startswith("EVHUB"):
                # 3-phase load distributed equally across phases
                for ph in ("A", "B", "C"):
                    phase_power[ph] += kw / 3.0
            else:
                phase_power[h.phase] += kw

        p_vals = list(phase_power.values())
        mean_p = sum(p_vals) / 3.0

        # If zero power on all phases, system is perfectly balanced (imbalance = 1.0)
        if mean_p <= 1e-9:
            return []

        max_p = max(p_vals)
        imbalance = max_p / mean_p
        limit = self.config.phase_limit

        if imbalance > limit:
            severity = imbalance / limit
            return [
                Breach(
                    transformer_id=t.transformer_id,
                    kind="phase",
                    severity=severity,
                    detail={
                        "imbalance": imbalance,
                        "limit": limit,
                        "phases": phase_power,
                        "mean_kw": mean_p,
                    },
                )
            ]
        return []

    def _voltage(
        self, t: Transformer, houses: list[House], net_kw: dict[str, float]
    ) -> list[Breach]:
        """Voltage deviation check via algo.powerflow.voltage_dev.
        
        Breach if |dev| > config.voltage_band (default 0.06).
        """
        band = self.config.voltage_band
        breaches: list[Breach] = []

        for h in houses:
            dev = algo.powerflow.voltage_dev(h.house_id, net_kw, self.topology)
            if abs(dev) > band:
                severity = abs(dev) / band
                breaches.append(
                    Breach(
                        transformer_id=t.transformer_id,
                        kind="voltage",
                        severity=severity,
                        detail={
                            "house_id": h.house_id,
                            "deviation": dev,
                            "voltage_band": band,
                            "distance_m": h.distance_m,
                        },
                    )
                )

        return breaches
