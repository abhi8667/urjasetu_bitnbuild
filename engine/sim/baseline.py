"""The net-metering counterfactual, and the comparison. Owner: B. Never cut.

Same meter feed, same seed, no trading. Surplus is exported and credited 1:1
against consumption; credits carry forward and then lapse; no wheeling and no
transaction charges apply. Transformer loading is computed from raw net export
with no reshaping — breaches are recorded but never acted on.

BL2: this module never mutates agent state. It reads the feed and the settled
ledger, and it computes. Nothing here can reach back into a run.

Without this there is no compare screen, and without the compare screen the demo
has no payoff: a system that works is not the same as a system that is better.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from engine import algo
from engine.config import Config
from engine.domain import BillLine, House, Transformer


@dataclass(frozen=True)
class HouseholdBill:
    house_id: str
    kwh_imported: float
    kwh_exported: float
    grid_cost_inr: float
    p2p_cost_inr: float
    p2p_earned_inr: float

    @property
    def net_inr(self) -> float:
        return self.grid_cost_inr + self.p2p_cost_inr - self.p2p_earned_inr


@dataclass(frozen=True)
class RunEconomics:
    label: str
    household_bills_inr: float
    discom_energy_revenue_inr: float
    discom_charge_revenue_inr: float
    export_credits_inr: float
    loss_of_life_hours: float
    per_house: dict[str, HouseholdBill]

    @property
    def discom_revenue_inr(self) -> float:
        return self.discom_energy_revenue_inr + self.discom_charge_revenue_inr


class Baseline:
    def __init__(self, feed, config: Config):
        self.feed = feed
        self.config = config
        self.houses: dict[str, House] = {h.house_id: h for h in feed.houses()}
        self.transformers: list[Transformer] = feed.transformers()

    # ---------------------------------------------------------- net metering

    def run(self, blocks: int | None = None) -> RunEconomics:
        cfg = self.config
        total = blocks if blocks is not None else self.feed.total_blocks()
        one_for_one = cfg.baseline_export_credit == "one_for_one"
        credits: dict[str, list[tuple[int, float]]] = defaultdict(list)
        imported: dict[str, float] = defaultdict(float)
        exported: dict[str, float] = defaultdict(float)
        cost: dict[str, float] = defaultdict(float)
        credited_kwh: dict[str, float] = defaultdict(float)
        life = 0.0

        for block in range(total):
            ticks = self.feed.ticks(block)
            for tick in ticks:
                net = tick.load_kwh - tick.gen_kwh
                if net > 0:
                    imported[tick.house_id] += net
                    if one_for_one:
                        remaining = self._spend_credits(
                            credits[tick.house_id], block, net)
                        credited_kwh[tick.house_id] += net - remaining
                    else:
                        remaining = net
                    cost[tick.house_id] += remaining * self.houses[tick.house_id].retail_tariff
                elif net < 0:
                    exported[tick.house_id] += -net
                    if one_for_one:
                        credits[tick.house_id].append((block, -net))
                    else:
                        # KERC pays the net-metering rate for export; it does not
                        # hand back a unit of retail-priced energy.
                        cost[tick.house_id] -= -net * cfg.feed_in_tariff
            life += self._loss_of_life(ticks)

        bills = {
            hid: HouseholdBill(hid, round(imported[hid], 6), round(exported[hid], 6),
                               round(cost[hid], 6), 0.0, 0.0)
            for hid in sorted(self.houses)
        }
        credit_value = (
            sum(credited_kwh[h] * self.houses[h].retail_tariff for h in credited_kwh)
            if one_for_one else
            sum(exported[h] * cfg.feed_in_tariff for h in exported))
        return RunEconomics(
            label=f"baseline_net_metering_{cfg.baseline_export_credit}",
            household_bills_inr=round(sum(b.net_inr for b in bills.values()), 6),
            discom_energy_revenue_inr=round(sum(cost.values()), 6),
            # Net metering collects no wheeling and no transaction charge — that
            # is the DISCOM's problem with it, and half the pitch.
            discom_charge_revenue_inr=0.0,
            export_credits_inr=round(credit_value, 6),
            loss_of_life_hours=round(life, 9),
            per_house=bills,
        )

    def _spend_credits(self, bank: list[tuple[int, float]], block: int,
                       needed: float) -> float:
        """Oldest first; anything past the carry-forward window has lapsed."""
        horizon = self.config.credit_carryforward_blocks
        i = 0
        while i < len(bank) and needed > 1e-12:
            opened, amount = bank[i]
            if block - opened > horizon:
                bank[i] = (opened, 0.0)
                i += 1
                continue
            used = min(amount, needed)
            bank[i] = (opened, amount - used)
            needed -= used
            if bank[i][1] <= 1e-12:
                i += 1
        bank[:] = [(b, a) for b, a in bank if a > 1e-12 and block - b <= horizon]
        return needed

    # ------------------------------------------------------------- ageing

    def _loss_of_life(self, ticks) -> float:
        """Shares D's thermal model so the comparison is apples to apples.

        While `algo.thermal.ageing_factor` is still a stub returning 1.0, this
        figure is load-independent and the baseline and P2P sides come out
        equal. The comparison machinery is correct; the divergence arrives with
        D's real C57.91 model AND C's reshaping — until trades change physical
        flows, net metering and P2P age the iron identically.
        """
        by_transformer: dict[str, float] = defaultdict(float)
        ambient = ticks[0].ambient_c if ticks else 25.0
        for tick in ticks:
            house = self.houses.get(tick.house_id)
            if house is not None:
                by_transformer[house.transformer_id] += abs(
                    tick.load_kwh - tick.gen_kwh) / self.config.block_hours
        life = 0.0
        for transformer in self.transformers:
            kva = by_transformer.get(transformer.transformer_id, 0.0) / 0.95
            hotspot = algo.thermal.hotspot_c(kva, transformer.rating_kva, ambient)
            life += algo.thermal.loss_of_life_hours(hotspot, self.config.block_hours)
        return life


# ------------------------------------------------------------- comparison

def p2p_economics(feed, config: Config, ledger: list[BillLine],
                  blocks: int | None = None,
                  loss_of_life_hours: float | None = None) -> RunEconomics:
    """The P2P side, reconstructed from the settled ledger plus the feed.

    Whatever a premises could not buy locally it still bought from the DISCOM at
    its retail tariff, and whatever it could not sell locally it still exported
    at the feed-in tariff. Both are counted here, so the two sides compare like
    for like.

    `loss_of_life_hours` should be the health agent's cumulative figure from the
    run. Pass it. Recomputing ageing from raw ticks measures the street as if the
    flow agent had never acted, which reports 0.0 hours saved however well the
    protection worked — the number was identical on both sides for exactly that
    reason until this argument existed.
    """
    houses = {h.house_id: h for h in feed.houses()}
    total = blocks if blocks is not None else feed.total_blocks()
    baseline = Baseline(feed, config)

    bought = defaultdict(float)
    sold = defaultdict(float)
    spend = defaultdict(float)
    earned = defaultdict(float)
    charges = 0.0
    for line in ledger:
        if line.role == "buyer":
            bought[line.house_id] += line.quantity_kwh
            spend[line.house_id] += line.net_inr
            charges += (line.transaction_inr + line.wheeling_inr
                        + line.cross_subsidy_inr + line.ageing_inr)
        elif line.role == "seller":
            sold[line.house_id] += line.quantity_kwh
            earned[line.house_id] += -line.net_inr
            charges += line.transaction_inr

    imported = defaultdict(float)
    exported = defaultdict(float)
    grid_cost = defaultdict(float)
    life = 0.0
    for block in range(total):
        ticks = feed.ticks(block)
        for tick in ticks:
            net = tick.load_kwh - tick.gen_kwh
            if net > 0:
                imported[tick.house_id] += net
            elif net < 0:
                exported[tick.house_id] += -net
        life += baseline._loss_of_life(ticks)

    bills = {}
    for hid in sorted(houses):
        grid_kwh = max(0.0, imported[hid] - bought[hid])
        grid_cost[hid] = grid_kwh * houses[hid].retail_tariff
        residual_export = max(0.0, exported[hid] - sold[hid])
        bills[hid] = HouseholdBill(
            house_id=hid,
            kwh_imported=round(imported[hid], 6),
            kwh_exported=round(exported[hid], 6),
            grid_cost_inr=round(grid_cost[hid] - residual_export * config.feed_in_tariff, 6),
            p2p_cost_inr=round(spend[hid], 6),
            p2p_earned_inr=round(earned[hid], 6),
        )

    return RunEconomics(
        label="p2p",
        household_bills_inr=round(sum(b.net_inr for b in bills.values()), 6),
        discom_energy_revenue_inr=round(sum(grid_cost.values()), 6),
        discom_charge_revenue_inr=round(charges, 6),
        export_credits_inr=0.0,
        loss_of_life_hours=round(
            life if loss_of_life_hours is None else loss_of_life_hours, 9),
        per_house=bills,
    )


def compare(p2p: RunEconomics, baseline: RunEconomics) -> dict:
    """The three numbers on A's compare screen, and the check that matters."""
    return {
        "household_bills_inr": {
            "baseline": baseline.household_bills_inr,
            "p2p": p2p.household_bills_inr,
            "saved_inr": round(baseline.household_bills_inr - p2p.household_bills_inr, 6),
        },
        "discom_revenue_inr": {
            "baseline": baseline.discom_revenue_inr,
            "p2p": p2p.discom_revenue_inr,
            "gained_inr": round(p2p.discom_revenue_inr - baseline.discom_revenue_inr, 6),
            "p2p_charges_inr": p2p.discom_charge_revenue_inr,
        },
        "transformer_life_hours": {
            "baseline": baseline.loss_of_life_hours,
            "p2p": p2p.loss_of_life_hours,
            "saved_hours": round(baseline.loss_of_life_hours - p2p.loss_of_life_hours, 9),
        },
        # The check teams discover too late. It must hold, or the ageing price
        # signal is doing nothing and the central claim is false.
        "baseline_ages_at_least_as_fast": (
            baseline.loss_of_life_hours >= p2p.loss_of_life_hours - 1e-9),
    }
