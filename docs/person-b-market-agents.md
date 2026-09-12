# Person B — Agents, Market Side

**Mission** The trading half of the engine, plus the infrastructure everything else plugs into. You own the tick loop, so you own the sequence.

**You are the seam.** D's algorithms and C's grid agents both reach the system through your loop. When something happens in the wrong order, you are the person who knows why.

---

## 1. What you own

```
config.py               pydantic config model, every default declared
domain.py               all dataclasses
bus.py                  event bus + ring buffer
persistence.py          SQLite, five tables
sim/runner.py           THE TICK LOOP
agents/prosumer.py      offers surplus
agents/consumer.py      bids for deficit
agents/market.py        wraps D's auction
agents/settlement.py    itemised billing
sim/baseline.py         the net-metering counterfactual
```

## 2. What you never touch

`grid/*` — that is C's. `algo/*` — that is D's. `ui/*` — that is A's.

You call D's functions; you do not read or modify them. You call C's agents through their interfaces; you do not reason about how a reshape is computed.

---

## 3. Hour 0–2: two handshakes, both yours

**With A — freeze the payload shapes.** `scene`, `block`, `event`, exactly as in UI PRD §4.3. Then write `fixtures/day-one.json` together: one simulated day of hand-made payloads. A is unblocked for twenty hours after this.

**With D — receive the signatures.** Confirm you can call `algo.auction.clear()` and `algo.forecast.ewma()` against stubs. Do not proceed until you have run both.

**Then publish `domain.py` immediately.** C cannot build a sentinel without knowing the shape of `Trade` and `MeterTick`. This is your first commit and it unblocks C.

---

## 4. The dataclasses — publish these first

```python
@dataclass(frozen=True)
class House:
    house_id: str; transformer_id: str; phase: Literal["A","B","C"]
    distance_m: float; has_pv: bool; pv_kw: float
    has_battery: bool; battery_kwh: float; battery_max_kw: float
    retail_tariff: float

@dataclass(frozen=True)
class Transformer:
    transformer_id: str; rating_kva: float
    rated_top_oil_rise_c: float; rated_hotspot_rise_c: float
    rated_life_hours: float; replacement_cost_inr: float

@dataclass(frozen=True)
class MeterTick:
    block: int; house_id: str
    load_kwh: float; gen_kwh: float; ambient_c: float

@dataclass(frozen=True)
class Order:
    order_id: str; block: int; house_id: str
    side: Literal["offer","bid"]; quantity_kwh: float; limit_price: float

@dataclass(frozen=True)
class Trade:
    trade_id: str; block: int; seller_id: str; buyer_id: str
    quantity_kwh: float; clearing_price: float; curtailed_fraction: float
```

Frozen throughout. If something needs to change, a new instance is created. This is what makes the tick loop debuggable.

---

## 5. Implementation order

| Order | Module | Target hour | Unblocks |
|---|---|---|---|
| 1 | `domain.py`, `config.py` | 2 | C and A |
| 2 | `bus.py` | 4 | A's transport |
| 3 | `sim/runner.py` skeleton | 6 | Your own testing |
| 4 | `agents/market.py` | 6 | Hour-6 gate |
| 5 | `agents/prosumer.py`, `agents/consumer.py` | 12 | Real order books |
| 6 | `agents/settlement.py` | 16 | Money conservation check |
| 7 | `persistence.py` | 18 | C's health state, restart test |
| 8 | `sim/baseline.py` | 20 | The compare screen |

---

## 6. Module specifications

### 6.1 `bus.py` — hour 4

```python
class Bus:
    def __init__(self):
        self._subs = defaultdict(list)
        self.ring = deque(maxlen=500)

    def subscribe(self, topic: str, fn): self._subs[topic].append(fn)

    def publish(self, topic: str, event: dict):
        self.ring.append(event)
        for fn in self._subs[topic]:
            fn(event)
```

Synchronous dispatch in subscription order. No asyncio, no queues, no broker.

**Event types, minimum set:** `block_opened`, `order_submitted`, `market_cleared`, `breach_detected`, `reshape_proposed`, `reshape_applied`, `fallback_curtailed`, `ageing_applied`, `block_settled`.

Every event carries `block`, `agent_id`, `payload`.

The ring buffer is what A reads. Nothing else in the UI touches the engine.

---

### 6.2 `sim/runner.py` — the tick loop

```python
for block in range(feed.total_blocks()):
    bus.publish("block_opened", {...})
    ticks   = feed.ticks(block)
    orders  = [a.build_offer(block, feed) for a in prosumers] \
            + [a.build_bid(block, feed)   for a in consumers]
    orders  = [o for o in orders if o is not None]

    result  = market.clear(orders)
    breach  = sentinel.check(result.trades, ticks)      # C's

    if breach:
        plan   = flow.reshape(result.trades, ticks, breach, batteries)  # C's
        result = market.clear(plan.constrained_orders)
        breach = sentinel.check(result.trades, ticks)
        if breach:
            result.trades = flow.fallback_curtail(result.trades, breach)

    ageing  = health.apply(result.trades, ticks)         # C's
    bills   = settlement.settle(result.trades, ageing)

    for a in prosumers + consumers:
        a.on_settled(block, result.trades, ...)

    persist(block, orders, result.trades, ageing, bills)
    bus.publish("block_settled", {...})
```

**Invariant P1.** At most 2 clearing passes per block. After the second failed check, `fallback_curtail` runs and its output is final. Hardcode the bound; do not make it configurable.

**Invariant P2.** No network I/O anywhere in this loop. SQLite writes only.

**Invariant P3.** Median tick under 50ms at 50 houses and 3 transformers. Measure from hour 20, not hour 34.

---

### 6.3 `agents/market.py` — hour 6

Thin. It wraps D's auction and publishes the event.

```python
class MarketAgent:
    def clear(self, orders) -> ClearingResult:
        result = algo.auction.clear(orders)
        self.bus.publish("market_cleared", {...})
        return result
```

Resist putting logic here. If you find yourself implementing matching, that belongs in D's file.

**Gate at hour 6:** a fixture order book of 5 offers and 5 bids clears at the analytically correct price.

---

### 6.4 `agents/prosumer.py` — hour 12

```python
class ProsumerAgent:
    def __init__(self, house, rng, strategy):
        self._soc = 0.0
        self._gen_hist = defaultdict(list)   # block_of_day -> values
        self._load_hist = defaultdict(list)
        self._price_hist = deque(maxlen=7*96)

    def build_offer(self, block, feed) -> Order | None:
        bod = block % feed.blocks_per_day
        gen  = algo.forecast.ewma(self._gen_hist[bod][-7:], alpha=0.3)
        load = algo.forecast.ewma(self._load_hist[bod][-7:], alpha=0.3)
        surplus = gen - load
        if surplus < config.min_order_kwh:
            return None
        floor = max(config.feed_in_tariff,
                    self._evening_ewma() * self.strategy.discount)
        return Order(side="offer", quantity_kwh=surplus, limit_price=floor, ...)

    def on_settled(self, block, trades, claims):
        # THE ONLY PLACE STATE MUTATES
```

Day 1 has no history — fall back to `feed.forecast()` entirely.

**Invariants**
- `PR1` `_soc` always in `[0, house.battery_kwh]`
- `PR2` offer quantity never exceeds forecast surplus plus owned claims available for discharge
- `PR3` `build_offer` is pure; call it twice, get the same answer

**Tests**
- Constant feed for 10 days → offer prices converge, variance under 2% across the last 3 days
- Zero generation → `None` every block
- PR1 holds across a 30-day run

---

### 6.5 `agents/consumer.py` — hour 12

```python
    def build_bid(self, block, feed) -> Order | None:
        deficit = max(0.0, load_forecast - gen_forecast)
        if deficit < config.min_order_kwh:
            return None
        ceiling = self.house.retail_tariff * (1 - self.strategy.margin)
        assert ceiling <= self.house.retail_tariff   # CN1, at construction
        return Order(side="bid", quantity_kwh=deficit, limit_price=ceiling, ...)
```

**Invariant CN1 — enforced.** A bid above retail tariff raises at construction. Not a warning, not a clamp. Raise.

**Invariant CN2 — asserted.** After every block, `cumulative_spend <= cumulative_baseline`. CN1 makes this true automatically, so CN2 should never fire. It exists to catch settlement bugs, not bidding bugs.

If CN2 ever fires, the bug is in §6.6, not here.

---

### 6.6 `agents/settlement.py` — hour 16

Itemised per trade. Never aggregate at source.

```python
energy        = qty * clearing_price
transaction   = qty * config.transaction_charge / 2      # each side. default 0.42 total
wheeling      = qty * config.wheeling_charge             # buyer only. default 1.01
cross_subsidy = qty * config.cross_subsidy if config.cross_subsidy_enabled else 0.0
storage_fee   = from C's claims
ageing        = qty * health.ageing_adder(transformer_id)
net           = sum of the above, signed
```

All six are config values. No literals.

**Invariants**
- `ST1` money conserved: sum of `net_inr` across all parties equals total non-energy charges collected, within 1e-6
- `ST2` each `BillLine`'s components sum to its `net_inr` within 1e-9
- `ST3` no bill line without a corresponding trade or claim

**Tests**
- One hand-computed trade produces exactly six expected components
- ST1 across a 30-day run
- Toggling `cross_subsidy_enabled` changes only that column

---

### 6.7 `sim/baseline.py` — hour 20. **Do not cut this.**

The counterfactual. Same meter feed, same seed, no trading.

- Surplus exported, credited 1:1 against consumption
- Credits carry forward `config.credit_carryforward_blocks`, then lapse
- No wheeling, no transaction charges
- Transformer loading computed from raw net export, breaches recorded but never acted on
- Shares D's thermal model so loss-of-life comparison is apples to apples

**Invariant BL2.** Baseline never mutates agent state.

**The check that matters:** baseline loss-of-life must be ≥ P2P loss-of-life over 30 days. If it is not, the ageing price signal is doing nothing and your central claim is false. Treat a failure here as blocking, not cosmetic.

This module produces the numbers on A's compare screen. Without it there is no compare screen, and without the compare screen the demo has no payoff.

---

### 6.8 `persistence.py` — hour 18

Five tables: `meter_tick`, `order_book`, `trade`, `bill_line`, `transformer_state`.

Only `transformer_state` survives restart. Write it **every block**, not at shutdown.

**Invariant PS1.** Hard-kill at block 500 of 2880, restart, finish. Cumulative loss of life matches an uninterrupted run within 1e-9. No gap, no double count.

---

## 7. Your hour-by-hour

| Hours | Work | Done when |
|---|---|---|
| 0–2 | Both handshakes; publish `domain.py`, `config.py` | C and A unblocked |
| 2–6 | `bus.py`, runner skeleton, `agents/market.py` | **Hour-6 gate: fixture book clears correctly** |
| 6–12 | Prosumer, consumer | Agents produce a full day of orders |
| 12–16 | Settlement | ST1 holds on a 1-day run |
| 16–20 | Persistence, **integration with C** | **Hour-20 gate: reshape resolves a 112% case** |
| 20–24 | Baseline | Baseline ≥ P2P loss of life |
| 24–28 | 30-day runs, run summary, tuning | Full suite green |
| 28–30 | Bug triage | — |
| 30–36 | Freeze, rehearse | — |

---

## 8. Your invariants, all in one place

| ID | Statement | Where enforced |
|---|---|---|
| D1 | Two identical runs → byte-identical `run_summary.json` | Runner |
| P1 | At most 2 clearing passes per block | Runner |
| P2 | No network I/O in the tick path | Runner |
| P3 | Median tick under 50ms | Runner, measured |
| PR1 | Battery SoC within capacity | Prosumer |
| PR3 | Decision methods pure | Prosumer, consumer |
| CN1 | Bid never above retail tariff | Consumer, raises |
| CN2 | Cumulative spend ≤ baseline | Consumer, asserted |
| ST1 | Money conserved | Settlement |
| BL2 | Baseline never mutates state | Baseline |
| PS1 | Loss of life survives restart exactly | Persistence |

Violations raise `InvariantError`. Nobody comments one out to get past a bug.

---

## 9. Determinism, which is your responsibility

One `numpy.random.Generator` created from `config.seed` at startup, passed explicitly to anything needing randomness. No module calls `random` or `np.random` directly.

Sorting everywhere has an explicit tie-break on id. Dict iteration order is stable in Python 3.7+, but do not rely on it for anything that reaches output — sort first.

**Test it early.** Run twice at hour 12 and diff the summaries. Finding a nondeterminism bug at hour 30 is very expensive.

---

## 10. Fallbacks

| If | Then |
|---|---|
| Hour-6 gate missed | Take D's auction stub permanently; it produces valid trades |
| Prosumer forecasting misbehaves | Use `feed.forecast()` directly, drop EWMA |
| Settlement money check fails | Halve the problem: disable cross-subsidy and storage fees, find it in the remaining four terms |
| Baseline slips past hour 24 | Escalate immediately. This is a never-cut item; pull D onto it. |
| Persistence slips | Keep state in memory; drop the restart test. Cheapest cut available. |

---

## 11. What you present

**The data provenance slide.** What is real and what is simulated, stated before anyone asks:

> Load profiles from NEEM smart meter data across 13 Indian cities. Appliance flexibility from iAWE, Delhi. Voltage behaviour calibrated against Prayas ESMI across 89 districts. Generation modelled with pvlib from live irradiance at our feeder's coordinates. Tariffs and charges from the actual DERC and UPERC pilot orders. Meters are replayed, not live — there is no open Indian dataset pairing household load with rooftop PV.

Saying the limitation yourself reads as rigour. Being caught on it reads as overclaiming.

**Questions you own**
- How the auction works and why uniform pricing
- How a household is protected from overpaying
- Where the data comes from and what is synthetic
- What net metering does today and why it breaks at scale
