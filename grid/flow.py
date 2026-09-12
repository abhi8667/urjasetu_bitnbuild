"""FlowAgent: Constraint-feasible trade reshaping and fallback curtailment.

Adheres strictly to docs/person-c-implementation-plan.md Phase 3,
docs/person-c-grid-agents.md §5.2, and docs/urjasetu-prd.md §6.5.

Orchestrates the LP solver (algo.reshape_lp), manages uniform curtailment fallback,
generates constrained orders for market re-clearing, and converts battery absorption
into StorageClaims.

Invariants:
  FL1: After a successful reshape, re-checking returns None, or the fallback runs.
  FL2: sum(new_claims.quantity) <= sum(battery_charges) * round_trip_efficiency.
  FL3: No claim's custodian is a house without a battery.
  FL4: Total energy is conserved every block to within 1e-6 kWh:
       Generation = Consumption + Net Battery Change + Losses
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from engine.config import DEFAULT as DEFAULT_CONFIG, Config
from engine.domain import Breach, House, MeterTick, Order, ReshapePlan, StorageClaim, Trade, Transformer
from engine import algo
from grid.battery import BatteryBook


@dataclass(frozen=True)
class ReshapeLimits:
    """Limits passed to algo.reshape_lp.solve."""
    transformer_id: str
    rating_kva: float
    loading_limit: float
    max_load_kva: float
    uniform_retention: float
    actual_load_kw: float
    voltage_band: float
    phase_limit: float


class FlowAgent:
    """Protection agent reshaping trades and managing curtailment fallback."""

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
        self.topology = topology
        self._houses_by_id: dict[str, House] = {h.house_id: h for h in self.houses}
        self._transformers_by_id: dict[str, Transformer] = {t.transformer_id: t for t in self.transformers}
        self._houses_by_transformer: dict[str, list[House]] = {}
        for h in self.houses:
            self._houses_by_transformer.setdefault(h.transformer_id, []).append(h)

    def fallback_curtail(self, trades: list[Trade], breach: Breach) -> list[Trade]:
        """Uniform retention fraction bringing loading to exactly the limit.
        
        Formula:
          rho = (rating_kva * loading_limit) / actual_kva = 1.0 / breach.severity
          quantity = quantity * rho
          curtailed_fraction = 1.0 - rho
          
        Always succeeds. Never raises.
        """
        if not trades or breach.severity <= 1.0:
            return list(trades)

        # Scale factor rho brings loading exactly to the limit
        # E.g. severity = 1.20 -> rho = 1 / 1.20 = 0.8333
        actual_kva = breach.detail.get("actual_load_kw", 0.0)
        t = self._transformers_by_id.get(breach.transformer_id)
        if t and actual_kva > 0.0:
            rho = (t.rating_kva * self.config.loading_limit) / actual_kva
        else:
            rho = 1.0 / breach.severity

        rho = max(0.0, min(1.0, rho))
        curtailed_fraction = round(1.0 - rho, 6)

        curtailed_trades: list[Trade] = []
        for tr in trades:
            h_seller = self._houses_by_id.get(tr.seller_id)
            h_buyer = self._houses_by_id.get(tr.buyer_id)
            on_dt = (
                (h_seller is not None and h_seller.transformer_id == breach.transformer_id)
                or (h_buyer is not None and h_buyer.transformer_id == breach.transformer_id)
            )

            if on_dt:
                # Retain fraction rho of the original quantity
                new_qty = round(tr.quantity_kwh * rho, 6)
                curtailed_trades.append(
                    Trade(
                        trade_id=tr.trade_id,
                        block=tr.block,
                        seller_id=tr.seller_id,
                        buyer_id=tr.buyer_id,
                        quantity_kwh=new_qty,
                        clearing_price=tr.clearing_price,
                        curtailed_fraction=curtailed_fraction,
                    )
                )
            else:
                curtailed_trades.append(tr)

        return curtailed_trades

    def reshape(
        self,
        trades: list[Trade],
        ticks: list[MeterTick],
        breach: Breach,
        batteries: BatteryBook | None = None,
    ) -> ReshapePlan:
        """Formulates limits and solves trade reshaping via algo.reshape_lp.
        
        Translates LP solutions into constrained orders for market re-clearing and
        opens StorageClaims for battery charges. Guarantees safe failure (never raises).
        """
        limits = self._build_limits(breach)
        try:
            sol = algo.reshape_lp.solve(trades, limits, batteries, self.topology)
        except Exception:
            # Fallback cleanly if LP solver encounters unforeseen error
            return ReshapePlan(
                constrained_orders=[],
                battery_charges={},
                new_claims=[],
                feasible=False,
                objective_value=0.0,
            )

        if not sol.feasible:
            return ReshapePlan(
                constrained_orders=[],
                battery_charges={},
                new_claims=[],
                feasible=False,
                objective_value=0.0,
            )

        # Build constrained orders for market re-clearing
        constrained_orders = self._to_constrained_orders(trades, sol.retention)

        # Open storage claims for battery charges
        new_claims: list[StorageClaim] = []
        if batteries is not None and sol.battery_charge:
            new_claims = self._open_claims(sol.battery_charge, trades, breach, batteries)

        return ReshapePlan(
            constrained_orders=constrained_orders,
            battery_charges=sol.battery_charge,
            new_claims=new_claims,
            feasible=True,
            objective_value=sol.objective_value,
        )

    def _build_limits(self, breach: Breach) -> ReshapeLimits:
        """Constructs limits object for algo.reshape_lp.solve."""
        t = self._transformers_by_id.get(breach.transformer_id)
        rating_kva = t.rating_kva if t else 100.0
        loading_limit = self.config.loading_limit
        max_load = rating_kva * loading_limit
        actual_load = breach.detail.get("actual_load_kw", rating_kva * breach.severity)
        uniform_retention = max(0.0, min(1.0, 1.0 / breach.severity)) if breach.severity > 0 else 1.0

        return ReshapeLimits(
            transformer_id=breach.transformer_id,
            rating_kva=rating_kva,
            loading_limit=loading_limit,
            max_load_kva=max_load,
            uniform_retention=uniform_retention,
            actual_load_kw=actual_load,
            voltage_band=self.config.voltage_band,
            phase_limit=self.config.phase_limit,
        )

    def _to_constrained_orders(
        self, trades: list[Trade], retention: dict[str, float]
    ) -> list[Order]:
        """Converts retained trade fractions c_t in [0, 1] into constrained matched Orders."""
        orders: list[Order] = []
        for tr in trades:
            c_t = retention.get(tr.trade_id, 1.0)
            c_t = max(0.0, min(1.0, c_t))
            retained_qty = round(tr.quantity_kwh * c_t, 6)

            if retained_qty > 1e-6:
                # Matched pair of offer and bid at clearing price so market.clear clears retained qty
                orders.append(
                    Order(
                        order_id=f"ORD-OFF-{tr.trade_id}",
                        block=tr.block,
                        house_id=tr.seller_id,
                        side="offer",
                        quantity_kwh=retained_qty,
                        limit_price=tr.clearing_price,
                    )
                )
                orders.append(
                    Order(
                        order_id=f"ORD-BID-{tr.trade_id}",
                        block=tr.block,
                        house_id=tr.buyer_id,
                        side="bid",
                        quantity_kwh=retained_qty,
                        limit_price=tr.clearing_price,
                    )
                )
        return orders

    def _open_claims(
        self,
        battery_charge: dict[str, float],
        trades: list[Trade],
        breach: Breach,
        batteries: BatteryBook,
    ) -> list[StorageClaim]:
        """Converts battery absorption into StorageClaims in BatteryBook."""
        claims: list[StorageClaim] = []
        block = trades[0].block if trades else 0

        # Find trades that were curtailed on this transformer to identify claim owners
        curtailed_trades = [
            tr for tr in trades
            if tr.trade_id in battery_charge or True
        ]

        for house_id, kwh in battery_charge.items():
            if kwh <= 1e-6:
                continue

            # Ensure FL3: custodian must have battery
            h = self._houses_by_id.get(house_id)
            if not h or not h.has_battery:
                continue

            # Owner is the seller whose trade energy was absorbed, or the house itself
            owner_id = house_id
            clearing_price = self.config.feed_in_tariff
            for tr in trades:
                if tr.seller_id == house_id or tr.buyer_id == house_id:
                    clearing_price = tr.clearing_price
                    owner_id = tr.seller_id
                    break

            try:
                claim = batteries.absorb(
                    custodian_id=house_id,
                    owner_id=owner_id,
                    kwh=kwh,
                    clearing_price=clearing_price,
                    block=block,
                )
                claims.append(claim)
            except Exception:
                # Guard against individual absorption errors without failing reshape
                pass

        return claims
