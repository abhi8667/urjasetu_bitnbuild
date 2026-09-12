"""Unit tests for grid/battery.py: Battery custody, claims, and discharge ordering.

Adheres strictly to docs/person-c-implementation-plan.md Phase 4,
docs/person-c-grid-agents.md §5.3, and docs/urjasetu-prd.md §6.5.

Checks:
  - Absorption of 4.1 kWh at 0.90 efficiency yields exactly 3.69 kWh claim
  - Custodian's own energy is not discharged while a third-party claim is open
  - Multiple claims discharge oldest-first (FIFO by opened_block)
  - BT1: Sum of claims on H <= H.battery_kwh
  - BT2: Non-battery custodian raises error
  - BT3: Claim quantity never increases
  - BT4: Discharge reduces exactly one claim, never below zero
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from engine.config import DEFAULT as config
from engine.feed import WhitefieldFeed
from grid.battery import BatteryBook


def test_absorption_round_trip_loss():
    """4.1 kWh absorbed at 0.90 efficiency yields a claim of exactly 3.69 kWh."""
    feed = WhitefieldFeed(config)
    book = BatteryBook(feed.houses(), config=config)
    bat_houses = [h for h in feed.houses() if h.has_battery]
    custodian = bat_houses[0].house_id
    owner = "H-OWNER-TEST"

    claim = book.absorb(custodian_id=custodian, owner_id=owner, kwh=4.1, clearing_price=4.0, block=1)
    assert abs(claim.quantity_kwh - 3.69) < 1e-6, f"Expected 3.69 kWh, got {claim.quantity_kwh}"
    assert claim.owner_id == owner
    assert claim.custodian_id == custodian
    # Cost basis: (4.00 + 0.35) / 0.90 = 4.8333
    expected_cost_basis = round((4.0 + config.storage_fee_inr_per_kwh) / config.round_trip_efficiency, 4)
    assert abs(claim.cost_basis - expected_cost_basis) < 1e-4


def test_custodian_own_energy_blocked_by_open_claim():
    """Custodian may not discharge its own energy while holding an unexpired claim."""
    feed = WhitefieldFeed(config)
    book = BatteryBook(feed.houses(), config=config)
    custodian = [h for h in feed.houses() if h.has_battery][0].house_id

    # Custodian stores some own energy
    stored_own = book.store_own_energy(custodian, 2.0)
    assert stored_own > 0

    # Third party absorbs energy into custodian's battery
    claim = book.absorb(custodian_id=custodian, owner_id="H-OTHER", kwh=3.0, clearing_price=4.0, block=5)
    assert claim.quantity_kwh > 0

    # Custodian tries to discharge own energy -> should be blocked (return 0.0)
    discharged_own = book.discharge_custodian_own(custodian, 1.0)
    assert discharged_own == 0.0, "Custodian was able to discharge own energy while holding open claim!"

    # Now discharge third-party claim completely
    book.discharge_claim(claim.claim_id, claim.quantity_kwh)
    assert len(book.get_claims_in_custody(custodian)) == 0

    # Now custodian CAN discharge own energy
    discharged_own_after = book.discharge_custodian_own(custodian, 1.0)
    assert discharged_own_after == 1.0, f"Custodian should be able to discharge own energy after claim closed, got {discharged_own_after}"


def test_discharge_oldest_first_fifo():
    """Two claims on one owner/custodian discharge oldest opened_block first."""
    feed = WhitefieldFeed(config)
    book = BatteryBook(feed.houses(), config=config)
    custodian = [h for h in feed.houses() if h.has_battery][0].house_id
    owner = "H-OWNER-FIFO"

    # Claim 1 opened at block 10, quantity = 1.8 kWh (from 2.0 absorbed)
    c1 = book.absorb(custodian_id=custodian, owner_id=owner, kwh=2.0, clearing_price=4.0, block=10)
    # Claim 2 opened at block 15, quantity = 1.8 kWh (from 2.0 absorbed)
    c2 = book.absorb(custodian_id=custodian, owner_id=owner, kwh=2.0, clearing_price=4.5, block=15)

    assert abs(c1.quantity_kwh - 1.8) < 1e-6
    assert abs(c2.quantity_kwh - 1.8) < 1e-6

    # Discharge 1.0 kWh for owner -> should come entirely from c1 (block 10)
    discharged = book.discharge_for_owner(owner, 1.0)
    assert abs(discharged - 1.0) < 1e-6

    # Check updated claim states
    c1_updated = book._claims[c1.claim_id]
    c2_updated = book._claims[c2.claim_id]
    assert abs(c1_updated.quantity_kwh - 0.8) < 1e-6, f"c1 should have 0.8 left, got {c1_updated.quantity_kwh}"
    assert abs(c2_updated.quantity_kwh - 1.8) < 1e-6, f"c2 should be untouched (1.8), got {c2_updated.quantity_kwh}"


def test_invariants_bt1_bt2_bt3_bt4():
    """BT1 (headroom bound), BT2 (must have battery), BT3 (non-increasing), BT4 (single claim, >= 0)."""
    feed = WhitefieldFeed(config)
    book = BatteryBook(feed.houses(), config=config)
    non_bat_house = [h for h in feed.houses() if not h.has_battery][0].house_id
    bat_house = [h for h in feed.houses() if h.has_battery][0]

    # BT2: absorbing into non-battery house must fail
    try:
        book.absorb(custodian_id=non_bat_house, owner_id="H-X", kwh=1.0, clearing_price=4.0, block=1)
        assert False, "BT2 failed: absorbed into non-battery house without error"
    except ValueError:
        pass

    # BT1: absorbing beyond capacity must fail
    try:
        book.absorb(custodian_id=bat_house.house_id, owner_id="H-X", kwh=bat_house.battery_kwh * 2, clearing_price=4.0, block=1)
        assert False, "BT1 failed: absorbed beyond capacity without error"
    except ValueError:
        pass

    # BT3 and BT4: test discharge properties
    c = book.absorb(custodian_id=bat_house.house_id, owner_id="H-X", kwh=2.0, clearing_price=4.0, block=1)
    orig_qty = c.quantity_kwh

    discharged = book.discharge_claim(c.claim_id, 0.5)
    assert abs(discharged - 0.5) < 1e-6
    new_c = book._claims[c.claim_id]
    assert new_c.quantity_kwh < orig_qty, "BT3 failed: claim quantity did not decrease"
    assert new_c.quantity_kwh >= 0.0, "BT4 failed: claim quantity below zero"

    # Excess discharge attempts should clamp to remaining and never go below zero
    excess_discharged = book.discharge_claim(c.claim_id, 100.0)
    final_c = book._claims[c.claim_id]
    assert abs(final_c.quantity_kwh) < 1e-6, "BT4 failed: claim below zero"
    assert excess_discharged == new_c.quantity_kwh


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {fn.__name__}: {exc}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
