# Person D — Algorithms

**Mission** Every stateless computation in UrjaSetu. Pure functions, no state, no I/O, no agent knowledge.

**You are upstream of B and C.** Your first deliverable is not code that works — it is *signatures that never change*, backed by stubs that let two other people start building at hour 2.

---

## 1. What you own

```
algo/auction.py       uniform-price double auction
algo/reshape_lp.py    linear program: constraint-feasible trade reshaping
algo/thermal.py       IEEE C57.91 hot-spot temperature and insulation ageing
algo/powerflow.py     linearised LinDistFlow voltage deviation
algo/forecast.py      EWMA forecasting
algo/risk.py          LightGBM transformer risk ranking
algo/llm.py           three LLM call sites with fallbacks
tests/algo/           unit tests for all of the above
tests/grid/           unit tests for C's modules (see §9)
```

## 2. What you never touch

Agent state. Orchestration. The tick loop. The UI. Persistence.

Your functions do not know what an agent is. If a function needs to remember something between calls, it is in the wrong file — hand it to B or C.

---

## 3. Hour 0–2: the only deadline that cannot slip

Write every signature. Ship a stub for each. Commit. Tell B and C.

```python
# algo/auction.py
def clear(orders: list[Order]) -> ClearingResult:
    """STUB: greedy price-time match, no uniform pricing."""

# algo/reshape_lp.py
def solve(trades, limits, batteries, topology) -> ReshapeSolution:
    """STUB: uniform curtailment to bring loading to limit."""

# algo/thermal.py
def hotspot_c(load_kva, rating_kva, ambient_c, params) -> float:
    """STUB: ambient + 40 * (load/rating)."""
def ageing_factor(hotspot_c) -> float:
    """STUB: 1.0."""

# algo/powerflow.py
def voltage_dev(house_id, net_kw_by_house, topology) -> float:
    """STUB: 0.0."""

# algo/forecast.py
def ewma(history: list[float], alpha: float) -> float:
    """STUB: history[-1] if history else 0.0."""

# algo/risk.py
def rank(states: list[TransformerState]) -> list[tuple[str, float]]:
    """STUB: sort by life_used_frac descending."""

# algo/llm.py
def daily_strategy(weather, price_history) -> StrategyParams:
    """STUB: return defaults."""
def diagnose(breach, transformer_state) -> str:
    """STUB: templated string."""
```

**Rule.** After hour 2, a signature changes only by announcement, with B and C adapting in the same sitting. Everything else you do is upgrading a stub in place.

**If you deliver nothing else, the project still ships on these stubs.** That is the point of them.

---

## 4. Implementation order and why

| Order | Module | Why here |
|---|---|---|
| 1 | `auction.py` | B's hour-6 gate depends on it |
| 2 | `forecast.py` | B's agents need it by hour 6 |
| 3 | `powerflow.py` | C's sentinel needs it by hour 12 |
| 4 | `reshape_lp.py` — curtailment only | C's hour-20 gate, the project's critical path |
| 5 | `thermal.py` | C's health agent, hour 16 |
| 6 | `reshape_lp.py` — battery extension | C's batteries, hour 20 |
| 7 | `risk.py` | Cuttable |
| 8 | `llm.py` | Cuttable |

Items 7 and 8 are on the cut list. Do not start them before item 6 is proven.

---

## 5. Module specifications

### 5.1 `algo/auction.py` — target hour 6

Uniform-price double auction.

```python
def clear(orders: list[Order]) -> ClearingResult:
    offers = sorted([o for o in orders if o.side == "offer"],
                    key=lambda o: (o.limit_price, o.order_id))
    bids   = sorted([o for o in orders if o.side == "bid"],
                    key=lambda o: (-o.limit_price, o.order_id))
    # walk both while bids[j].limit_price >= offers[i].limit_price
    # partial fills permitted: split the larger, carry remainder in-block
    # clearing_price = midpoint of the LAST matched pair's limit prices
    # every trade settles at clearing_price, not at its own limit
```

**Invariants**
- `MK1` total traded ≤ min(total offered, total bid)
- `MK2` for every trade: `seller.limit ≤ clearing_price ≤ buyer.limit`
- `MK3` identical order lists → identical trade lists, including trade_ids

**Tests**
- Hand-built 5×5 book against an analytically computed clearing price
- Empty book returns empty result, does not raise
- Input shuffled → identical output after tie-break sort
- One offer, one bid, no overlap → no trades, `clearing_price is None`
- Partial fill: one 5 kWh offer against two 3 kWh bids

---

### 5.2 `algo/forecast.py` — target hour 6

```python
def ewma(history: list[float], alpha: float = 0.3) -> float:
    """Exponentially weighted mean, most recent weighted highest.
       Empty history returns 0.0. Single value returns that value."""
```

The caller supplies same-block-of-day history; you do not know about blocks or days.

**Tests**
- Constant history returns that constant
- Alpha = 1.0 returns the last value exactly
- Empty and single-element cases

---

### 5.3 `algo/powerflow.py` — target hour 12

Linearised LinDistFlow. **Do not implement Newton-Raphson.** At 50 nodes it is unnecessary and will cost you six hours.

```python
def voltage_dev(house_id, net_kw_by_house, topology) -> float:
    """Per-unit voltage deviation from nominal at this house.
       Sum over the path from transformer to house of
         (R_seg * P_downstream + X_seg * Q_downstream) / V_nom^2
       Q approximated from a configured power factor.
       Segment R and X derived from distance_m and per-metre constants."""
```

Topology gives you, per house: its transformer, its `distance_m`, and the per-metre R and X from config.

Simplification you are allowed to make, and should: treat each house as radially connected to its transformer by a single segment of length `distance_m`. A true multi-segment feeder tree is more accurate and not worth the hours.

**Tests**
- Single house, zero load → deviation 0.0
- Deviation scales linearly with net power at fixed distance
- Deviation scales linearly with distance at fixed power
- A hand-computed two-house case matches to within 1%
- Reversed power flow (export) produces a positive deviation, import negative

---

### 5.4 `algo/reshape_lp.py` — target hour 20. **The critical path.**

Build this in two passes: curtailment-only first, then batteries.

**Pass 1 — curtailment only**

```python
def solve(trades, limits, batteries=None, topology=None) -> ReshapeSolution:
    # variables: c_t in [0, 1] for each trade — fraction retained
    # objective: maximise sum(c_t * qty_t * price_t)
    # constraints per transformer:
    #   sum of net_kw as a function of c  <=  rating * loading_limit
    #   per-phase net within phase_limit of the mean
    #   |voltage_dev_i(c)|  <=  voltage_band   for each house
    # solve with scipy.optimize.linprog, method="highs"
```

`linprog` minimises, so negate the objective. All constraints go in `A_ub`/`b_ub`. Variable bounds are `[(0, 1)] * n_trades`.

**Pass 2 — battery extension**

Add variables `b_h ≥ 0` for each house with a battery — kWh absorbed this block.

```
objective: maximise  sum(c_t * qty_t * price_t) - sum(b_h * storage_fee)
new constraints:
    b_h <= house.battery_max_kw * 0.25          # power limit over 15 min
    b_h <= house.battery_kwh - soc_h            # capacity headroom
```

Battery absorption enters the transformer loading constraint as additional local consumption, which is what relieves the breach.

**Return shape**

```python
@dataclass(frozen=True)
class ReshapeSolution:
    retention: dict[str, float]     # trade_id -> c_t
    battery_charge: dict[str, float] # house_id -> kWh
    feasible: bool
    objective_value: float
```

**You do not create StorageClaims.** You return numbers; C turns them into claims. Ownership is agent state, not algorithm.

**Infeasibility.** If `linprog` reports infeasible, return `feasible=False` with an empty solution. C handles the fallback. Never raise.

**Invariants**
- `FL_A` every `retention` value is in `[0, 1]`
- `FL_B` every `battery_charge` value is ≥ 0 and within both limits
- `FL_C` a solution with `feasible=True` satisfies every constraint to within 1e-6

**Tests**
- A hand-built 112% loading case reduces to ≤ 100%
- A case already within limits returns all retention = 1.0
- All batteries full → curtailment-only solution, still feasible
- Deliberately contradictory constraints → `feasible=False`, no raise
- Solution time under 20ms at 50 trades and 50 batteries — measure it

---

### 5.5 `algo/thermal.py` — target hour 16

IEEE C57.91. Purely algebraic. No iteration, no ODE.

```python
def hotspot_c(load_kva, rating_kva, ambient_c, p: ThermalParams) -> float:
    K    = load_kva / rating_kva
    dTO  = p.rated_top_oil_rise * ((K**2 * p.R + 1) / (p.R + 1)) ** p.n
    dH   = p.rated_hotspot_rise * K ** (2 * p.m)
    return ambient_c + dTO + dH

def ageing_factor(hotspot_c: float) -> float:
    """Arrhenius. 1.0 at 110C reference for 65C-rise insulation."""
    return math.exp(15000 / 383 - 15000 / (hotspot_c + 273))

def loss_of_life_hours(hotspot_c: float, block_hours: float = 0.25) -> float:
    return ageing_factor(hotspot_c) * block_hours
```

Defaults: `R = 5.0`, `n = 0.8`, `m = 0.8`, `rated_top_oil_rise = 55.0`, `rated_hotspot_rise = 25.0`. All configurable, none hardcoded at a call site.

**Tests**
- At 110°C hot-spot, `ageing_factor` = 1.0 within 1e-3. *If this fails your constants are wrong; fix before proceeding.*
- A 10°C rise roughly triples the factor — check within 10% of the Arrhenius prediction
- Hot-spot is monotonically increasing in both load and ambient
- Zero load returns hot-spot equal to ambient

**The India point, worth stating on your slide:** ambient is an additive term inside an exponential. The same 90% load at 45°C ages the transformer several times faster than at 25°C. That is why your model takes live temperature.

---

### 5.6 `algo/risk.py` — cuttable

LightGBM binary classifier over per-transformer features: rolling mean and max of loading, rolling mean and max of hot-spot, cumulative life-used fraction, voltage excursion count in the trailing window, ambient percentile.

Synthetic training label during development: life-used crossing a threshold within the next N blocks.

Expose `feature_importances_` so C can render an explanation.

**Must degrade.** With `lightgbm_enabled = False`, `rank()` returns the stub behaviour — sort by life used. Test both paths.

---

### 5.7 `algo/llm.py` — cuttable, last

Three call sites, all with timeouts and fallbacks.

```python
def daily_strategy(weather, price_history, timeout=3.0) -> StrategyParams:
    """Returns clamped params. On timeout or malformed output,
       returns the previous params unchanged."""

def diagnose(breach, transformer_state, timeout=2.0) -> str:
    """One plain line for the trace. On failure, a templated string."""

def answer(question, block_history, timeout=5.0) -> str:
    """On failure, 'unavailable'."""
```

**Constraint LM1.** The LLM may set only `StrategyParams.discount` and `.margin`. Clamp both to configured ranges *before returning*. Nothing downstream should have to trust the model.

**Tests**
- Mocked LLM returning out-of-range values → clamped output
- Mocked LLM that always times out → identical run to `llm_enabled = False`
- Malformed JSON → fallback, no raise

---

## 6. Your hour-by-hour

| Hours | Work | Done when |
|---|---|---|
| 0–2 | Every signature + every stub, committed, announced | B and C are building against them |
| 2–6 | `auction.py` real, `forecast.py` real | §5.1 and §5.2 tests pass |
| 6–12 | `powerflow.py` real, `reshape_lp.py` pass 1 | §5.3 tests pass; LP reduces a 112% case |
| 12–16 | `thermal.py` real | 110°C check passes |
| 16–20 | `reshape_lp.py` pass 2 — batteries | §5.4 full test set passes |
| 20–24 | `risk.py` | Both enabled and disabled paths tested |
| 24–28 | `llm.py` | All three fallbacks tested |
| 28–30 | Bug triage, support B and C | — |
| 30–36 | Freeze, rehearse, own the algorithm slide | — |

---

## 7. Your invariants

Every function you write satisfies all four:

1. **Pure.** Same input, same output, always. No globals, no clocks, no randomness except through a passed generator.
2. **No I/O.** No file reads, no network, no database. `llm.py` is the sole exception and it has a timeout and a fallback.
3. **Never raises for bad domain input.** Infeasible LP → `feasible=False`. Empty book → empty result. Raise only for programmer error, such as a malformed argument type.
4. **Configurable.** No physical constant appears as a literal at a call site.

---

## 8. Failure and fallback

| Situation | Response |
|---|---|
| LP infeasible | Return `feasible=False`. C handles it. |
| `linprog` slow at scale | Reduce variable count: batch trades by transformer, solve per transformer |
| LightGBM won't train on synthetic labels | Ship the stub; it is on the cut list |
| LLM unreliable | Ship defaults; engine check 10 already requires this path |
| Thermal constants look wrong | Trust the 110°C test, not your intuition |

---

## 9. Your second job: testing C's modules

You write `tests/grid/` — unit tests for C's sentinel, flow agent, and health agent.

Two reasons. You understand the LP and thermal model more deeply than anyone; and when the reshape misbehaves at hour 21, someone other than the author should be reading the code.

This is not a review role. Write the tests early, from the spec, before C's implementation exists.

---

## 10. What you present

**The algorithm slide, and it is the most valuable slide in the deck.**

> IEEE C57.91 for transformer ageing. A standard uniform-price double auction. A linear program for constraint reshaping. Linearised LinDistFlow for voltage. We did not invent our own physics.

Most teams cannot say that sentence. It reframes the project from "hackathon build" to "engineering", and it is the single best answer to a technical judge probing whether you understand what you built.

**Questions you own**
- Why an LP rather than reinforcement learning
- How the thermal model works and where the constants come from
- Why linearised power flow is sufficient at this scale
- What the LLM is and is not allowed to do
