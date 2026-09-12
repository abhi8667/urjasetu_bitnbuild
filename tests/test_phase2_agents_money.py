"""Phase 2 — prosumer, consumer, settlement.

Runs under pytest, or standalone with `python3 tests/test_phase2_agents_money.py`.
Stdlib only.
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.agents.consumer import ConsumerAgent
from engine.agents.market import MarketAgent
from engine.agents.prosumer import ProsumerAgent
from engine.agents.settlement import SettlementAgent
from engine.bus import Bus
from engine.config import Config
from engine.domain import (AgeingResult, InvariantError, MeterTick, Order,
                           StrategyParams, Trade)
from engine.feed import WhitefieldFeed
from engine.sim.pool import AgentPool
from engine.sim.runner import Runner

CONFIG = Config()
FEED = WhitefieldFeed(CONFIG)
HOUSES = {h.house_id: h for h in FEED.houses()}
PV_HOUSE = HOUSES["10006"]          # 15 kW rooftop, 10 kWh battery
LOAD_HOUSE = HOUSES["10000"]        # no PV


class StubFeed:
    """Constant output, so convergence can be measured without the dataset's
    daily variation confounding it."""

    def __init__(self, gen_kwh: float, load_kwh: float, blocks_per_day: int = 24):
        self.gen, self.load, self.bpd = gen_kwh, load_kwh, blocks_per_day

    def forecast(self, house_id, block, horizon):
        return [MeterTick(block, house_id, self.load, self.gen, 25.0)]

    def tick(self, house_id, block):
        return MeterTick(block, house_id, self.load, self.gen, 25.0)


def _pool_run(blocks=None, config=CONFIG, ageing=None):
    feed = WhitefieldFeed(config)
    pool = AgentPool(feed.houses(), config)
    settlement = SettlementAgent(feed.houses(), config, consumers=pool.consumers, feed=feed)
    runner = Runner(feed, pool, config, settlement=settlement)
    summary = runner.run(blocks=blocks)
    return runner, pool, settlement, summary


# ------------------------------------------------------------- prosumer

def test_zero_generation_never_produces_an_offer():
    agent = ProsumerAgent(PV_HOUSE, CONFIG)
    for block in range(24):
        if block < 6 or block > 18:
            assert agent.build_offer(block, FEED) is None


def test_pr3_build_offer_is_pure():
    """Call it twice, get the same answer. State moves only in on_settled."""
    agent = ProsumerAgent(PV_HOUSE, CONFIG)
    first = agent.build_offer(12, StubFeed(gen_kwh=6.0, load_kwh=1.0))
    second = agent.build_offer(12, StubFeed(gen_kwh=6.0, load_kwh=1.0))
    assert first == second


def test_pr2_offer_never_exceeds_forecast_surplus():
    feed = StubFeed(gen_kwh=6.0, load_kwh=1.0)
    agent = ProsumerAgent(PV_HOUSE, CONFIG)
    order = agent.build_offer(12, feed)
    assert order.quantity_kwh <= agent.forecast_surplus_kwh(12, feed) + 1e-9


def test_reserve_price_floors_at_the_feed_in_tariff():
    """No trade clears after 18:00 on this dataset until C's batteries can
    discharge into the evening, so the opportunity cost of selling now is the
    feed-in tariff — correctly, since that is the only other buyer."""
    agent = ProsumerAgent(PV_HOUSE, CONFIG)
    assert agent.reserve_price() == CONFIG.feed_in_tariff


def test_evening_prices_lift_the_reserve_price_once_they_exist():
    agent = ProsumerAgent(PV_HOUSE, CONFIG)
    tick = MeterTick(19, PV_HOUSE.house_id, 2.0, 0.0, 24.0)
    for block in (19, 43, 67):
        agent.on_settled(block, [tick], [], clearing_price=6.0)
    # D's real EWMA is iterative, so compare with a tolerance rather than
    # exactly: ewma([6,6,6]) is 5.999999999999999, not 6.0.
    assert abs(agent.reserve_price() - 6.0 * StrategyParams().discount) < 1e-9


def test_offers_converge_on_a_constant_feed():
    """Ten days of identical input: the last three days' offers vary by under
    2% of their mean."""
    feed = StubFeed(gen_kwh=6.0, load_kwh=1.0)
    agent = ProsumerAgent(PV_HOUSE, CONFIG)
    quantities = []
    for day in range(10):
        block = day * 24 + 12
        order = agent.build_offer(block, feed)
        quantities.append(order.quantity_kwh)
        agent.on_settled(block, [feed.tick(PV_HOUSE.house_id, block)], [], 4.0)
    tail = quantities[-3:]
    mean = sum(tail) / len(tail)
    assert max(abs(q - mean) for q in tail) / mean < 0.02


def test_pr1_soc_outside_capacity_raises():
    agent = ProsumerAgent(PV_HOUSE, CONFIG)
    tick = MeterTick(0, PV_HOUSE.house_id, 1.0, 0.0, 25.0)
    try:
        agent.on_settled(0, [tick], [], battery_delta_kwh=PV_HOUSE.battery_kwh + 1)
        raise AssertionError("PR1 did not fire")
    except InvariantError as exc:
        assert "PR1" in str(exc)


# ------------------------------------------------------------- consumer

def test_cn1_bid_never_exceeds_the_retail_tariff():
    agent = ConsumerAgent(LOAD_HOUSE, CONFIG)
    assert agent.bid_ceiling() < LOAD_HOUSE.retail_tariff


def test_cn1_raises_rather_than_clamping():
    """A negative margin would price a household above the grid. That raises —
    silently clamping hides the bug that produced it."""
    agent = ConsumerAgent(LOAD_HOUSE, CONFIG, strategy=StrategyParams(margin=-0.5))
    try:
        agent.bid_ceiling()
        raise AssertionError("CN1 did not fire")
    except InvariantError as exc:
        assert "CN1" in str(exc)


def test_no_deficit_produces_no_bid():
    agent = ConsumerAgent(LOAD_HOUSE, CONFIG)
    assert agent.build_bid(12, StubFeed(gen_kwh=9.0, load_kwh=1.0)) is None


def test_bids_are_capped_so_buyers_compete():
    """Supply is short in every block on this street; without a cap the first
    buyer to sort swallows the book."""
    agent = ConsumerAgent(LOAD_HOUSE, CONFIG)
    order = agent.build_bid(19, StubFeed(gen_kwh=0.0, load_kwh=50.0))
    assert order.quantity_kwh == CONFIG.max_bid_kwh_per_block


def test_cn2_fires_when_the_charge_stack_exceeds_retail():
    agent = ConsumerAgent(LOAD_HOUSE, CONFIG)
    try:
        agent.record_purchase(1.0, LOAD_HOUSE.retail_tariff + 0.01)
        raise AssertionError("CN2 did not fire")
    except InvariantError as exc:
        assert "CN2" in str(exc)


# ----------------------------------------------------------- settlement

def test_one_trade_produces_exactly_the_eight_expected_components():
    """2 kWh at Rs4.00.

    energy       2 x 4.00 = 8.00, buyer pays, seller receives
    transaction  2 x 0.42 = 0.84 total, split 0.42 each side
    wheeling     2 x 1.01 = 2.02, buyer only
    platform     2 x 0.25 = 0.50, buyer only
    gst          5% of (wheeling + buyer transaction + platform) = 5% of 2.94 = 0.147
    cross-subsidy, storage, ageing: zero here

    EIGHT components, not six. `platform_fee` and `gst_pct` were config values
    that settlement documented as part of its six and then never billed — there
    was no column for either and no code path that read them.

    Seller nets -8.00 + 0.42 = -7.58.
    Buyer nets 8.00 + 0.42 + 2.02 + 0.50 + 0.147 = 11.087.
    They sum to 3.507, which is exactly what the DISCOM collected — ST1.
    """
    agent = SettlementAgent(FEED.houses(), CONFIG, feed=FEED)
    seller, buyer = agent.settle([Trade("T1", 12, "10006", "10000", 2.0, 4.0, 0.0)])
    assert [round(c, 6) for c in seller.components] == [
        -8.0, 0.42, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    assert [round(c, 6) for c in buyer.components] == [
        8.0, 0.42, 2.02, 0.0, 0.0, 0.0, 0.5, 0.147]
    assert round(seller.net_inr, 6) == -7.58 and round(buyer.net_inr, 6) == 11.087
    assert round(agent.charges_collected, 6) == 3.507


def test_st1_money_is_conserved_over_a_30_day_run():
    _, _, settlement, _ = _pool_run()
    total = sum(line.net_inr for line in settlement.ledger)
    assert abs(total - settlement.charges_collected) < 1e-6


def test_st2_components_always_sum_to_net():
    _, _, settlement, _ = _pool_run(blocks=48)
    for line in settlement.ledger:
        assert abs(sum(line.components) - line.net_inr) < 1e-9


def test_st3_a_bill_line_without_a_trade_raises():
    agent = SettlementAgent(FEED.houses(), CONFIG, feed=FEED)
    trade = Trade("T1", 0, "10006", "10000", 1.0, 4.0, 0.0)
    lines = agent.settle([trade])
    from engine.agents.settlement import _assert_st3
    try:
        _assert_st3(lines, [])
        raise AssertionError("ST3 did not fire")
    except InvariantError as exc:
        assert "ST3" in str(exc)


def test_cross_subsidy_toggle_changes_only_that_column():
    trade = [Trade("T1", 0, "10006", "10000", 2.0, 4.0, 0.0)]
    off = SettlementAgent(FEED.houses(), CONFIG, feed=FEED).settle(trade)
    on_config = replace(CONFIG, cross_subsidy=0.5, cross_subsidy_enabled=True)
    on = SettlementAgent(FEED.houses(), on_config, feed=FEED).settle(trade)
    for a, b in zip(off, on):
        assert a.energy_inr == b.energy_inr
        assert a.wheeling_inr == b.wheeling_inr
        assert a.transaction_inr == b.transaction_inr
    assert sum(l.cross_subsidy_inr for l in on) == 1.0
    assert sum(l.cross_subsidy_inr for l in off) == 0.0


def test_ageing_adder_reaches_the_buyers_bill():
    """C's adder is priced into the trade, not bolted on afterwards.

    Note which field carries it. `adders` is the adder computed from the CURRENT
    block's thermal state, in force from t+1; `active_adders` is the one computed
    in t-1 and in force NOW. HL4 says the adder billed in block t was computed no
    later than t-1, so settlement reads `active_adders`. This test used to pass
    the figure as `adders` and assert it reached the bill — which is to say it
    asserted the HL4 violation.
    """
    ageing = AgeingResult(states=[], adders={"DT-1": 9.99},
                          active_adders={"DT-1": 1.5}, block=0)
    agent = SettlementAgent(FEED.houses(), CONFIG, feed=FEED)
    _, buyer = agent.settle([Trade("T1", 0, "10006", "10000", 2.0, 4.0, 0.0)], ageing)
    assert buyer.ageing_inr == 3.0, "the ACTIVE adder (t-1) must be the one billed"
    assert abs(sum(buyer.components) - buyer.net_inr) < 1e-9


def test_hl4_the_forward_adder_never_reaches_this_blocks_bill():
    """The complement of the test above: a figure present only in `adders`
    must NOT be billed, or the price signal is retroactive."""
    ageing = AgeingResult(states=[], adders={"DT-1": 2.0}, active_adders={}, block=0)
    agent = SettlementAgent(FEED.houses(), CONFIG, feed=FEED)
    _, buyer = agent.settle([Trade("T1", 0, "10006", "10000", 2.0, 4.0, 0.0)], ageing)
    assert buyer.ageing_inr == 0.0, (
        "HL4: an adder computed from THIS block's load was billed to THIS "
        "block's trades — retroactive pricing")


# ------------------------------------------------- market: the KERC rule

def test_trades_never_cross_transformers():
    """KERC 2024 permits P2P only within one DT — electricity cannot be routed
    meter-to-meter across transformers."""
    of = {h.house_id: h.transformer_id for h in FEED.houses()}
    feed = WhitefieldFeed(CONFIG)
    pool = AgentPool(feed.houses(), CONFIG)
    market = MarketAgent(Bus(), feed.houses(), CONFIG)
    checked = 0
    for block in range(9, 16):
        result = market.clear(block, pool.build(block, feed.ticks(block), feed))
        for trade in result.trades:
            assert of[trade.seller_id] == of[trade.buyer_id], (
                f"{trade.trade_id} crossed {of[trade.seller_id]} -> "
                f"{of[trade.buyer_id]}")
            checked += 1
    assert checked > 0, "no trades cleared, so nothing was checked"


def test_per_transformer_books_barely_cost_volume():
    """Enforcing the rule is nearly free here: 99% of surplus was already
    matchable inside its own transformer."""
    _, _, _, strict = _pool_run(blocks=240)
    loose = replace(CONFIG, enforce_same_transformer=False)
    _, _, _, flat = _pool_run(blocks=240, config=loose)
    assert flat["traded_kwh"] > 0
    assert abs(strict["traded_kwh"] - flat["traded_kwh"]) / flat["traded_kwh"] < 0.02


def test_a_flat_book_would_cross_transformers():
    """Documents why the rule needs enforcing: with one book, most energy
    crosses a transformer boundary."""
    loose = replace(CONFIG, enforce_same_transformer=False)
    feed = WhitefieldFeed(loose)
    of = {h.house_id: h.transformer_id for h in feed.houses()}
    pool = AgentPool(feed.houses(), loose)
    market = MarketAgent(Bus(), feed.houses(), loose)
    crossed = 0
    for block in range(9, 16):
        result = market.clear(block, pool.build(block, feed.ticks(block), feed))
        crossed += sum(1 for t in result.trades
                       if of[t.seller_id] != of[t.buyer_id])
    assert crossed > 0


# ------------------------------------------------------- the whole loop

def test_full_30_day_run_with_agents_and_settlement():
    runner, pool, settlement, summary = _pool_run()
    assert summary["blocks"] == 720
    assert summary["trades"] > 1000
    assert settlement.charges_collected > 0
    assert runner.median_tick_ms < 50.0


def test_d1_determinism_holds_with_real_agents():
    _, _, a, first = _pool_run(blocks=96)
    _, _, b, second = _pool_run(blocks=96)
    assert first == second
    assert [l.__dict__ for l in a.ledger] == [l.__dict__ for l in b.ledger]


def test_every_buyer_stays_under_its_retail_tariff():
    """CN2 in aggregate — and the headroom C's ageing adder has to fit into."""
    _, pool, _, _ = _pool_run()
    buyers = [c for c in pool.consumers.values() if c.kwh_bought > 0]
    assert buyers
    for consumer in buyers:
        all_in = consumer.cumulative_spend / consumer.kwh_bought
        assert all_in < consumer.house.retail_tariff


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
