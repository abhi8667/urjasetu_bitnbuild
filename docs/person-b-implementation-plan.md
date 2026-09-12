# Track B — Market Agents and the Tick Loop: implementation plan

Reviewed against `docs/person-b-market-agents.md`, `docs/urjasetu-prd.md`
§6.1–6.3 / §6.7–6.8, `DECISIONS.md`, and the code on `main`.

Track B owns the **trading half** plus the infrastructure everything else plugs
into. You own the tick loop, so you own the sequence — and the sequence is the
system's argument: economics proposes, physics disposes, money follows physics.

---

## What is already done

Your hour 0–2 deliverables are on `main`. C and A are unblocked.

| Shipped | Contains |
|---|---|
| `engine/domain.py` | Contract 3, frozen, plus the result types |
| `engine/config.py` | every default; hourly time base; rating override |
| `engine/feed.py` | the locked dataset as a `MeterFeed`, 720 blocks |
| `engine/algo/*.py` | D's Contract 1 stubs, callable today |
| `tests/test_feed_contracts.py` | 12 tests, stdlib only |

So you start at `bus.py`, with real ticks rather than fixtures.

---

## Fix these before you write a line

**1. Your provenance slide (§11 of your doc) describes a dataset that does not
exist.** It names NEEM, iAWE, Prayas ESMI, pvlib, and DERC/UPERC orders. None of
that is in this repo. Reading it aloud is an overclaim on a question you own.
Replace it from `DECISIONS.md` D9:

> Tariffs and P2P charges are KERC's published 2024–25 orders, used verbatim.
> Load, irradiance and weather are physically modelled from published monthly
> averages for Bangalore — no open Indian dataset pairs household load with
> rooftop PV at meter level, so we model generation rather than claim to
> measure it. Sixty premises on four transformers in Whitefield, real
> coordinates, one measured day replayed and seasonally scaled across thirty.

**2. Cadence constants in your code sketches are stale.** A block is an hour:

| Your doc | Correct |
|---|---|
| `deque(maxlen=7*96)` | `maxlen=7*24` |
| evening blocks 68–84 | `config.evening_blocks` = (17, 21) |
| PS1 "block 500 of 2880" | block 500 of **720** |

Never write a block literal. `config.blocks_per_day`, `config.block_hours`.

**3. `StrategyParams` has no `margin`.** Your consumer sketch uses
`strategy.margin`; the dataclass ships `discount`, `battery_reserve_frac`,
`bid_aggression`. Add `margin: float = 0.10` and tell D, who returns it from
`algo.llm.daily_strategy`.

**4. C needs two additions to your files at hour 0.** Both are `domain.py`,
which is yours:

- `AgeingResult` — what `health.apply()` returns. If C defines it too, you get
  two incompatible shapes at hour 20.
- `ThermalParams.R = 5.0` — the C57.91 oil resistance ratio. The dataclass has
  `n`, `m`, `tau_oil_hours` but not `R`, and `hotspot_c` needs it.

---

# Phase 1 — Infrastructure and the seam (hours 2–6)

**Goal: the loop runs end to end on real data, clearing real orders.**

### `bus.py` (hour 4)

`defaultdict(list)` plus a `deque(maxlen=500)`. Synchronous dispatch in
subscription order. No asyncio, no queues, no broker.

Nine event types minimum: `block_opened`, `order_submitted`, `market_cleared`,
`breach_detected`, `reshape_proposed`, `reshape_applied`, `fallback_curtailed`,
`ageing_applied`, `block_settled`. Every event carries `block`, `agent_id`,
`payload`.

**The ring buffer is A's only window into the engine.** Publish faithfully even
when it feels noisy — and never let the UI reach into engine state, or replay
stops working and the demo loses its safety net.

### `sim/runner.py` skeleton (hour 6)

Wire the full sequence with C's calls stubbed to `None`/identity so it runs on
day one:

```
ticks → orders → clear → check → [reshape → re-clear → fallback] → age → settle
```

**Hardcode the 2-pass bound (P1).** Do not make it configurable, or someone
raises it at hour 29 to "fix" a bug.

### `agents/market.py` (hour 6) — **your gate**

Thin. Wrap `algo.auction.clear()`, publish `market_cleared`, return. If you are
writing matching logic, it belongs in D's file.

> **Gate:** a fixture book of 5 offers and 5 bids clears at the analytically
> correct price. If it slips, take D's stub permanently — it produces valid
> trades — and keep moving.

**Phase 1 done when:** the runner completes 720 blocks without raising, and the
ring buffer holds plausible events.

---

# Phase 2 — Agents and money (hours 6–16)

**Goal: real order books, and rupees that add up.**

### `agents/prosumer.py` + `agents/consumer.py` (hour 12)

EWMA over the same block-of-day across the last 7 days, blended with the feed's
forecast; day 1 falls back to `feed.forecast()` entirely. Seller floor is
`max(config.feed_in_tariff, evening_ewma × strategy.discount)`. Buyer ceiling is
`retail_tariff × (1 - strategy.margin)`.

What the dataset will do to you:

- **Only 18 of 64 nodes ever generate, and only in hours 09–15.** Your
  prosumers are silent 70% of the day. That is correct, not a bug.
- **Supply is scarce: 195 kWh of surplus against 2060 kWh of demand.** Every
  block is supply-constrained, so all offers clear and the price pins to the
  offer side. `config.max_bid_kwh_per_block` (3.0) makes buyers compete for
  scarce surplus instead of the first bid swallowing the book. Tune it once you
  can see a clearing price, and record the number in `DECISIONS.md`.
- Retail tariffs are ₹7.18–8.40 (stepped BESCOM slabs), so CN1 leaves plenty of
  headroom over P2P prices around ₹3–7.

`PR3` — `build_offer` is pure. Call it twice, get the same answer. State mutates
only in `on_settled`. `CN1` **raises**; it does not clamp or warn.

### `agents/settlement.py` (hour 16)

Six itemised components per trade, every one a config value, none a literal:
energy, transaction (split both sides), wheeling (buyer only), cross-subsidy,
storage fee, ageing. **Never aggregate at source** — ST1 is only checkable if
you can see each term, and the DISCOM ledger screen *is* those columns.

Losses are reachable now:

```python
loss_pct = feed.transmission_loss_pct(house_id)   # 3.25–6.75%, per premises
```

FL4 has no losses term without it, and a flat percentage makes the invariant
meaningless.

### Determinism, at hour 12 — not hour 30

One `numpy.random.Generator` from `config.seed`, created in the runner, passed
explicitly. No module calls `random` or `np.random`. Sort everything reaching
output with an explicit id tie-break. **Run twice and diff the summaries.**
Finding a nondeterminism bug at hour 30 is very expensive.

**Phase 2 done when:** ST1 holds across a one-day run, and two runs diff clean.

---

# Phase 3 — Integration and durability (hours 16–20)

**Goal: the hour-20 gate, which is the project.**

### `persistence.py` (hour 18)

Five tables: `meter_tick`, `order_book`, `trade`, `bill_line`,
`transformer_state`. Only `transformer_state` survives restart, written **every
block**, not at shutdown.

`PS1`: hard-kill at block 500 of 720, restart, finish. Cumulative loss of life
matches an uninterrupted run within 1e-9 — no gap, no double count.

This is the cheapest cut on your list if you are behind: keep state in memory,
drop the restart test.

### Integration with C (hours 16–20)

Unstub the sentinel and flow agent calls. You own the sequence, so you own what
goes wrong here.

> **Gate:** a breaching block reshapes to ≤ 100% within the 2-pass bound. Real
> cases are free — the feed breaches 162 blocks in 720, all in 18:00–21:00.

When the reshape misbehaves — likeliest bad afternoon of the weekend — the
question "D's LP, C's constraints, or B's trade list?" gets answered by you,
from the event stream. Which is why the bus was Phase 1 and not an afterthought.

**One cross-track trap, at hour 24, with C.** A buyer at ₹4.00 clearing pays
4.00 + 1.01 wheeling + 0.21 transaction + C's ageing adder, against a ₹7.18–8.40
retail tariff. A full ₹2.00 adder lands at ≈₹7.22 — under, but not by much. If
the adder ever pushes a buyer above retail, your CN2 fires and the bug will look
like yours when it is C's. Check the corridor together.

**Phase 3 done when:** the hour-20 gate passes and a 30-day run completes.

---

# Phase 4 — The proof (hours 20–28)

**Goal: numbers that survive a judge.**

### `sim/baseline.py` (hour 20). **Never cut.**

Same feed, same seed, no trading. Surplus exported and credited 1:1, credits
carried `config.credit_carryforward_blocks` then lapsed, no wheeling, no
transaction charges. Transformer loading from raw net export — breaches recorded
but never acted on. Shares D's thermal model so loss-of-life is apples to apples.

`BL2`: baseline never mutates agent state.

> **The check that matters:** baseline loss of life must exceed P2P loss of life
> over 30 days. If it does not, the ageing price signal is doing nothing and the
> project's central claim is false. **Run it at hour 24, not hour 33.** If this
> slips past hour 24, escalate and pull D onto it.

### 30-day runs and the run summary (hours 24–28)

`run_summary.json` is what D1 asserts byte-identical across two runs. Flag
perfect-foresight mode in it if `forecast_noise_frac` is 0.

**Lead with the right number.** P2P covers about 9.5% of daily demand on this
data, so household bill savings will be modest. Transformer life saved and
DISCOM revenue (₹1.43/kWh on energy it previously earned nothing on) are where
you are strong. Decide what goes on screen largest **before** A builds the
compare layout — `DECISIONS.md` D10.

**Phase 4 done when:** the three checks pass — money conserved (ST1), energy
conserved (FL4), baseline ages faster than P2P — and a 30-day run finishes in
under 3 minutes wall clock.

---

## Your invariants, all in one place

| ID | Statement | Phase |
|---|---|---|
| D1 | Two identical runs → byte-identical `run_summary.json` | 2 |
| P1 | At most 2 clearing passes per block | 1 |
| P2 | No network I/O in the tick path | 1 |
| P3 | Median tick under 50ms at 64 nodes | 3 |
| PR1 | Battery SoC within capacity | 2 |
| PR3 | Decision methods pure | 2 |
| CN1 | Bid never above retail tariff — **raises** | 2 |
| CN2 | Cumulative spend ≤ baseline | 2 |
| ST1 | Money conserved | 2 |
| BL2 | Baseline never mutates state | 4 |
| PS1 | Loss of life survives restart exactly | 3 |

Violations raise `InvariantError`. Nobody comments one out to get past a bug —
the assertion *is* the bug report.

When ST1 fails, halve the space: disable cross-subsidy and storage fees, find it
in the remaining four terms. Do not debug six components at once at hour 27.

---

## Your first 90 minutes

1. `git pull origin main && python3 tests/test_feed_contracts.py` → 12/12
2. Add `margin` to `StrategyParams`; add `AgeingResult` and `ThermalParams.R`
   for C. Tell C and D in the same message.
3. Rewrite the provenance slide from D9 while it is fresh.
4. Write `bus.py`.
5. Handshake with A: freeze `scene` / `block` / `event`, then generate
   `fixtures/day-one.json` **from the feed** — `feed.sites()` gives A real
   coordinates and building types. Hand-writing it now is building against
   fiction when real output is one import away.

---

## Questions you own

- How the auction works, and why uniform pricing rather than pay-as-bid
- How a household is protected from overpaying
- Where the data comes from and what is modelled
- What net metering does today, and why it breaks at scale
