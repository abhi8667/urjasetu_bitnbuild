# Track C — Grid-Side Agents: implementation plan

Reviewed against `docs/person-c-grid-agents.md`, `docs/urjasetu-prd.md` §6.4–6.6,
`DECISIONS.md`, and the code on `main`.

Track C owns the **protection half** of the engine: detect breaches, reshape
trades to fit physics rather than cancelling them, hold battery custody, and
price transformer wear back into the market.

---

## What is already done for you

`main` carries the contracts and the feed. Nothing here is yours to build:

```python
from engine.config import DEFAULT as config
from engine.domain import Breach, MeterTick, ReshapePlan, StorageClaim, Trade, Transformer
from engine.feed import WhitefieldFeed
from engine import algo                     # D's stubs, callable today

feed = WhitefieldFeed(config)
transformers = feed.transformers()          # rating_kva from config, NOT the registry
houses = feed.houses()                      # phase, distance_m, battery_max_kw populated
sites = feed.sites()                        # lat/lon, building_type, transmission_loss_pct
```

**Take `rating_kva` from `feed.transformers()`.** Reading `kva_rating` out of
`transformer_registry.json` gives the installed 250–500 kVA and your sentinel
will never fire. See `DECISIONS.md` D1.

---

## Two decisions to confirm before coding

**Directory.** Top-level `grid/` package: `grid/sentinel.py`, `grid/flow.py`,
`grid/battery.py`, `grid/health.py`. **No `engine/grid/` re-export bridge** —
two import paths for one module is drift waiting to happen, and both B's and
C's docs say `grid/*`.

**Fallback before batteries.** The curtailment fallback must exist and be
proven before any battery absorption logic. A system that always has a safe
answer beats one with a clever answer that sometimes fails. If battery claims
ever violate FL4, drop to own-battery-only immediately — that is cut #7, not a
debugging session at hour 21.

---

## Three things to ask B and D for at hour 0

These are not yours to define; both files belong to B.

| Need | Why | Ask |
|---|---|---|
| `AgeingResult` in `engine/domain.py` | `health.apply()` returns it; two shapes = integration bug | B |
| `ThermalParams.R = 5.0` | `hotspot_c` needs the oil resistance ratio; the dataclass has `n`, `m`, `tau_oil_hours` only | B |
| `algo.thermal.hotspot_c` signature confirmed | you call it every block from hour 24 | D |

---

## Phase 1 — Test harness (hours 0–2)

`tests/grid/test_harness.py`. Three fixtures, but **only two need building by
hand**:

1. **Balanced three-phase** — symmetric load across A/B/C. Must never breach,
   regardless of magnitude. Hand-built.
2. **Voltage violation** — remote premises at the feeder's far end. **Cap
   `distance_m` at 187.3 m — that is the dataset maximum.** If you need a
   longer feeder to violate the 6% band, synthesise it explicitly and label it
   as synthetic; do not imply the data contains it.
3. **Overload** — *do not inject one.* The feed breaches 162 blocks in 720:
   DT-3 peaks at K=1.29, DT-4 at 1.32, DT-1 at 1.27, all in the 18:00–21:00
   window. Pull a real block:

```python
# a real breaching block, no fixture needed
ticks = feed.ticks(19)          # 19:00 on day 0
```

4. **Battery fixture** — 8 houses, 10 kWh each, ±5 kW, varying SoC.

**DT-2 never breaches (peak K = 0.95). Keep it that way** — it is the healthy
control on the compare screen.

---

## Phase 2 — `grid/sentinel.py` (loading hour 8, phase+voltage hour 12)

Loading check first and alone — it is the cheapest useful thing and it is what
the hour-12 gate actually rests on.

```python
class GridSentinel:
    def __init__(self, transformers, houses, config=DEFAULT, topology=None): ...
    def check(self, trades: list[Trade], ticks: list[MeterTick]) -> Breach | None: ...
```

**Net power per house.** From ticks, adjusted by cleared trades:

```
net_kw[h] = (load_kwh - gen_kwh) / config.block_hours + sold_kw - bought_kw
```

> **Decide the sign deliberately and comment it.** A house generating 8 kW and
> consuming 3 kW is at net −5; selling 5 kWh then puts it at 0. Whether a
> same-transformer trade removes load from the DT is a modelling choice, not an
> accident. Note that `sum(|net|)` and `|sum(net)|` coincide during the breach
> window — there is no generation after 18:00 — so the PRD's conservative
> formula is safe here. Know why before someone asks.

**Loading** — `K = sum(abs(net_kw[h]) for h in houses_on(t)) / t.rating_kva`,
breach if `K > config.loading_limit` (default 1.0), severity `K / limit`.

**Phase** — `imbalance = max(P_A,P_B,P_C) / mean(P_A,P_B,P_C)`, breach above
`config.phase_limit` (1.15). Phases are round-robin *within* each transformer,
so a phase breach only appears when trades skew one phase. Expect this check to
stay quiet; that is correct, not broken.

**Voltage** — call `algo.powerflow.voltage_dev(house_id, net, topology)`. Do not
compute it yourself. `topology` is `feed.sites()` plus `house.distance_m`.

Return the highest-severity breach or `None`.

- `SN1` `check` is pure — never mutates trades, ticks, or state
- `SN2` a returned breach has `severity >= 1.0`

**Tests:** exactly 100.0% → `None`; 100.1% → loading breach; balanced
three-phase never breaches at any magnitude; radial voltage within 1% of a hand
calculation; empty trade list runs clean.

---

## Phase 3 — `grid/flow.py`, curtailment and fallback (hours 12–17)

**Fallback first.** Uniform retention bringing loading to exactly the limit:

```
rho = (rating_kva * loading_limit) / actual_kva
quantity *= rho ; curtailed_fraction = 1 - rho
```

This always succeeds and never raises. Prove it before anything clever exists.

**Then the LP path.** Build limits, call
`algo.reshape_lp.solve(trades, limits, batteries, topology)`. `feasible=False`
returns a `ReshapePlan(feasible=False, ...)` and the runner falls back — never
raise.

**Your evening lever is not curtailment.** The breach window has no solar to
curtail. What you have at 19:00 is battery discharge and the four EV hubs
(`EVHUB-DT1`…`DT4`), which carry a `flexible_charging_window` flag in the raw
telemetry. Curtailing flexible EV charging demos far better than cutting a
household, and it is physically the right answer. Design the limits for it.

- `FL1` re-check after reshape returns `None`, or the fallback runs
- `FL2` `Σ new_claims.quantity <= Σ battery_charges × round_trip_efficiency`
- `FL3` no claim whose custodian lacks a battery
- `FL4` energy conserved every block to 1e-6 kWh — **losses come from
  `feed.transmission_loss_pct(house_id)` (3.25–6.75%), not zero and not a flat
  rate**

---

## Phase 4 — `grid/battery.py` (hours 17–20). The subtlest code you will write

Energy in a battery has an owner, a custodian, and a cost basis.

```
claim.quantity  = absorbed_kwh * config.round_trip_efficiency        # 0.90
custodian paid  = absorbed_kwh * config.storage_fee_inr_per_kwh      # 0.35 flat
claim.cost_basis = (foregone_clearing_price + storage_fee) / round_trip_efficiency
```

Absorption bounded by `battery_max_kw * config.block_hours` (5.0 kWh at
block_hours = 1.0) and by remaining headroom.

**Discharge is owner-first, oldest `opened_block` first.** A custodian may not
discharge its own energy while holding an unexpired claim, unless the owner has
declined at the current price. **Write that rule as a comment in the code —
you will second-guess it at hour 29.**

- `BT1` claims on house H sum to `<= H.battery_kwh`
- `BT2` every custodian has `has_battery == True`
- `BT3` a claim's quantity never increases
- `BT4` a discharge reduces exactly one claim, never below zero

**Tests:** 4.1 kWh absorbed at 0.90 → a claim of exactly 3.69 kWh; custodian's
own energy untouched while a claim is open; two claims discharge oldest-first;
BT1 across a 30-day run.

Then integrate batteries into the LP (hour 20 gate).

---

## Phase 5 — `grid/health.py` (hours 20–26)

IEEE C57.91, all algebraic, no iteration:

```
K          = load_kva / rating_kva
dTO        = rated_top_oil_rise * ((K**2 * R + 1) / (R + 1)) ** n     # R = 5.0
theta_h    = ambient_c + dTO + rated_hotspot_rise * K ** (2 * m)
F_AA       = exp(15000 / 383 - 15000 / (theta_h + 273))
loss_hours = F_AA * config.block_hours        # NOT 0.25 — the PRD predates hourly blocks
```

> **`config.block_hours`, not `0.25`.** The PRD's literal is the old 15-minute
> block. Using it makes every loss-of-life figure, the ageing adder and the
> baseline comparison a quarter of the truth.

Ambient is real and varies 21–28 °C across the day, so a 19:00 breach block
ages the unit measurably harder than an identical load at 03:00. That is your
slide.

**Ageing adder** — marginal, and applied to block `t+1`, never retroactively:

```
marginal_loss = loss_hours(with_trades) - loss_hours(without_trades)
adder = (marginal_loss / rated_life_hours) * replacement_cost / block_kwh
```

Clamp to `[0, config.max_ageing_adder]` (₹2.00/kWh).

> **Sanity-check the corridor with B at hour 24.** A buyer at a ₹4.00 clearing
> price pays 4.00 + 1.01 wheeling + 0.21 transaction + your adder, against a
> ₹7.18–8.40 retail tariff. A full ₹2.00 adder lands at ≈₹7.22. If your adder
> ever pushes a buyer's all-in above retail, the trade should never have
> cleared — B's CN2 is what catches it, and the bug will look like theirs.

`risk_rank()` calls `algo.risk.rank`; with `config.lightgbm_enabled = False` it
sorts by cumulative life used. Both paths must work — the stub is already the
documented fallback.

- `HL1` cumulative loss of life is monotonically non-decreasing
- `HL2` adder in `[0, max_ageing_adder]`, never NaN
- `HL3` state survives save/load exactly
- `HL4` the adder applied in block `t` was computed no later than `t-1`

---

## Phase 6 — Tuning (hour 26, not hour 34)

The one step most easily forgotten. Target: a breach often enough that a judge
watching for ninety seconds sees one, rarely enough that it is not constant.
Current state, measured: 162 breach blocks in 720, all in 18:00–21:00, 1–3
transformers at a time, on every simulated day.

Levers, in order of honesty:

1. `config.rating_kva` — already set to standard LT sizings (125/63/63/63)
2. `config.loading_limit` — defensible down to about 0.9; utilities do derate
3. `config.derate_factor` — **the demo control, on the never-cut list.** 0.8
   forces a breach on demand. Wire it and test it before rehearsal.

Record whatever you change in `DECISIONS.md`.

---

## Verification

Match `tests/test_feed_contracts.py`: pytest-compatible functions plus a
`__main__` runner, stdlib only, so anything runs on a clean machine.

```bash
python3 tests/test_feed_contracts.py     # contracts still hold
python3 tests/grid/test_sentinel.py      # hour-12 gate
python3 tests/grid/test_flow.py          # hour-20 gate
python3 tests/grid/test_battery.py
python3 tests/grid/test_health.py
```

**Hour-12 gate.** A real 19:00 block is flagged as a `loading` breach on the
correct transformer, severity ≈ 1.2–1.3. Nothing acts on it yet. That is fine.

**Hour-20 gate — this is the project.** The same block reshapes to ≤ 100%
within the 2-pass bound, and a deliberately infeasible input drops cleanly to
`fallback_curtail` without raising. If this slips past hour 24, cut batteries
immediately and without discussion; curtailment-only still demos.

**Hour-24 check, with B.** Baseline loss of life must exceed P2P loss of life
over 30 days. If it does not, the ageing signal is doing nothing and the
project's central claim is false. Run it at hour 24, not hour 33.

---

## D writes your unit tests

Per the master plan's risk register, D owns `tests/grid/`. When the reshape
misbehaves at hour 21 — the most likely bad afternoon of the weekend — a second
pair of eyes on your constraint construction is what collapses the search
space. Take them up on it at hour 12, not hour 20.
