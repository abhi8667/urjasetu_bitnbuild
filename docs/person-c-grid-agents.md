# Person C — Agents, Grid Side

**Mission** The protection half of the engine. You detect when a trade would hurt the network, and you find a way to make it fit instead of killing it.

**You own the demo's climax.** The reshape is the moment a judge sees the system solving rather than refusing. Everything else in the project is scaffolding around your hour-20 gate.

---

## 1. What you own

```
grid/sentinel.py    three constraint checks, stateless
grid/flow.py        reshaping, the fallback, orchestrating the LP
grid/battery.py     custody, claims, discharge ordering
grid/health.py      cumulative loss of life, ageing adder, risk exposure
```

## 2. What you never touch

`agents/*`, `market/*`, `sim/*` — B's. `algo/*` — D's. `ui/*` — A's.

You call `algo.powerflow`, `algo.reshape_lp`, `algo.thermal`, `algo.risk`. You do not modify them. If a computation needs changing, that is a message to D, not an edit.

---

## 3. Hour 0–2

Read engine PRD §6.4–6.6 fully. Confirm you can call D's four stubs. Get `domain.py` from B the moment it lands — you cannot build a sentinel without knowing the shape of `Trade` and `MeterTick`.

Then build a **test harness before any module**: hand-written lists of `Trade` and `MeterTick` representing a 112%-loading case, a balanced case, and a voltage-violation case. You will use these for the next eighteen hours and they are worth the hour.

You are not blocked by B's tick loop at any point. Build against your own fixtures throughout.

---

## 4. Implementation order

| Order | Module | Target hour | Why here |
|---|---|---|---|
| 1 | `sentinel.py` — loading check only | 8 | Cheapest useful thing |
| 2 | `sentinel.py` — phase and voltage | 12 | **Hour-12 gate** |
| 3 | `flow.py` — curtailment path | 16 | The critical path begins |
| 4 | `flow.py` — fallback | 17 | Must exist before batteries |
| 5 | `battery.py` — custody and claims | 20 | **Hour-20 gate** |
| 6 | `flow.py` — battery integration | 20 | Same gate |
| 7 | `health.py` — state and adder | 24 | The slow loop |
| 8 | `health.py` — risk exposure | 26 | Cuttable |

**Build the fallback before the batteries.** A system that always has a safe answer is worth more than one with a clever answer that sometimes fails.

---

## 5. Module specifications

### 5.1 `grid/sentinel.py` — hour 12

Stateless. A pure function wearing a class.

```python
class GridSentinel:
    def check(self, trades, ticks) -> Breach | None:
        net = self._net_kw_by_house(trades, ticks)
        breaches = []
        for t in self.transformers:
            breaches += self._loading(t, net)
            breaches += self._phase(t, net)
            breaches += self._voltage(t, net)
        return max(breaches, key=lambda b: b.severity) if breaches else None
```

**Loading**
```
K = sum(abs(net_kw[h]) for h in houses_on(t)) / t.rating_kva
breach if K > config.loading_limit            # default 1.0
```

**Phase**
```
imbalance = max(P_A, P_B, P_C) / mean(P_A, P_B, P_C)
breach if imbalance > config.phase_limit      # default 1.15
```

**Voltage** — call D, do not compute it yourself
```python
dev = algo.powerflow.voltage_dev(house_id, net, self.topology)
breach if abs(dev) > config.voltage_band      # default 0.06
```

```python
@dataclass(frozen=True)
class Breach:
    transformer_id: str
    kind: Literal["loading", "phase", "voltage"]
    severity: float          # 1.0 == exactly at limit
    detail: dict
```

**Invariants**
- `SN1` `check` is pure — never mutates trades, ticks, or any agent state
- `SN2` a returned breach has `severity >= 1.0`; `None` means all three checks are within limits

**Tests**
- Exactly 100% loading → `None`; 100.1% → loading breach
- Perfectly balanced three-phase → never a phase breach, regardless of magnitude
- Single-house radial voltage matches a hand-computed value within 1%
- Empty trade list → `None`, no raise

**Hour-12 gate: an injected breach is detected.** Nothing acts on it yet. That is fine.

---

### 5.2 `grid/flow.py` — hours 16–20. **The project's critical path.**

```python
class FlowAgent:
    def reshape(self, trades, ticks, breach, batteries) -> ReshapePlan:
        limits = self._build_limits(breach.transformer_id)
        sol = algo.reshape_lp.solve(trades, limits, batteries, self.topology)
        if not sol.feasible:
            return ReshapePlan(feasible=False, ...)
        claims = self._open_claims(sol.battery_charge, trades)
        orders = self._to_constrained_orders(trades, sol.retention)
        return ReshapePlan(constrained_orders=orders,
                           battery_charges=sol.battery_charge,
                           new_claims=claims, feasible=True, ...)

    def fallback_curtail(self, trades, breach) -> list[Trade]:
        """Uniform retention fraction bringing loading to exactly the limit.
           Always succeeds. Never raises."""
```

**Your job is not the optimisation.** D solves the LP. Yours is constructing the limits, turning the solution into orders and claims, and guaranteeing a safe answer when the LP fails.

**Invariants**
- `FL1` after a successful reshape, re-checking returns `None`, or the fallback path runs
- `FL2` `sum(new_claims.quantity) <= sum(battery_charges) * round_trip_efficiency`
- `FL3` no claim's custodian is a house without a battery
- `FL4` energy conserved per block within 1e-6 kWh: generation = consumption + net battery change + losses

**FL4 is the one that will catch your bugs.** Assert it every block from the moment batteries exist.

**Tests**
- A 112% case reshapes to ≤ 100% in one pass
- All batteries full → curtail-only plan, still feasible
- Batteries disabled in config → identical behaviour to curtailment-only
- Contradictory constraints → fallback fires, nothing raises
- FL4 across a 30-day run

---

### 5.3 `grid/battery.py` — hour 20. **The subtlest code you will write.**

Energy in a battery has an owner, a custodian, and a cost basis. Get this wrong and energy silently appears or vanishes.

```python
@dataclass(frozen=True)
class StorageClaim:
    claim_id: str
    owner_id: str          # whose energy
    custodian_id: str      # whose battery
    quantity_kwh: float    # AFTER round-trip loss
    cost_basis: float      # INR/kWh the owner effectively paid
    opened_block: int
```

**On absorption**
```
claim.quantity = absorbed_kwh * config.round_trip_efficiency      # default 0.90
custodian is paid absorbed_kwh * config.storage_fee_inr_per_kwh   # default 0.35, flat
claim.cost_basis = (foregone_clearing_price + storage_fee) / round_trip_efficiency
```

`cost_basis` is the number the owner's agent must beat when deciding whether to discharge later. It is what makes the decision real rather than decorative.

**On discharge — owner-first**

A custodian may not discharge its own energy while holding an unexpired claim, unless the claim owner has declined at the current price. Among claims, oldest `opened_block` first.

Write this rule down as a comment in the code. You will second-guess it at hour 29.

**Invariants**
- `BT1` `sum(claim.quantity for claims on house H) <= H.battery_kwh`
- `BT2` every claim's custodian has `has_battery == True`
- `BT3` a claim's quantity never increases after creation
- `BT4` discharging a claim reduces exactly one claim and never below zero

**Tests**
- Absorb 4.1 kWh at 0.90 efficiency → claim of 3.69 kWh exactly
- Custodian's own energy is not discharged while a claim is open
- Two claims on one battery discharge oldest-first
- BT1 across a 30-day run

---

### 5.4 `grid/health.py` — hour 24

```python
class TransformerHealthAgent:
    def apply(self, trades, ticks) -> AgeingResult:
        for t in self.transformers:
            hs = algo.thermal.hotspot_c(load_kva, t.rating_kva, ambient, params)
            lol = algo.thermal.loss_of_life_hours(hs)
            self._cumulative[t.id] += lol
        # compute NEXT block's adder here
```

**Ageing adder**
```
marginal = loss_of_life(with_trades) - loss_of_life(without_trades)
adder    = (marginal / t.rated_life_hours) * t.replacement_cost / block_kwh
adder    = clamp(adder, 0, config.max_ageing_adder)     # default 2.00
```

**Invariant HL4.** The adder computed in block `t` applies to clearing in block `t+1`. Never retroactive. This is the slow loop — it shapes tomorrow, it does not veto today.

**Invariants**
- `HL1` cumulative loss of life is monotonically non-decreasing per transformer
- `HL2` adder is within `[0, max_ageing_adder]` and never `NaN`
- `HL3` `save`/`load` round trip is exact

**Tests**
- 30 days at 50% load consumes less life than 30 days at 90%
- `save`/`load` produces identical `risk_rank` output
- HL1 across a 30-day run

**This is the project's only real memory.** Every other agent resets. Yours accumulates, and its day-30 behaviour is incomprehensible without days 1–29. When a judge asks where the memory is, this is the answer.

---

## 6. Your hour-by-hour

| Hours | Work | Done when |
|---|---|---|
| 0–2 | Read spec, build test harness, get `domain.py` | Three fixture cases exist |
| 2–8 | Sentinel, loading check | An injected 112% case is detected |
| 8–12 | Sentinel, phase and voltage | **Hour-12 gate passes** |
| 12–16 | Flow agent, curtailment path | 112% reduces to ≤100% |
| 16–17 | Fallback | Infeasible input never raises |
| 17–20 | Battery custody, claims, LP integration | **Hour-20 gate: full reshape with batteries** |
| 20–24 | Health agent, thermal state, adder | Baseline vs P2P loss of life diverges |
| 24–26 | Risk exposure via D's model | Both enabled and disabled paths work |
| 26–30 | Tuning limits, bug triage | — |
| 30–36 | Freeze, rehearse | — |

---

## 7. Tuning, which is real work

Your default limits will produce either no breaches or constant breaches. Neither demos.

Target: **a breach every 8–15 blocks during daylight hours**, so a judge watching for ninety seconds sees one naturally, and the derate control produces one on demand.

Levers, in order of preference:
1. Transformer `rating_kva` relative to the cluster's peak — the honest lever
2. `loading_limit` — defensible down to about 0.9, since utilities do derate
3. House `distance_m` spread, which drives voltage breaches
4. Phase assignment unevenness

Do this at hour 26, not hour 34, and record what you chose in `DECISIONS.md`. A judge may ask why a 100 kVA transformer serves 17 houses; "typical Indian LT distribution sizing" is the answer, and you should have checked it.

---

## 8. Fallbacks

| If | Then |
|---|---|
| Hour-12 gate missed | Drop phase and voltage; loading check alone still demos |
| LP integration fails | Ship fallback-curtail only. Less elegant, fully functional. |
| Battery claims produce energy-conservation failures | Revert to own-battery-only. Ownership ambiguity disappears. |
| Claims still broken at hour 24 | Cut batteries entirely; curtailment-only is a complete demo |
| Health agent slips | Cut the adder, keep cumulative loss of life for the compare screen |
| Risk model won't train | Use the stub — sort by life used. It is on the cut list. |

**The line you must not cross:** if the hour-20 gate slips past hour 24, cut batteries immediately and without discussion. A working curtailment reshape beats a broken battery reshape by a wide margin.

---

## 9. What you present

**The architecture slide.** Two loops, two speeds:

> The fast loop enforces safety inside a block — detect, reshape, re-clear, and if that fails, curtail. The slow loop shapes behaviour across days through price — a transformer that ran hot yesterday is more expensive to trade through today. One is a constraint. The other is an incentive.

Then name the pattern: plan → validate → sandbox → rollback, the Grid-Agent loop from the literature. You did not invent it; you implemented it.

**Questions you own**
- What stops the market from damaging the grid
- How the reshape decides what to trim
- Who owns energy sitting in someone else's battery
- How transformer ageing is calculated and why ambient temperature matters
- Why the ageing adder applies to the next block rather than this one
