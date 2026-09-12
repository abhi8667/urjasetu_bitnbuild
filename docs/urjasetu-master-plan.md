# UrjaSetu — Master Plan

**What this document is** How four people building four separate things end up with one working system. Read this before your individual PRD, and again at every integration point.

**The document set**

| Document | For | Contains |
|---|---|---|
| `urjasetu-prd.md` | B, C, D | Engine specification — modules, invariants, checks |
| `urjasetu-ui-prd.md` | A | Interface specification — screens, motion, transports |
| `person-a-interface.md` | A | Implementation plan |
| `person-b-market-agents.md` | B | Implementation plan |
| `person-c-grid-agents.md` | C | Implementation plan |
| `person-d-algorithms.md` | D | Implementation plan |
| `urjasetu-roles.md` | All | Ownership, cut order, question ownership |
| This document | All | How it comes together |

---

## 1. The project in one paragraph

Sixty metered premises on a simulated Whitefield street, four transformers. Software agents bargain on behalf of each premises every hour — solar houses offer surplus, others bid for what they need. A second set of agents checks whether the resulting trades would overload a transformer, and if so reshapes them rather than cancelling them: everyone sells a little less, batteries absorb the rest, and the energy comes back out at the evening peak. A health agent tracks how much life each transformer just burned and prices that wear back into tomorrow's trades. The result is a street that trades power with itself in a way that keeps its own equipment alive — and a side-by-side comparison proving it beats net metering on household bills, DISCOM revenue, and transformer lifespan simultaneously.

---

## 2. How the four pieces fit

```
        D — Algorithms (pure functions, no state)
                    ↑           ↑
                    │           │
        B — Market agents   C — Grid agents
              (state)          (state)
                    ↓           ↓
              B — Tick loop (the seam)
                        ↓
                   bus.ring
                        ↓
              A — Interface (renders, never computes)
```

**One direction only.** D never imports from B or C. A never imports from any engine module. B owns the tick loop, so B owns the sequence.

**Why this split works.** It matches how the code wants to be structured. D's functions are unit-testable the moment they exist, with nothing running. B's and C's agents are state plus orchestration around D's calls. A renders whatever comes out of the ring buffer. Nobody needs anybody else's working code to make progress.

---

## 3. The two contracts

Everything depends on these being frozen at hour 2 and never moving.

### Contract 1 — Algorithm signatures (D publishes)

D ships every signature with a naive stub by hour 2. B and C build against stubs and never wait again. D upgrades implementations in place behind unchanged signatures.

```python
algo.auction.clear(orders)                      -> ClearingResult
algo.forecast.ewma(history, alpha)              -> float
algo.powerflow.voltage_dev(house, net, topo)    -> float
algo.reshape_lp.solve(trades, limits, bats, topo) -> ReshapeSolution
algo.thermal.hotspot_c(load, rating, ambient, p) -> float
algo.thermal.loss_of_life_hours(hotspot, hours) -> float
algo.risk.rank(states)                          -> list[(str, float)]
algo.llm.daily_strategy(weather, history)       -> StrategyParams
```

### Contract 2 — Payload shapes (B and A freeze together)

`scene` once, `block` per block, `event` zero-or-more per block. Exact shapes in UI PRD §4.3. A writes `fixtures/day-one.json` from these and is unblocked for twenty hours.

### Contract 3 — Domain dataclasses (B publishes, hour 2)

`House`, `Transformer`, `MeterTick`, `Order`, `Trade`. All frozen. C cannot start without these.

**If a contract must change:** the owner announces it, everyone affected adapts in the same sitting, and A regenerates the fixture. Never a silent change.

---

## 4. Timeline

| Hours | A — Interface | B — Market | C — Grid | D — Algorithms |
|---|---|---|---|---|
| 0–2 | Handshake with B, fixture file | **Publish `domain.py`, `config.py`** | Test harness, read spec | **Publish all signatures + stubs** |
| 2–6 | Shell, tokens, routing | Bus, runner skeleton, market agent | Sentinel, loading check | Auction real, forecast real |
| 6–12 | Projection, layout, colours | Prosumer, consumer | Sentinel, phase + voltage | Power flow real, LP pass 1 |
| 12–16 | Trace, strip, badge | Settlement | Flow agent, curtailment | Thermal real |
| 16–20 | Wire pulses, arcs, cap | Persistence, **integrate with C** | Fallback, batteries, claims | LP pass 2, batteries |
| 20–24 | Reshape choreography | Baseline | Health agent, adder | Risk model |
| 24–28 | LiveTransport, **integrate**, compare | 30-day runs, run summary | Tuning limits | LLM layer |
| 28–30 | Demo controls | Triage | Triage | Triage |
| 30–33 | **All four: integration, fixture refresh, triage. No new features.** | | | |
| 33–36 | **All four: rehearsal ×4. No code.** | | | |

**Hour 30 is a hard feature freeze.** Anything not integrated by then is cut, not rushed.

---

## 5. Integration points

Four scheduled merges. Nobody integrates outside these without announcing it.

| Hour | Who | What merges | Passing means |
|---|---|---|---|
| 6 | B + D | Real auction into the market agent | A fixture order book clears at the analytically correct price |
| 16 | B + C | Tick loop + sentinel | Breaches detected and logged; nothing acts on them yet |
| 20 | B + C + D | Full loop with the real LP | **A 112% loading case reshapes to ≤100% within the 2-pass bound** |
| 26 | All | Engine + LiveTransport | The real city renders from a real run |

**After every merge, A regenerates the fixture from actual engine output.** A fixture that drifts from reality means A has been building against fiction — which you discover at hour 30, when it is expensive.

---

## 6. Checkpoint gates

Progress is measured by these, not by lines written. Put them on a wall.

| Hour | Gate | Owner | If missed |
|---|---|---|---|
| 6 | Market clears a fixture book correctly | B, D | D abandons the LLM layer and helps B |
| 12 | Sentinel detects an injected breach | C | C drops phase and voltage, keeps loading only |
| **20** | **A reshape resolves a 112% case** | **B, C, D** | **Cut batteries. Curtailment-only still demos.** |
| 24 | City renders live from the engine | A | A ships replay-only, permanently |
| 28 | Compare screen shows real numbers | A, B | Cut ledger, theatre, household |
| 30 | Full demo runs end to end twice | All | Stop development. Rehearse what works. |

**Hour 20 is the project.** Everything before it is scaffolding; everything after is polish. If it slips past hour 24, cut batteries immediately and without discussion.

---

## 7. Cut order

Decided now, so nobody argues at hour 29.

1. Household screen
2. DISCOM ledger screen
3. Agent theatre screen
4. LightGBM risk ranking → sort by loss of life
5. LLM layer → defaults
6. Cloud bank control → keep the derate
7. Battery custody and claims → own-battery-only
8. Batteries entirely → curtailment-only
9. Phase and voltage checks → loading only

**Never cut:** market clearing, sentinel, flow agent, settlement, baseline, city screen, compare screen, derate control. That set is the project.

**Note on item 5.** The engine is specified to run correctly with the LLM disabled, and integration check 10 requires it. Cutting the LLM costs you a sentence in the pitch, nothing else.

---

## 8. The three checks that prove the project works

If these pass, you have a real system. If any fails, something central is broken regardless of how good it looks.

**Money is conserved (ST1).** Across every block, the sum of every party's net position equals the total charges collected. If this fails, your settlement is inventing rupees.

**Energy is conserved (FL4).** Generation equals consumption plus net battery change plus losses, every block, within 1e-6 kWh. If this fails, your battery custody is inventing kilowatt-hours.

**Baseline ages faster than P2P.** Over 30 days, net metering must consume more transformer life than your system. If this fails, the ageing price signal is doing nothing and your central claim is false.

The third one is the one teams discover too late. **Run it at hour 24, not hour 33.**

---

## 9. Risk register

| Risk | Likelihood | Mitigation | Owner |
|---|---|---|---|
| D becomes a two-person bottleneck | High | Stubs at hour 2, signatures frozen | D |
| Battery claims break energy conservation | High | FL4 asserted every block from the moment batteries exist; fallback to own-battery-only | C |
| A builds against a drifted fixture | Medium | Regenerate at every integration point | A |
| Reshape bug spans three people's code | Medium | D writes the unit tests for C's modules, so a second pair of eyes exists | D, C |
| Nondeterminism found late | Medium | Run twice and diff at hour 12, not hour 30 | B |
| Engine dies during the pitch | Medium | Replay transport, plus manual step mode | A |
| Tuning produces no breaches, or constant ones | Medium | Tune at hour 26 for one breach every 8–15 daylight blocks | C |
| Scope creep on the last three screens | Low | They are cuts 1–3. Say no. | All |

---

## 10. What goes wrong when it goes wrong

**The reshape misbehaves at hour 21.** Is it D's LP formulation, C's constraint construction, or B's trade list? Three people, one bug, two hours gone. This is the most likely bad afternoon of your weekend.

The mitigation is already built in: D's functions are individually testable. Prove the LP correct in isolation against a hand-built case, and the search collapses to C's limit construction or B's inputs.

**The rule when this happens:** whoever owns the failing test drives; the other two answer questions. Three people typing is how two hours becomes four.

---

## 11. Shared responsibilities

Belong to nobody, therefore to everyone. Assign at hour 0 or they will not happen.

| Item | Owner | Due |
|---|---|---|
| Demo script, spoken and timed | A drafts, all edit | Hour 24 |
| Architecture slide — the two loops | C | Hour 26 |
| Algorithm slide — what is standard, what is ours | D | Hour 26 |
| Data provenance slide — what is real, what is simulated | B | Hour 26 |
| Q&A prep, the ten likely questions | All | Hour 30 |
| Rehearsal ×4 | All | Hours 33–36 |

**D's algorithm slide matters more than it sounds.** "IEEE C57.91 for ageing, a standard double auction, a linear program for reshaping, linearised LinDistFlow for voltage — we did not invent our own physics." Most teams cannot say that sentence, and it reframes the whole project.

**B's provenance slide protects you.** Naming your own limitation — no open Indian dataset pairs household load with rooftop PV, so generation is physically modelled rather than measured — reads as rigour. Being caught on it reads as overclaiming.

---

## 12. The demo, which everything serves

Three and a half minutes.

| Time | Screen | Beat |
|---|---|---|
| 0:00–0:30 | Compare, static | Net metering prices energy but not time, place, or wear. At a crore of rooftops, that is what breaks. |
| 0:30–1:30 | Theatre | The architecture, one agent at a time. The market appears twice — that is the loop. |
| 1:30–3:00 | City, live | Let it run. Press `d`. The transformer goes red, trades reshape instead of dying, batteries absorb, the trace explains itself. |
| 3:00–3:30 | Compare, live | Household bill down, DISCOM revenue up, transformer life saved. Same thirty days, same weather. |
| On request | Ledger | Why the DISCOM wants this: ₹1.43/kWh in charges it previously earned nothing on, plus deferred replacement at ₹2–3 lakh a unit. |

**The single sentence to land:** net metering prices energy but not time, location, or wear — and that is exactly what we price.

**Everyone must be able to drive it.** A will be answering questions about what is on screen; someone else holds the keyboard. Teach the keys at hour 30.

---

## 13. Working rules

1. One commit per module, messages that say what changed. No "wip" at hour 30.
2. Invariant violations raise. Nobody comments out an assertion to get past a bug — the assertion *is* the bug report.
3. `DECISIONS.md`, `UI-DECISIONS.md`, `ALGO-DECISIONS.md`. Five minutes of writing saves an hour of "why did we do it this way."
4. Blocked more than 20 minutes: say so out loud. Silent blocking is the most expensive failure mode on a small team.
5. The fixture file is sacred. Refresh at every integration point.
6. No new dependencies after hour 24.
7. No edits to a file you do not own. Message the owner instead.
8. Sleep in shifts. Three awake and one rested beats four awake and useless at hour 32.

---

## 14. Definition of done, for the team

- The three checks in §8 pass
- A 30-day run completes in under 3 minutes wall clock
- The full demo runs end to end from replay with the engine not running at all
- Every screen reachable by keyboard alone
- All four decision logs written
- Four people who can each answer the questions in their column of §12 of `urjasetu-roles.md`

If you reach hour 33 with these true, spend the last three hours rehearsing and nothing else. A well-rehearsed demo of a smaller system beats a fumbled demo of a larger one, every time.
