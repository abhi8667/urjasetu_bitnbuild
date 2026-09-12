"""Agent pool — satisfies the runner's OrderSource protocol. Owner: B.

Holds one ProsumerAgent per PV premises and one ConsumerAgent per premises
(PRD §4), collects their orders each block, and fans `on_settled` back out.

Ordering is explicit everywhere. Dict iteration is stable in 3.7+, but nothing
that reaches output relies on it — orders are sorted by id before they leave.
"""
from __future__ import annotations

from engine.agents.consumer import ConsumerAgent
from engine.agents.prosumer import ProsumerAgent
from engine.config import Config
from engine.domain import BillLine, House, MeterTick, Order, StrategyParams, Trade


class AgentPool:
    def __init__(self, houses: list[House], config: Config, rng=None,
                 strategy: StrategyParams | None = None):
        self.config = config
        ordered = sorted(houses, key=lambda h: h.house_id)
        self.prosumers = {h.house_id: ProsumerAgent(h, config, rng, strategy)
                          for h in ordered if h.has_pv}
        self.consumers = {h.house_id: ConsumerAgent(h, config, rng, strategy)
                          for h in ordered}
        self.houses = ordered
        #: house_id -> kWh the last build() held back to charge a battery.
        self.battery_reserves: dict[str, float] = {}
        #: The strategy every agent is currently running. The LLM layer moves
        #: this once per simulated day via `set_strategy`; with the LLM off it
        #: stays at the declared defaults for the whole run, which is what PRD
        #: integration check 10 requires.
        self.strategy = strategy or StrategyParams()
        #: Clearing prices seen so far, the only history the LLM is given.
        self.price_history: list[float] = []

    def build(self, block: int, ticks: list[MeterTick], feed=None) -> list[Order]:
        orders: list[Order] = []
        selling: set[str] = set()
        self.battery_reserves = {}
        for house_id, prosumer in self.prosumers.items():
            reserved = prosumer.reserve_kwh(block, feed)
            if reserved > 0:
                self.battery_reserves[house_id] = round(reserved, 6)
            order = prosumer.build_offer(block, feed)
            if order is not None:
                orders.append(order)
                selling.add(house_id)
        for house_id, consumer in self.consumers.items():
            # A premises never bids for energy it is simultaneously offering —
            # otherwise the auction can match a house against itself.
            if house_id in selling:
                continue
            order = consumer.build_bid(block, feed)
            if order is not None:
                orders.append(order)
        return sorted(orders, key=lambda o: o.order_id)

    def set_strategy(self, strategy: StrategyParams) -> None:
        """Swap the strategy every agent trades on.

        Called once per simulated day by the runner, from `algo.llm`. The
        agents are otherwise unchanged — an LLM that is off, slow or wrong
        cannot do anything here except leave the previous parameters in place,
        which is exactly the defaults the engine ships on.
        """
        self.strategy = strategy
        for agent in (*self.prosumers.values(), *self.consumers.values()):
            agent.strategy = strategy

    def on_settled(self, block: int, ticks: list[MeterTick], trades: list[Trade],
                   clearing_price: float | None, bill_lines: list[BillLine]) -> None:
        if clearing_price is not None:
            self.price_history.append(clearing_price)
        by_house = {t.house_id: t for t in ticks}
        for house_id, prosumer in self.prosumers.items():
            prosumer.on_settled(block, [by_house[house_id]], trades, clearing_price)
        for house_id, consumer in self.consumers.items():
            consumer.on_settled(block, [by_house[house_id]], bill_lines)
