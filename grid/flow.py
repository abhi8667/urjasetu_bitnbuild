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

from dataclasses import dataclass, field
from typing import Any
from engine.config import DEFAULT as DEFAULT_CONFIG, Config
from engine.domain import Breach, House, MeterTick, Order, ReshapePlan, StorageClaim, Trade, Transformer
from engine.physics import apparent_kva, kw_headroom_for
from engine import algo
from grid.battery import BatteryBook

# The module-level POWER_FACTOR literal that used to live here is gone. It is
# config.power_factor now, read through engine.physics, so this agent, the
# sentinel, the health agent and the baseline cannot drift apart again.


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
    # house_id -> net kW this block, BEFORE any reshaping. The LP builds its
    # loading and voltage rows from these; with an empty dict it has no houses
    # to constrain and reports success having changed nothing.
    baseline_kw: dict = field(default_factory=dict)
    # The LP hardcoded both of these and its _cfg_get() override silently never
    # fired (it reads module attributes on engine.config that do not exist), so
    # it solved with 15-minute blocks and a tighter voltage band than the
    # sentinel enforces. Passed explicitly so there is one source of truth.
    block_hours: float = 1.0


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
        # D's powerflow indexes topology[house_id].distance_m, so default to the
        # house registry rather than None — passing None crashed every block.
        self.topology = topology if topology is not None else {
            h.house_id: h for h in houses}
        self._houses_by_id: dict[str, House] = {h.house_id: h for h in self.houses}
        self._transformers_by_id: dict[str, Transformer] = {t.transformer_id: t for t in self.transformers}
        self._houses_by_transformer: dict[str, list[House]] = {}
        for h in self.houses:
            self._houses_by_transformer.setdefault(h.transformer_id, []).append(h)

    def fallback_curtail(self, trades: list[Trade], breach: Breach) -> list[Trade]:
        """Uniform retention fraction bringing loading to exactly the limit.

        Formula:
          rho = (rating_kva * loading_limit) / actual_load_kva = 1 / severity
          quantity = quantity * rho
          curtailed_fraction = 1.0 - rho

        Note what rho is divided by: `actual_load_kva`, the breach detail the
        sentinel now publishes, NOT `actual_load_kw`. Dividing a kVA rating by a
        kW load mixed units and made rho 5% too generous, so the "brings loading
        exactly to the limit" claim in this docstring was false by 1/pf. The
        two forms now agree, which is why the fallback to 1/severity below is a
        genuine equivalent rather than a different answer.

        Always succeeds. Never raises.
        """
        if not trades or breach.severity <= 1.0:
            return list(trades)

        # Scale factor rho brings loading exactly to the limit
        # E.g. severity = 1.20 -> rho = 1 / 1.20 = 0.8333
        actual_kva = breach.detail.get("actual_load_kva")
        if actual_kva is None:
            # Older breach payloads carried only kW; convert rather than
            # comparing kW against a kVA rating.
            actual_kva = apparent_kva(breach.detail.get("actual_load_kw", 0.0),
                                      self.config.power_factor)
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
        limits = self._build_limits(breach, ticks)
        stored = {h.house_id: batteries.total_stored_kwh(h.house_id)
                  for h in self.houses if h.has_battery} if batteries else {}
        try:
            # solve() wants limits keyed by transformer and batteries as
            # {house_id: kWh stored}. Passing the objects themselves raised
            # TypeError on every call, and the blanket `except Exception` this
            # replaces turned that into feasible=False 270 times out of 270.
            sol = algo.reshape_lp.solve(
                trades, {breach.transformer_id: limits}, stored, self.topology)
        except (TypeError, AttributeError, KeyError, NameError):
            # A contract break, not a solver failure. This must crash loudly:
            # the blanket `except Exception` this replaces turned three shape
            # mismatches into feasible=False on all 270 breaches and hid them
            # behind the curtailment path for an entire integration.
            raise
        except ImportError as exc:
            # scipy missing. This is the single most damaging failure this
            # system had: the LP needs scipy.optimize.linprog, the import was
            # lazy, and the resulting ModuleNotFoundError landed in the blanket
            # `except Exception` below as feasible=False. The whole autonomous
            # reshaping path then did nothing, silently, and the headline
            # "baseline ages faster than P2P" check passed only because both
            # sides came out identical. scipy is a declared dependency in
            # requirements.txt; if it is not importable, say so and stop.
            raise RuntimeError(
                "The reshape LP requires scipy (pip install -r requirements.txt). "
                "Without it the flow agent cannot reshape and the grid-protection "
                "half of UrjaSetu silently does nothing — refusing to run "
                "degraded. See DECISIONS.md D15."
            ) from exc
        except Exception:
            # A genuine solver failure — infeasible, out of memory, no
            # convergence. The fallback exists exactly for this.
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

        # Both views of the same LP decision: trades are authoritative, orders
        # exist so the market agent can re-assert MK1/MK2 over them.
        constrained_trades = self._to_constrained_trades(trades, sol.retention)
        constrained_orders = self._to_constrained_orders(trades, sol.retention)

        # Open storage claims for battery charges
        new_claims: list[StorageClaim] = []
        if batteries is not None and sol.battery_charge:
            new_claims = self._open_claims(sol.battery_charge, trades, breach, batteries)

        discharges = {}
        if batteries is not None:
            for house_id, kwh in sorted(sol.battery_discharge.items()):
                if kwh <= 1e-6:
                    continue
                # Owner-first, oldest claim first; whatever the owners decline
                # comes out of the custodian's own stored energy.
                moved = batteries.discharge_for_owner(house_id, kwh)
                if moved < kwh - 1e-9:
                    moved += batteries.discharge_custodian_own(house_id, kwh - moved)
                if moved > 1e-6:
                    discharges[house_id] = round(moved, 6)

        return ReshapePlan(
            constrained_orders=constrained_orders,
            constrained_trades=constrained_trades,
            battery_charges=sol.battery_charge,
            new_claims=new_claims,
            feasible=True,
            objective_value=sol.objective_value,
            battery_discharges=discharges,
        )

    def _build_limits(self, breach: Breach, ticks: list[MeterTick] | None = None) -> ReshapeLimits:
        """Constructs limits object for algo.reshape_lp.solve."""
        t = self._transformers_by_id.get(breach.transformer_id)
        rating_kva = t.rating_kva if t else 100.0
        loading_limit = self.config.loading_limit
        max_load = rating_kva * loading_limit
        actual_load = breach.detail.get("actual_load_kw", rating_kva * breach.severity)
        uniform_retention = max(0.0, min(1.0, 1.0 / breach.severity)) if breach.severity > 0 else 1.0

        baseline_kw = {}
        for tick in (ticks or []):
            h = self._houses_by_id.get(tick.house_id)
            if h is not None and h.transformer_id == breach.transformer_id:
                baseline_kw[tick.house_id] = (tick.load_kwh - tick.gen_kwh) / self.config.block_hours

        # The sentinel measures K = apparent_kva(sum|net_kw|) / rating_kva, so
        # the REAL power a DT can carry before breaching is
        # rating_kva * loading_limit * power_factor. The LP builds its rows in
        # kW, so it must be handed that kW bound — engine.physics.kw_headroom_for
        # is the inverse of the sentinel's loading_k, which is what makes
        # "feasible" from the LP mean "the re-check will pass".
        #
        # The LP multiplies what it is given by its own loading_limit, so the
        # bound handed over here is pre-divided by it to avoid applying the
        # limit twice.
        headroom_kw = kw_headroom_for(rating_kva, loading_limit,
                                      self.config.power_factor)
        return ReshapeLimits(
            baseline_kw=baseline_kw,
            block_hours=self.config.block_hours,
            transformer_id=breach.transformer_id,
            rating_kva=headroom_kw / loading_limit if loading_limit else headroom_kw,
            loading_limit=loading_limit,
            max_load_kva=max_load,
            uniform_retention=uniform_retention,
            actual_load_kw=actual_load,
            voltage_band=self.config.voltage_band,
            phase_limit=self.config.phase_limit,
        )

    def _to_constrained_trades(
        self, trades: list[Trade], retention: dict[str, float]
    ) -> list[Trade]:
        """The LP's decision, expressed as trades rather than as an order book.

        This is the authoritative output of a reshape. The old path built a
        matched offer/bid pair per trade and asked the auction to re-clear them,
        but a uniform-price auction sorts by price and walks — it does NOT
        preserve pairings. With every pair priced identically, the re-clear
        rematched seller A's retained energy to buyer B's bid at will, and the
        LP's careful per-trade allocation (which is what satisfies the loading
        and voltage rows) was discarded before it ever reached the grid.

        Quantities are already net of curtailment, per engine/trades.py, and
        `curtailed_fraction` records what the LP took.
        """
        out: list[Trade] = []
        for tr in trades:
            c_t = max(0.0, min(1.0, retention.get(tr.trade_id, 1.0)))
            retained_qty = round(tr.quantity_kwh * c_t, 6)
            if retained_qty <= 1e-6:
                continue
            out.append(Trade(
                trade_id=tr.trade_id,
                block=tr.block,
                seller_id=tr.seller_id,
                buyer_id=tr.buyer_id,
                quantity_kwh=retained_qty,
                clearing_price=tr.clearing_price,
                curtailed_fraction=round(1.0 - c_t, 6),
            ))
        return out

    def _to_constrained_orders(
        self, trades: list[Trade], retention: dict[str, float]
    ) -> list[Order]:
        """The same decision as an order book, for the market agent's invariant
        checks (MK1/MK2) only. Never re-matched — see `_to_constrained_trades`."""
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

        # (A `curtailed_trades` list comprehension used to sit here with the
        # filter `if tr.trade_id in battery_charge or True` — unconditionally
        # true, and the result was never read. Removed.)

        for house_id, kwh in sorted(battery_charge.items()):
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

            # Never absorb more than the battery can physically take. The LP is
            # bounded by headroom, but its bound is computed from the stored
            # figure at solve time; anything that moved since would make absorb()
            # raise, and the old blanket `except Exception` swallowed it.
            room = batteries.available_absorption_kwh(house_id)
            take = min(kwh, room)
            if take <= 1e-6:
                continue
            try:
                claims.append(batteries.absorb(
                    custodian_id=house_id,
                    owner_id=owner_id,
                    kwh=take,
                    clearing_price=clearing_price,
                    block=block,
                ))
            except ValueError:
                # BT1/BT2 refusal for this one house. Skip it; the rest of the
                # plan is still valid. Narrow, so a genuine bug still surfaces.
                continue

        return claims
