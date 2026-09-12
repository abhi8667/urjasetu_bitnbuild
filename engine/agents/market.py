"""Market agent. Owner: B.

Deliberately thin: it wraps D's auction and publishes the event. If you find
yourself implementing matching here, it belongs in `engine/algo/auction.py`.
"""
from __future__ import annotations

from engine import algo
from engine.bus import Bus
from engine.config import DEFAULT, Config
from engine.domain import ClearingResult, House, InvariantError, Order, Trade

AGENT_ID = "market"


class MarketAgent:
    """Wraps D's auction. The only logic here is *which book* clears — the
    matching itself is D's, and putting any of it in this file is a mistake.

    `eligible_trade_partners` in the device registry encodes a physical fact:
    electricity cannot be routed meter-to-meter across transformers, so KERC's
    2024 P2P regulations permit trades only within one DT. A single flat book
    ignores that and cross-matches freely — on this street that was 76% of
    traded energy. So each transformer clears its own book and the results are
    merged.

    The merged `clearing_price` is the volume-weighted mean across books, which
    is a reporting figure only: every trade still settles at its own book's
    uniform price, which is what MK2 checks.
    """

    def __init__(self, bus: Bus, houses: list[House] | None = None,
                 config: Config = DEFAULT):
        self.bus = bus
        self.config = config
        self._transformer_of = {h.house_id: h.transformer_id for h in (houses or [])}

    def clear(self, block: int, orders: list[Order]) -> ClearingResult:
        if self.config.enforce_same_transformer and self._transformer_of:
            result = self._clear_per_transformer(orders)
        else:
            result = algo.auction.clear(orders)
        _assert_market_invariants(orders, result)
        self._publish(block, result)
        return result

    def apply_reshape(self, block: int, orders: list[Order],
                      trades: list[Trade]) -> ClearingResult:
        """Accept the flow agent's LP decision as the cleared book.

        The runner used to re-run `clear()` over the reshape's constrained
        orders. That was wrong: a uniform-price auction sorts by price and walks
        the book, and the reshape prices every constrained pair identically, so
        the auction rematched seller A's retained energy against buyer B's bid
        at will. The LP's per-trade allocation — which is precisely what
        satisfies the loading and voltage rows it solved — was discarded before
        it reached the grid, and the re-check then measured a different street
        than the one the LP made feasible.

        So the trades come through untouched. What `clear()` was really
        providing here was the MK1/MK2 assertions, and those still run.
        """
        clearing_price = _volume_weighted_price(trades)
        result = ClearingResult(
            trades=sorted(trades, key=lambda t: t.trade_id),
            clearing_price=clearing_price,
            unmatched_offers=[],
            unmatched_bids=[],
        )
        _assert_market_invariants(orders, result)
        self._publish(block, result, reshaped=True)
        return result

    def _publish(self, block: int, result: ClearingResult,
                 reshaped: bool = False) -> None:
        self.bus.publish("market_cleared", block, AGENT_ID, {
            "clearing_price": result.clearing_price,
            "trades": len(result.trades),
            "volume_kwh": round(sum(t.quantity_kwh for t in result.trades), 6),
            "unmatched_offers": len(result.unmatched_offers),
            "unmatched_bids": len(result.unmatched_bids),
            "reshaped": reshaped,
        })

    def _clear_per_transformer(self, orders: list[Order]) -> ClearingResult:
        books: dict[str, list[Order]] = {}
        for order in orders:
            tid = self._transformer_of.get(order.house_id)
            if tid is None:
                raise InvariantError(
                    f"order {order.order_id} from unknown premises {order.house_id}")
            books.setdefault(tid, []).append(order)

        trades, unmatched_offers, unmatched_bids = [], [], []
        weighted, volume = 0.0, 0.0
        for tid in sorted(books):
            book = algo.auction.clear(books[tid])
            trades.extend(book.trades)
            unmatched_offers.extend(book.unmatched_offers)
            unmatched_bids.extend(book.unmatched_bids)
            if book.clearing_price is not None:
                qty = sum(t.quantity_kwh for t in book.trades)
                weighted += book.clearing_price * qty
                volume += qty
        return ClearingResult(
            trades=sorted(trades, key=lambda t: t.trade_id),
            clearing_price=round(weighted / volume, 6) if volume else None,
            unmatched_offers=sorted(unmatched_offers, key=lambda o: o.order_id),
            unmatched_bids=sorted(unmatched_bids, key=lambda o: o.order_id),
        )


def _volume_weighted_price(trades: list[Trade]) -> float | None:
    """Reporting figure only, exactly as in `_clear_per_transformer`: each trade
    keeps its own book's uniform price, and this is their volume-weighted mean."""
    volume = sum(t.quantity_kwh for t in trades)
    if volume <= 0:
        return None
    return round(sum(t.clearing_price * t.quantity_kwh for t in trades) / volume, 6)


def _assert_market_invariants(orders: list[Order], result: ClearingResult) -> None:
    """MK1 and MK2, checked here rather than in D's file.

    D owns the algorithm; B owns the guarantee that what came back is usable.
    Checking on this side means a bad upgrade to the auction surfaces at the
    call site, with the order book in hand.
    """
    traded = sum(t.quantity_kwh for t in result.trades)
    offered = sum(o.quantity_kwh for o in orders if o.side == "offer")
    bid = sum(o.quantity_kwh for o in orders if o.side == "bid")
    if traded > min(offered, bid) + 1e-9:
        raise InvariantError(
            f"MK1: traded {traded:.6f} kWh exceeds min(offered {offered:.6f}, "
            f"bid {bid:.6f})")

    if not result.trades:
        return
    if result.clearing_price is None:
        raise InvariantError("MK2: trades exist but clearing_price is None")

    # Keyed by (house_id, side), not by house_id alone. A single house can hold
    # both an offer and a bid in one book — it cannot today, because AgentPool
    # suppresses a bid from a house that is already selling, but the reshape's
    # constrained orders have no such rule. With a house_id-only dict the second
    # order silently replaced the first and MK2 was then checked against the
    # wrong side's limit: a latent false pass, and a possible false failure.
    offers = {o.house_id: o for o in orders if o.side == "offer"}
    bids = {o.house_id: o for o in orders if o.side == "bid"}
    for trade in result.trades:
        # Every trade settles at its own book's uniform price. With one book that
        # is result.clearing_price; with per-transformer books the merged figure
        # is a volume-weighted mean, so the check that bites is the pair of limit
        # bounds below.
        seller, buyer = offers.get(trade.seller_id), bids.get(trade.buyer_id)
        if seller and seller.limit_price > trade.clearing_price + 1e-9:
            raise InvariantError(
                f"MK2: seller {trade.seller_id} floor {seller.limit_price} above "
                f"its clearing price {trade.clearing_price}")
        if buyer and buyer.limit_price < trade.clearing_price - 1e-9:
            raise InvariantError(
                f"MK2: buyer {trade.buyer_id} ceiling {buyer.limit_price} below "
                f"its clearing price {trade.clearing_price}")
