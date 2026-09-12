"""FL4 — energy conservation. PRD §10 integration check 2.

The PRD states it as "generation = consumption + net battery change + losses,
within 1e-6 kWh per block". Taken literally over the whole street that is not a
testable claim: this street is a net importer every block (roughly 2060 kWh/day
of demand against 195 kWh of surplus), so the grid supplies the difference, and
grid exchange is a RESIDUAL the engine never tracks independently. Writing

    grid = load + charge + losses - gen - discharge

and then asserting the balance closes proves nothing — it is an identity by
construction and would pass against arbitrarily broken code.

So this file checks the four statements underneath FL4 that CAN be false, and
that between them are what FL4 is actually protecting:

  FL4-a  battery bookkeeping — what BatteryBook holds changes by exactly what
         the runner moved. Catches kWh created or destroyed inside the storage
         layer, which is the failure the master plan calls out by name.
  FL4-b  battery physical bounds — never below empty, never above capacity.
  FL4-c  traded energy is physically backed — a premises cannot sell surplus it
         did not generate. This is where energy gets invented if anywhere does.
  FL4-d  losses are real — energy charged for as transmission loss must actually
         leave the system, not merely move as money.

Run standalone: `python3 tests/test_fl4_energy_conservation.py`
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.agents.settlement import SettlementAgent
from engine.bus import Bus
from engine.config import DEFAULT as CONFIG
from engine.feed import WhitefieldFeed
from engine.sim.pool import AgentPool
from engine.sim.runner import Runner
from grid import BatteryBook, FlowAgent, GridSentinel, TransformerHealthAgent

TOL = 1e-6
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    return ok


class _AuditedBattery(BatteryBook):
    """BatteryBook that records its own total stored before and after every
    movement, so the runner's belief can be checked against ground truth."""

    def __init__(self, houses, config):
        super().__init__(houses, config)
        self._battery_ids = [h.house_id for h in houses if h.has_battery]
        self.movements = []           # (kind, house_id, kwh, total_before, total_after)

    def total_across_fleet(self) -> float:
        return sum(self.total_stored_kwh(h) for h in self._battery_ids)

    def store_own_energy(self, house_id, kwh):
        before = self.total_across_fleet()
        out = super().store_own_energy(house_id, kwh)
        self.movements.append(("charge", house_id, out, before, self.total_across_fleet()))
        return out

    def discharge_for_owner(self, owner_id, kwh):
        before = self.total_across_fleet()
        out = super().discharge_for_owner(owner_id, kwh)
        self.movements.append(("discharge", owner_id, out, before, self.total_across_fleet()))
        return out

    def discharge_custodian_own(self, custodian_id, kwh):
        before = self.total_across_fleet()
        out = super().discharge_custodian_own(custodian_id, kwh)
        self.movements.append(("discharge", custodian_id, out, before,
                               self.total_across_fleet()))
        return out


def _run(blocks=None):
    feed = WhitefieldFeed(CONFIG)
    houses, transformers = feed.houses(), feed.transformers()
    pool = AgentPool(houses, CONFIG)
    settle = SettlementAgent(houses, CONFIG, consumers=pool.consumers, feed=feed)
    batteries = _AuditedBattery(houses, CONFIG)
    bus = Bus(ring_size=2_000_000)          # keep every event; FL4 needs all of them
    runner = Runner(feed, pool, CONFIG, bus=bus,
                    sentinel=GridSentinel(transformers, houses, CONFIG),
                    flow=FlowAgent(transformers, houses, CONFIG),
                    health=TransformerHealthAgent(transformers, CONFIG, houses),
                    settlement=settle, batteries=batteries)
    runner.run(blocks=blocks)
    return feed, houses, runner, settle, batteries


# --------------------------------------------------------------------- FL4

def test_fl4a_battery_bookkeeping_is_exact():
    """Every recorded movement must change fleet-wide stored energy by exactly
    the kWh the movement claims to have moved. A charge that stores less than it
    was handed, or a discharge that releases more than it held, shows up here."""
    _, _, _, _, batteries = _run(blocks=240)
    bad = []
    for kind, house_id, kwh, before, after in batteries.movements:
        delta = after - before
        expected = kwh if kind == "charge" else -kwh
        if abs(delta - expected) > TOL:
            bad.append((kind, house_id, kwh, delta, expected))
    check("FL4-a  battery bookkeeping exact to 1e-6",
          not bad,
          f"{len(batteries.movements)} movements audited"
          + (f", {len(bad)} inconsistent: {bad[:3]}" if bad else ""))


def test_fl4b_batteries_never_exceed_physical_bounds():
    """Stored energy stays within [0, capacity] for every battery at all times."""
    _, houses, _, _, batteries = _run(blocks=240)
    cap = {h.house_id: h.battery_kwh for h in houses if h.has_battery}
    violations = []
    for house_id, capacity in cap.items():
        stored = batteries.total_stored_kwh(house_id)
        if stored < -TOL or stored > capacity + TOL:
            violations.append((house_id, stored, capacity))
    check("FL4-b  every battery within [0, capacity]",
          not violations,
          f"{len(cap)} batteries" + (f", violations: {violations}" if violations else ""))


def test_fl4c_traded_energy_is_physically_backed():
    """A premises cannot sell energy it never had.

    Available to sell in a block = physical surplus (gen - load) + whatever it
    discharged from its own battery. Offers are built from a FORECAST, so a
    seller can commit to more than it turns out to have; nothing in the engine
    reconciles the forecast against the actual afterwards. That gap is energy
    the market moved and the street never produced.
    """
    feed, houses, runner, settle, _ = _run(blocks=240)
    discharged = defaultdict(float)
    for event in runner.bus.recent_events("battery_moved"):
        for house_id, kwh in event["payload"]["discharged_kwh"].items():
            discharged[(event["block"], house_id)] += kwh

    sold = defaultdict(float)
    for line in settle.ledger:
        if line.role == "seller":
            sold[(line.block, line.house_id)] += line.quantity_kwh

    oversold_kwh = 0.0
    oversold_blocks = 0
    traded_kwh = sum(sold.values())
    for (block, house_id), qty in sold.items():
        tick = feed.tick_for(house_id, block)
        available = max(0.0, tick.gen_kwh - tick.load_kwh) + discharged.get((block, house_id), 0.0)
        if qty > available + TOL:
            oversold_kwh += qty - available
            oversold_blocks += 1

    pct = 100 * oversold_kwh / traded_kwh if traded_kwh else 0.0
    check("FL4-c  no premises sells energy it never had",
          oversold_kwh <= TOL,
          f"{oversold_kwh:.2f} kWh of {traded_kwh:.1f} kWh traded ({pct:.1f}%) "
          f"across {oversold_blocks} seller-blocks")


def test_fl4d_transmission_losses_actually_remove_energy():
    """Transmission loss is charged for. Is any energy actually lost?

    settlement bills the buyer for wheeling and the per-premises transmission
    loss percentage exists on the feed, but if the delivered quantity equals the
    sold quantity then the loss is a money transfer only — the kWh never leave
    the system, and FL4's 'plus losses' term has nothing behind it.
    """
    feed, _, _, settle, _ = _run(blocks=240)
    sold = sum(l.quantity_kwh for l in settle.ledger if l.role == "seller")
    delivered = sum(l.quantity_kwh for l in settle.ledger if l.role == "buyer")
    implied_loss = sum(
        l.quantity_kwh * feed.transmission_loss_pct(l.house_id) / 100.0
        for l in settle.ledger if l.role == "seller")
    check("FL4-d  losses remove energy, not just money",
          abs((sold - delivered) - implied_loss) <= TOL,
          f"sold {sold:.2f} kWh, delivered {delivered:.2f} kWh, "
          f"difference {sold - delivered:.2f} kWh vs {implied_loss:.2f} kWh of "
          f"loss billed")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        try:
            fn()
        except Exception as exc:
            RESULTS.append((fn.__name__, False))
            print(f"  ERROR  {fn.__name__}: {type(exc).__name__}: {exc}")
    failed = sum(1 for _, ok in RESULTS if not ok)
    print(f"\n{len(RESULTS) - failed}/{len(RESULTS)} FL4 checks passed")
    sys.exit(1 if failed else 0)
