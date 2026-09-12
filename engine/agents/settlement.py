"""Settlement agent — itemised billing. Owner: B.

Eight components per trade, every one a config value and none a literal. Never
aggregate at source: ST1 is only checkable if each term is visible, and the
DISCOM ledger screen *is* these columns.

Sign convention on `net_inr` is from the party's point of view — positive pays,
negative receives. Energy cancels between buyer and seller, so summing every
party's net position leaves exactly the charges the DISCOM collected. That is
ST1, and it falls out of the convention rather than being computed separately.

Three things this file used to get wrong:

  * `platform_fee` (Rs0.25/kWh) and `gst_pct` (5%) were configured, documented
    here as part of "six components, every one a config value", and never
    billed. There was no column for either. They are components 7 and 8 now.
  * The storage fee was documented in `grid/battery.py` as "custodian is paid
    absorbed_kwh x storage_fee_inr_per_kwh" and hardcoded to 0.0 on both bill
    lines. No money ever reached a custodian. `settle_claims` pays it.
  * The ageing adder was read off `AgeingResult.adders`, the figure computed
    from THIS block's own load. That is retroactive pricing and breaks HL4.
    It reads `active_adders` (computed in t-1) now — see engine/domain.py.

CHARGE-STACK CAP. Adding the platform fee and GST puts real pressure on CN2,
which says a buyer's all-in cost may never exceed what the DISCOM would have
charged. CN1 guarantees the ENERGY price is below retail; it says nothing about
energy plus six charges. Rather than let CN2 fire in the middle of a run — it is
an assertion, it would stop the run, and the fault would not be in the bidding —
the stack is capped explicitly, and the ageing adder is what gives way. That is
the right term to trim: it is the discretionary price signal, not a statutory
charge, and `max_ageing_adder` already exists because the signal is understood
to need bounding. Every trim is recorded in `adder_trimmed_inr` so the cap can
never quietly hide a settlement bug.
"""
from __future__ import annotations

from engine.bus import Bus
from engine.config import Config
from engine.domain import (AgeingResult, BillLine, House, InvariantError,
                           StorageClaim, Trade)

AGENT_ID = "settlement"


class SettlementAgent:
    def __init__(self, houses: list[House], config: Config, bus: Bus | None = None,
                 consumers: dict[str, object] | None = None, feed=None):
        self.houses = {h.house_id: h for h in houses}
        self.config = config
        self.bus = bus
        self.consumers = consumers or {}
        self.feed = feed
        self.charges_collected = 0.0
        self.ledger: list[BillLine] = []
        #: How much ageing adder the CN2 cap gave back, in rupees. Nonzero means
        #: the charge stack is pressing against retail on this street.
        self.adder_trimmed_inr = 0.0
        #: Claim ids already billed a storage fee, so a claim that stays open
        #: across blocks is not charged twice.
        self._settled_claims: set[str] = set()

    # ------------------------------------------------------------- trades

    def settle(self, trades: list[Trade], ageing: AgeingResult | None = None,
               block: int | None = None,
               claims: list[StorageClaim] | None = None) -> list[BillLine]:
        cfg = self.config
        lines: list[BillLine] = []
        collected = 0.0

        for trade in sorted(trades, key=lambda t: t.trade_id):
            qty = trade.quantity_kwh
            # Energy physically lost between the two premises. The registry
            # derives it per-premises from distance to the DT (3 + 0.02 * m,
            # giving 3.25-6.75%). It was being CHARGED for and never REMOVED:
            # sold kWh equalled delivered kWh exactly, so FL4's "plus losses"
            # term had no energy behind it and the wires were lossless in fact
            # while being billed as lossy. The buyer pays for what was injected
            # on its behalf and receives what survives the trip.
            loss_pct = self.feed.transmission_loss_pct(trade.seller_id) if self.feed else 0.0
            loss_kwh = qty * loss_pct / 100.0
            delivered = qty - loss_kwh

            energy = qty * trade.clearing_price
            transaction_half = qty * cfg.transaction_charge / 2
            wheeling = qty * cfg.wheeling_charge
            cross_subsidy = qty * cfg.cross_subsidy if cfg.cross_subsidy_enabled else 0.0
            platform = qty * cfg.platform_fee
            ageing_inr = qty * self._adder(trade, ageing)

            # GST applies to the SERVICES the DISCOM and the platform render —
            # wheeling, the buyer's half of the transaction charge, and the
            # platform fee. Not to the energy itself (that is a transfer between
            # two consumers, not a supply by the licensee) and not to the ageing
            # adder (a wear recovery, not a service). Stated here because the
            # base of a tax is a policy choice and leaving it implicit in an
            # expression is how it gets silently changed.
            taxable = wheeling + transaction_half + platform
            gst = taxable * cfg.gst_pct / 100.0

            # CN2 cap — see the module docstring. The buyer's all-in must stay at
            # or below what this much DELIVERED energy would have cost from the
            # grid, and the ageing adder is the term that yields.
            house = self.houses.get(trade.buyer_id)
            if house is not None:
                ceiling = delivered * house.retail_tariff
                fixed = (energy + transaction_half + wheeling + cross_subsidy
                         + platform + gst)
                allowed_adder = ceiling - fixed
                if ageing_inr > allowed_adder:
                    self.adder_trimmed_inr += ageing_inr - max(0.0, allowed_adder)
                    ageing_inr = max(0.0, allowed_adder)

            buyer_net = (energy + transaction_half + wheeling + cross_subsidy
                         + platform + gst + ageing_inr)

            seller = BillLine(
                line_id=f"{trade.trade_id}:S", block=trade.block, trade_id=trade.trade_id,
                house_id=trade.seller_id, role="seller",
                quantity_kwh=qty, loss_kwh=0.0,
                unit_price_inr=trade.clearing_price,
                energy_inr=-energy, transaction_inr=transaction_half,
                wheeling_inr=0.0, cross_subsidy_inr=0.0, storage_fee_inr=0.0,
                ageing_inr=0.0, platform_inr=0.0, gst_inr=0.0,
                net_inr=-energy + transaction_half)
            buyer = BillLine(
                line_id=f"{trade.trade_id}:B", block=trade.block, trade_id=trade.trade_id,
                house_id=trade.buyer_id, role="buyer",
                quantity_kwh=round(delivered, 9), loss_kwh=round(loss_kwh, 9),
                unit_price_inr=trade.clearing_price,
                energy_inr=energy, transaction_inr=transaction_half,
                wheeling_inr=wheeling, cross_subsidy_inr=cross_subsidy,
                storage_fee_inr=0.0, ageing_inr=ageing_inr,
                platform_inr=platform, gst_inr=gst,
                net_inr=buyer_net)

            for line in (seller, buyer):
                _assert_st2(line)
                lines.append(line)
            # Everything except energy leaves the participants for the DISCOM.
            # The storage fee is the exception — it moves between two households,
            # so it nets to zero and is not collected by anyone.
            collected += (2 * transaction_half + wheeling + cross_subsidy
                          + platform + gst + ageing_inr)

            consumer = self.consumers.get(trade.buyer_id)
            if consumer is not None:
                # Delivered, not injected: the buyer got less than was sent, and
                # CN2 must compare what it actually received against what that
                # much energy would have cost from the grid.
                consumer.record_purchase(delivered, buyer.net_inr)

        # Storage fees for claims opened this block. Zero-sum between two
        # households, so they add lines without adding to `collected`.
        lines.extend(self._claim_lines(claims or []))

        _assert_st1(lines, collected)
        _assert_st3(lines, trades, claims or [])

        self.ledger.extend(lines)
        self.charges_collected += collected
        if self.bus is not None and block is not None:
            # NOTE the topic. The runner publishes `block_settled` at the end of
            # its tick with a different payload shape; two producers on one topic
            # with two schemas is a bug waiting for a subscriber. This one is
            # `bill_lines_posted` — the settlement agent's own event.
            self.bus.publish("bill_lines_posted", block, AGENT_ID, {
                "bill_lines": len(lines),
                "charges_collected_inr": round(collected, 6),
                "adder_trimmed_inr": round(self.adder_trimmed_inr, 6),
            })
        return lines

    # ------------------------------------------------------------- claims

    def _claim_lines(self, claims: list[StorageClaim]) -> list[BillLine]:
        """The storage fee, finally paid.

        `grid/battery.py` has always documented "custodian is paid absorbed_kwh x
        storage_fee_inr_per_kwh" and folded that fee into the claim's cost basis,
        but no bill line ever moved the money: `storage_fee_inr` was hardcoded to
        0.0 on both sides of every trade. The owner of stored energy got custody
        for free and the custodian lent out its battery for nothing.

        Two lines per claim, equal and opposite, so this is invisible to ST1 —
        the fee moves between two households and the DISCOM collects none of it.
        """
        fee_rate = self.config.storage_fee_inr_per_kwh
        out: list[BillLine] = []
        for claim in sorted(claims, key=lambda c: c.claim_id):
            if claim.claim_id in self._settled_claims:
                continue           # a claim is billed once, when it is opened
            self._settled_claims.add(claim.claim_id)
            if claim.owner_id == claim.custodian_id:
                continue           # a house storing its own energy pays itself
            fee = round(claim.quantity_kwh * fee_rate, 9)
            if fee <= 0.0:
                continue
            out.append(BillLine(
                line_id=f"{claim.claim_id}:O", block=claim.opened_block,
                trade_id=claim.claim_id, house_id=claim.owner_id, role="owner",
                quantity_kwh=claim.quantity_kwh, loss_kwh=0.0,
                unit_price_inr=claim.cost_basis,
                energy_inr=0.0, transaction_inr=0.0, wheeling_inr=0.0,
                cross_subsidy_inr=0.0, storage_fee_inr=fee, ageing_inr=0.0,
                platform_inr=0.0, gst_inr=0.0, net_inr=fee))
            out.append(BillLine(
                line_id=f"{claim.claim_id}:C", block=claim.opened_block,
                trade_id=claim.claim_id, house_id=claim.custodian_id,
                role="custodian",
                quantity_kwh=claim.quantity_kwh, loss_kwh=0.0,
                unit_price_inr=claim.cost_basis,
                energy_inr=0.0, transaction_inr=0.0, wheeling_inr=0.0,
                cross_subsidy_inr=0.0, storage_fee_inr=-fee, ageing_inr=0.0,
                platform_inr=0.0, gst_inr=0.0, net_inr=-fee))
        for line in out:
            _assert_st2(line)
        return out

    def _adder(self, trade: Trade, ageing: AgeingResult | None) -> float:
        """C's ageing adder for the transformer this trade sits on.

        Computed in block t-1, applied in block t (HL4). `ageing.ageing_adder`
        is the ACTIVE adder — the one computed a block ago. It used to resolve to
        `AgeingResult.adders`, the figure computed from this block's own thermal
        state, which priced a trade using information that did not exist when it
        was struck. See engine/domain.py.
        """
        if ageing is None:
            return 0.0
        house = self.houses.get(trade.seller_id)
        if house is None:
            return 0.0
        return ageing.ageing_adder.get(house.transformer_id, 0.0)


def _assert_st1(lines: list[BillLine], collected: float) -> None:
    total = sum(line.net_inr for line in lines)
    if abs(total - collected) > 1e-6:
        raise InvariantError(
            f"ST1: net positions sum to Rs{total:.9f} but Rs{collected:.9f} was "
            f"collected — settlement is inventing rupees")


def _assert_st2(line: BillLine) -> None:
    if abs(sum(line.components) - line.net_inr) > 1e-9:
        raise InvariantError(
            f"ST2: {line.line_id} components sum to {sum(line.components):.9f}, "
            f"net_inr is {line.net_inr:.9f}")


def _assert_st3(lines: list[BillLine], trades: list[Trade],
                claims: list[StorageClaim] | None = None) -> None:
    """Every bill line traces to something real.

    Storage-fee lines have no trade — they belong to a StorageClaim — so the
    known set is trades plus claims. Passing claims is optional so the existing
    two-argument call sites (and the test that drives this directly) still mean
    exactly what they meant.
    """
    known = {t.trade_id for t in trades}
    known |= {c.claim_id for c in (claims or [])}
    for line in lines:
        if line.trade_id not in known:
            raise InvariantError(
                f"ST3: bill line {line.line_id} has no corresponding trade")
