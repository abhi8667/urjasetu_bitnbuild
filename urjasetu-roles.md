# UrjaSetu — Role Division

**Team size** 3 (with a 2-person variant in §8)
**Window** 36 hours
**Companion documents** `urjasetu-prd.md` (engine), `urjasetu-ui-prd.md` (interface)

---

## 1. The three roles

| | Owner | Owns | Never touches |
|---|---|---|---|
| **A** | Engine core | Domain model, event bus, config, market clearing, prosumer + consumer agents, settlement, baseline, persistence | Grid constraints, the LP, anything in the UI |
| **B** | Grid side | Sentinel, flow agent + LP, battery custody and claims, transformer health, thermal model, LightGBM | Market clearing, bidding logic, the UI |
| **C** | Interface | Everything in the UI PRD: shell, isometric city, five screens, transports, controls | Any engine module |

Nobody works outside their column without saying so out loud. Two people editing the same file at hour 28 is how teams lose runs.

---

## 2. The hour-one handshake

Before anyone writes real code, A and C sit together for 30 minutes and freeze two things:

1. **The payload shapes** — `scene`, `block`, `event`, exactly as in UI PRD §4.3.
2. **A fixture file** — one simulated day of those payloads, hand-written or scripted, saved to `fixtures/day-one.json`.

Once frozen, C is unblocked for the next twenty hours and needs nothing from A or B. This single step is what makes three-way parallelism possible.

**If the shapes need to change later**, the person proposing it announces it, updates the fixture, and both sides adapt in the same sitting. Never a silent change.

---

## 3. Ownership boundaries

### A owns
```
config.py            domain.py           bus.py
market/clearing.py   agents/prosumer.py  agents/consumer.py
settlement/settle.py sim/baseline.py     persistence.py
sim/runner.py        (the tick loop)
```

### B owns
```
grid/sentinel.py     grid/flow.py        grid/health.py
grid/battery.py      (custody + claims)  grid/thermal.py
models/risk.py       (LightGBM)
```

### C owns
```
ui/                  (everything)
fixtures/            (shared, but C maintains)
```

**The seam between A and B** is the tick loop in `sim/runner.py`. A writes it. B's modules plug into it through the signatures in engine PRD §6.4–6.6. B should be able to develop and test the entire grid side without running A's tick loop, using hand-built trade and tick lists.

---

## 4. Timeline

| Hours | A | B | C |
|---|---|---|---|
| 0–1 | **Handshake with C**, repo skeleton, config | Read engine PRD §6.4–6.6, set up test harness | **Handshake with A**, shell + tokens |
| 1–6 | Domain model, bus, market clearing | Sentinel: three checks, hand-tested | Projection, layout, static city |
| 6–12 | Prosumer + consumer agents, tick loop | Flow agent, curtailment-only LP | Block subscription, colour encoding |
| 12–18 | Settlement, persistence, baseline | Batteries + claims in the LP | Trace panel, metric strip, arcs |
| 18–22 | **Integration with B**, invariant checks | **Integration with A** | Reshape choreography |
| 22–26 | Run summary, 30-day runs, tuning | Thermal model + ageing adder | LiveTransport, degradation states |
| 26–30 | LLM layer, LightGBM handoff to B | Risk ranking, health checkpoint | Compare screen, demo controls |
| 30–33 | **All three: integration, fixture refresh, bug triage** | | |
| 33–36 | **All three: rehearsal ×4, no new code** | | |

**Hour 30 is a hard code freeze on new features.** Anything not integrated by then is cut.

---

## 5. Integration points

Three scheduled merges. Nobody integrates outside these without telling the others.

| Hour | What merges | Passing means |
|---|---|---|
| 12 | A's tick loop + B's sentinel | Breaches are detected and logged, not yet acted on |
| 18 | A + B full loop | Reshape fires and resolves within the 2-pass bound |
| 26 | Engine + C's LiveTransport | The real city renders from a real run |

After each merge, C regenerates `fixtures/day-one.json` from the actual engine output so the fixture never drifts from reality.

---

## 6. Checkpoint gates

Progress is measured by these, not by lines written.

| Hour | Gate | If missed |
|---|---|---|
| 6 | Market clears trades from a fixture order book | A drops the baseline module, B helps |
| 12 | Sentinel detects an injected breach | B drops phase and voltage checks, keeps loading only |
| 18 | A reshape resolves a 112% loading case | Cut batteries; curtailment-only still demos |
| 22 | City renders live from the engine | C switches to replay-only, permanently |
| 26 | Compare screen shows real numbers | Cut the ledger, theatre, and household screens |
| 30 | Full demo runs end to end twice | Stop all development; rehearse what works |

---

## 7. Cut order

When time runs out, cut in this order. Decided now, so nobody argues at hour 29.

1. Household screen
2. DISCOM ledger screen
3. Agent theatre screen
4. LightGBM risk ranking (fall back to sorting by loss of life)
5. LLM layer (defaults are fine; integration check 10 already requires this to work)
6. Cloud bank control (keep the derate)
7. Battery custody and claims (fall back to own-battery-only)
8. Batteries entirely (fall back to curtailment-only)

**Never cut:** market clearing, sentinel, flow agent, settlement, baseline, city screen, compare screen, derate control. That set is the project.

---

## 8. Two-person variant

| | Owner | Takes |
|---|---|---|
| **A** | Engine + shell | Everything in A's column, plus the UI shell, transports, and compare screen |
| **B** | Grid + city | Everything in B's column, plus the isometric city and trace panel |

Cut immediately, before starting: agent theatre, DISCOM ledger, household screen, LightGBM, cloud bank control. Ship city plus compare, with the derate control. That is still a complete, defensible demo.

---

## 9. Shared responsibilities

These belong to nobody and therefore to everyone. Assign them explicitly at hour 0 or they will not happen.

| Item | Owner | When |
|---|---|---|
| Demo script (spoken, timed) | C drafts, all edit | Hour 24 |
| Architecture slide | B drafts | Hour 26 |
| Data provenance slide | A drafts | Hour 26 |
| Q&A prep — the ten likely questions | All | Hour 30 |
| Rehearsal ×4 | All | Hours 33–36 |

**Everyone must be able to drive the demo.** The person who built the UI will be busy answering questions. Whoever is on the keyboard should not be discovering the keyboard shortcuts on stage.

---

## 10. Working rules

1. **One commit per module, messages that say what changed.** No "wip" at hour 30.
2. **Invariant violations raise.** Nobody comments out an assertion to get past a bug. The assertion is the bug report.
3. **`DECISIONS.md` and `UI-DECISIONS.md`** record every ambiguity resolved. Five minutes of writing saves an hour of "why did we do it this way."
4. **Blocked for more than 20 minutes: say so.** Out loud, to the other two. Silent blocking is the most expensive failure mode on a small team.
5. **The fixture file is sacred.** If it drifts from real engine output, C is building against fiction. Refresh at every integration point.
6. **No new dependencies after hour 24.**
7. **Sleep in shifts if you sleep.** Two people awake and one rested beats three people awake and useless at hour 32.

---

## 11. Question ownership

When a judge asks, this is who answers. Decide now; hesitation on stage reads as uncertainty.

| Question about | Answered by |
|---|---|
| Why P2P and not central allocation | Whoever is presenting |
| The auction, pricing, consumer protection | A |
| Constraints, the reshape, transformer ageing | B |
| Data provenance and what is simulated | A |
| Anything on screen | C |
| "Is this live or a video" | Whoever is on the keyboard — then press derate |
