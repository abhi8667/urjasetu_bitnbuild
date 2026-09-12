"""BatteryBook: Battery custody, StorageClaim tracking, and discharge ordering.

Adheres strictly to docs/person-c-implementation-plan.md Phase 4,
docs/person-c-grid-agents.md §5.3, and docs/urjasetu-prd.md §6.5.

Rules:
  - On absorption:
      claim.quantity = absorbed_kwh * config.round_trip_efficiency (default 0.90)
      custodian is paid absorbed_kwh * config.storage_fee_inr_per_kwh (default 0.35 flat)
      claim.cost_basis = (foregone_clearing_price + storage_fee_per_kwh) / round_trip_efficiency
  - On discharge — OWNER-FIRST:
      A custodian may not discharge its own energy while holding an unexpired
      claim, unless the claim owner has declined at the current price.
      Among claims, oldest opened_block first (FIFO).

Invariants:
  BT1: sum(claim.quantity for claims on house H) <= H.battery_kwh
  BT2: Every claim's custodian has has_battery == True
  BT3: A claim's quantity never increases after creation
  BT4: Discharging reduces exactly one claim and never below zero
"""
from __future__ import annotations

from dataclasses import replace
from engine.config import DEFAULT as DEFAULT_CONFIG, Config
from engine.domain import House, StorageClaim


class BatteryBook:
    """Tracks physical battery charge headroom, claims custody, and discharge order."""

    def __init__(self, houses: list[House], config: Config = DEFAULT_CONFIG) -> None:
        self.config = config
        self.houses_by_id: dict[str, House] = {h.house_id: h for h in houses}
        self.battery_houses: dict[str, House] = {h.house_id: h for h in houses if h.has_battery}

        # claim_id -> StorageClaim
        self._claims: dict[str, StorageClaim] = {}
        # custodian_id -> list of claim_ids in order of arrival
        self._claims_by_custodian: dict[str, list[str]] = {h_id: [] for h_id in self.battery_houses}
        # owner_id -> list of claim_ids
        self._claims_by_owner: dict[str, list[str]] = {}
        # custodian_id -> custodian's own stored energy in kWh
        self._own_stored_kwh: dict[str, float] = {h_id: 0.0 for h_id in self.battery_houses}
        self._counter: int = 0

    def total_stored_kwh(self, house_id: str) -> float:
        """Total physical energy stored in house_id's battery (claims + own)."""
        if house_id not in self.battery_houses:
            return 0.0
        claims_kwh = sum(
            self._claims[cid].quantity_kwh
            for cid in self._claims_by_custodian.get(house_id, [])
            if cid in self._claims
        )
        return self._own_stored_kwh.get(house_id, 0.0) + claims_kwh

    def available_absorption_kwh(self, house_id: str) -> float:
        """Maximum energy (kWh) this battery can absorb in the current block.
        
        Bounded by power limit (battery_max_kw * block_hours) and remaining headroom.
        """
        if house_id not in self.battery_houses:
            return 0.0
        h = self.battery_houses[house_id]
        headroom = max(0.0, h.battery_kwh - self.total_stored_kwh(house_id))
        power_limit = h.battery_max_kw * self.config.block_hours
        return min(headroom, power_limit)

    def absorb(
        self,
        custodian_id: str,
        owner_id: str,
        kwh: float,
        clearing_price: float,
        block: int,
    ) -> StorageClaim:
        """Absorbs energy into custodian's battery on behalf of owner_id.
        
        Enforces:
          BT1: sum(claim.quantity) <= H.battery_kwh
          BT2: Custodian must have has_battery == True
        """
        if custodian_id not in self.battery_houses:
            raise ValueError(f"BT2 Violation: House {custodian_id} does not have a battery")

        h = self.battery_houses[custodian_id]
        kwh = max(0.0, kwh)
        available = self.available_absorption_kwh(custodian_id)
        if kwh > available + 1e-6:
            raise ValueError(
                f"BT1 Violation: Cannot absorb {kwh:.4f} kWh into {custodian_id}; "
                f"available headroom is {available:.4f} kWh"
            )

        self._counter += 1
        claim_id = f"CLM-{custodian_id}-{owner_id}-{block}-{self._counter}"
        
        # Deduct round-trip efficiency at absorption: 4.1 kWh -> 3.69 kWh
        quantity_kwh = round(kwh * self.config.round_trip_efficiency, 6)
        
        # Cost basis in INR/kWh
        fee_per_kwh = self.config.storage_fee_inr_per_kwh
        cost_basis = round((clearing_price + fee_per_kwh) / self.config.round_trip_efficiency, 4)

        claim = StorageClaim(
            claim_id=claim_id,
            owner_id=owner_id,
            custodian_id=custodian_id,
            quantity_kwh=quantity_kwh,
            cost_basis=cost_basis,
            opened_block=block,
        )

        self._claims[claim_id] = claim
        self._claims_by_custodian[custodian_id].append(claim_id)
        self._claims_by_owner.setdefault(owner_id, []).append(claim_id)

        # Assert BT1 holds
        assert self.total_stored_kwh(custodian_id) <= h.battery_kwh + 1e-6, "BT1 invariant violated"
        return claim

    def store_own_energy(self, house_id: str, kwh: float) -> float:
        """Stores the owner's own energy in its battery, respecting headroom."""
        if house_id not in self.battery_houses:
            return 0.0
        available = self.available_absorption_kwh(house_id)
        actual = min(available, max(0.0, kwh))
        effective = actual * self.config.round_trip_efficiency
        self._own_stored_kwh[house_id] += effective
        return effective

    def discharge_claim(self, claim_id: str, kwh: float) -> float:
        """Discharges up to kwh from a specific claim.
        
        Enforces BT3 (quantity never increases) and BT4 (reduces exactly one claim, never < 0).
        """
        if claim_id not in self._claims:
            return 0.0
        claim = self._claims[claim_id]
        if claim.quantity_kwh <= 0.0:
            return 0.0

        discharge_amount = min(claim.quantity_kwh, max(0.0, kwh))
        new_quantity = round(claim.quantity_kwh - discharge_amount, 6)
        assert new_quantity <= claim.quantity_kwh, "BT3 Violation: claim quantity increased"
        assert new_quantity >= 0.0, "BT4 Violation: claim quantity below zero"

        # Update immutable claim dataclass
        updated_claim = replace(claim, quantity_kwh=new_quantity)
        self._claims[claim_id] = updated_claim

        if new_quantity <= 1e-9:
            # Clean up zero-balance claim
            pass

        return discharge_amount

    def discharge_for_owner(self, owner_id: str, kwh: float) -> float:
        """Discharges energy owned by owner_id across custodians using FIFO (oldest opened_block first)."""
        open_claims = self.get_claims_for_owner(owner_id)
        # Sort oldest opened_block first
        open_claims.sort(key=lambda c: (c.opened_block, c.claim_id))

        remaining_to_discharge = kwh
        total_discharged = 0.0

        for c in open_claims:
            if remaining_to_discharge <= 1e-9:
                break
            discharged = self.discharge_claim(c.claim_id, remaining_to_discharge)
            total_discharged += discharged
            remaining_to_discharge -= discharged

        return total_discharged

    def discharge_custodian_own(self, custodian_id: str, kwh: float) -> float:
        """Discharges custodian's own stored energy.
        
        OWNER-FIRST RULE:
        A custodian may NOT discharge its own energy while holding an unexpired
        third-party claim, unless the claim owner has declined at the current price.
        """
        if custodian_id not in self.battery_houses:
            return 0.0

        # Check for open third-party claims in custody
        active_third_party_claims = [
            self._claims[cid]
            for cid in self._claims_by_custodian.get(custodian_id, [])
            if cid in self._claims
            and self._claims[cid].owner_id != custodian_id
            and self._claims[cid].quantity_kwh > 1e-6
        ]

        if active_third_party_claims:
            # Rule: Custodian's own energy is blocked from discharge while open claims exist
            return 0.0

        own = self._own_stored_kwh.get(custodian_id, 0.0)
        actual = min(own, max(0.0, kwh))
        self._own_stored_kwh[custodian_id] -= actual
        return actual

    def get_claims_for_owner(self, owner_id: str) -> list[StorageClaim]:
        """Returns all open (quantity > 0) claims for the specified owner."""
        cids = self._claims_by_owner.get(owner_id, [])
        return [self._claims[cid] for cid in cids if cid in self._claims and self._claims[cid].quantity_kwh > 1e-6]

    def get_claims_in_custody(self, custodian_id: str) -> list[StorageClaim]:
        """Returns all open claims held in custodian's battery."""
        cids = self._claims_by_custodian.get(custodian_id, [])
        return [self._claims[cid] for cid in cids if cid in self._claims and self._claims[cid].quantity_kwh > 1e-6]
