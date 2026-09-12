"""Settlement agent — itemised billing. Owner: B.

Six components per trade, every one a config value and none a literal. Never
aggregate at source: ST1 is only checkable if each term is visible, and the
DISCOM ledger screen *is* these columns.

Sign convention on `net_inr` is from the party's point of view — positive pays,
negative receives. Energy cancels between buyer and seller, so summing every
party's net position leaves exactly the charges the DISCOM collected. That is
ST1, and it falls out of the convention rather than being computed separately.
"""
from __future__ import annotations

from engine.bus import Bus
from engine.config import Config
from engine.domain import (AgeingResult, BillLine, House, InvariantError, Trade)

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

    def settle(self, trades: list[Trade], ageing: AgeingResult | None = None,
               block: int | None = None) -> list[BillLine]:
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
            ageing_inr = qty * self._adder(trade, ageing)

            seller = BillLine(
                line_id=f"{trade.trade_id}:S", block=trade.block, trade_id=trade.trade_id,
                house_id=trade.seller_id, role="seller",
                quantity_kwh=qty, loss_kwh=0.0,
                unit_price_inr=trade.clearing_price,
                energy_inr=-energy, transaction_inr=transaction_half,
                wheeling_inr=0.0, cross_subsidy_inr=0.0, storage_fee_inr=0.0,
                ageing_inr=0.0,
                net_inr=-energy + transaction_half)
            buyer = BillLine(
                line_id=f"{trade.trade_id}:B", block=trade.block, trade_id=trade.trade_id,
                house_id=trade.buyer_id, role="buyer",
                quantity_kwh=round(delivered, 9), loss_kwh=round(loss_kwh, 9),
                unit_price_inr=trade.clearing_price,
                energy_inr=energy, transaction_inr=transaction_half,
                wheeling_inr=wheeling, cross_subsidy_inr=cross_subsidy,
                storage_fee_inr=0.0, ageing_inr=ageing_inr,
                net_inr=energy + transaction_half + wheeling + cross_subsidy + ageing_inr)

            for line in (seller, buyer):
                _assert_st2(line)
                lines.append(line)
            # Everything except energy leaves the participants for the DISCOM.
            # The storage fee is the exception — it moves between two households,
            # so it nets to zero and is not collected by anyone.
            collected += 2 * transaction_half + wheeling + cross_subsidy + ageing_inr

            consumer = self.consumers.get(trade.buyer_id)
            if consumer is not None:
                # Delivered, not injected: the buyer got less than was sent, and
                # CN2 must compare what it actually received against what that
                # much energy would have cost from the grid.
                consumer.record_purchase(delivered, buyer.net_inr)

        _assert_st1(lines, collected)
        _assert_st3(lines, trades)

        self.ledger.extend(lines)
        self.charges_collected += collected
        if self.bus is not None and block is not None:
            self.bus.publish("block_settled", block, AGENT_ID, {
                "bill_lines": len(lines),
                "charges_collected_inr": round(collected, 6),
            })
        return lines

    def _adder(self, trade: Trade, ageing: AgeingResult | None) -> float:
        """C's ageing adder for the transformer this trade sits on.

        Computed in block t, applied in block t+1 (HL4) — the health agent hands
        over an already-lagged figure, so settlement never lags it again.
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


def _assert_st3(lines: list[BillLine], trades: list[Trade]) -> None:
    known = {t.trade_id for t in trades}
    for line in lines:
        if line.trade_id not in known:
            raise InvariantError(
                f"ST3: bill line {line.line_id} has no corresponding trade")
