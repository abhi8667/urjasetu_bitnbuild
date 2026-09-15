# UrjaSetu — Agent Verification Guide

**Audience:** an AI agent tasked with independently verifying that this system's
agents work correctly, that nothing is hallucinated, and that no accuracy has
been lost.

**Your job is to falsify, not to confirm.** Everything below is written so that
a claim either survives a check you run yourself or it does not. Where a number
is quoted, reproduce it. Where a behaviour is asserted, try to break it. If a
check fails, the failure is the finding — do not adjust the check until it
passes.

---

## 0. Setup, and the one command that runs everything

```bash
cd /path/to/urjasetu
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

./run_tests.sh                            # 12 suites passed, 0 failed, 0 skipped
python3 temp/checks/verify_agents.py      # 50 passed, 0 failed  (~40 s)
```

`temp/checks/verify_agents.py` is an executable form of this document: fifty
checks covering every section below, each one written to falsify rather than to
confirm. **Run it first.** Its exit code is the number of failures. Then work
through the sections by hand — the script cannot check §5.4 (the UI in a
browser) or §10 (the server), and reading the reasoning matters as much as
seeing green.

Two static checks run standalone too:

```bash
python3 temp/checks/no_hardcoded_ui.py    # fabricated constants back in the UI?
python3 temp/checks/one_power_factor.py   # kW->kVA conversion written twice?
```

Both are **comment-aware on purpose**. The source carries comments that quote
the old wrong values, because knowing what was wrong is what stops it being
reintroduced; a plain `grep` flags those comments and reports a false positive.
If you write your own version of these checks, strip comments first or you will
chase ghosts.

**If `run_tests.sh` prints any number of SKIPs, stop and investigate.** It used
to skip five of twelve suites when scipy was absent and still report "0 failed",
which is how a completely dead reshape path survived an entire integration. The
script now exits 1 rather than skipping. A SKIP means someone reintroduced that.

---

## 1. The agent inventory

There are **eight** agent types. Verify each exists, is constructed, and is
actually called during a run — several of these were previously written, tested,
and never invoked by anything.

| # | Agent | File | Instances | Called from |
|---|---|---|---|---|
| 1 | `ProsumerAgent` | `engine/agents/prosumer.py` | one per PV premises (18) | `AgentPool.build` |
| 2 | `ConsumerAgent` | `engine/agents/consumer.py` | one per premises (64) | `AgentPool.build` |
| 3 | `MarketAgent` | `engine/agents/market.py` | 1 | `Runner._tick` |
| 4 | `SettlementAgent` | `engine/agents/settlement.py` | 1 | `Runner._tick` |
| 5 | `GridSentinel` | `grid/sentinel.py` | 1 | `Runner._tick` (twice: reactive + predictive) |
| 6 | `FlowAgent` | `grid/flow.py` | 1 | `Runner._tick` on a loading breach |
| 7 | `TransformerHealthAgent` | `grid/health.py` | 1 | `Runner._tick` |
| 8 | LLM strategy layer | `engine/algo/llm.py` | 1 | `Runner._maybe_set_daily_strategy`, once per simulated day |

`BatteryBook` (`grid/battery.py`) is a custody ledger, not an agent.

**Check 1.1 — every agent is reached.** Run this. Every count must be > 0.

```bash
python3 - <<'EOF'
from collections import Counter
from engine.config import DEFAULT as C
from engine.bus import Bus
from engine.feed import WhitefieldFeed
from engine.sim.pool import AgentPool
from engine.sim.runner import Runner
from engine.agents.settlement import SettlementAgent
from grid import BatteryBook, FlowAgent, GridSentinel, TransformerHealthAgent

feed = WhitefieldFeed(C); h, t = feed.houses(), feed.transformers()
bus = Bus(ring_size=10**7)
pool = AgentPool(h, C)
s = SettlementAgent(h, C, bus=bus, consumers=pool.consumers, feed=feed)
r = Runner(feed, pool, C, bus=bus, settlement=s,
           sentinel=GridSentinel(t, h, C), flow=FlowAgent(t, h, C),
           health=TransformerHealthAgent(t, C, h), batteries=BatteryBook(h, C),
           include_baseline=False)
r.run(blocks=720)
for topic, n in sorted(Counter(e["topic"] for e in bus.ring).items()):
    print(f"{topic:24s} {n}")
EOF
```

Expect every one of these to appear: `block_opened`, `order_submitted`,
`market_cleared`, `breach_predicted`, `breach_detected`, `reshape_proposed`,
`reshape_applied`, `fallback_curtailed`, `ageing_applied`, `battery_moved`,
`delivery_shortfall`, `bill_lines_posted`, `block_settled`.

`strategy_updated` appears **only** when the LLM is enabled — see §6.

**Check 1.2 — `reshape_applied` must be non-zero.** If it is 0, the flow agent
is not reshaping anything and the "autonomously execute" half of the system is
dead. The most likely cause is a missing scipy; the second most likely is
something re-introducing a broad `except Exception` in `grid/flow.py:reshape`.

---

## 2. Reference figures — reproduce these exactly

`python3 demo.py --days 30` is deterministic (invariant D1: same config, same
bytes). These are the figures as of this verification pass:

| Figure | Value |
|---|---|
| premises / transformers / blocks | 64 / 4 / 720 |
| orders submitted | 45,613 |
| trades cleared | 5,553 |
| energy traded | 4,430.2 kWh |
| delivered to buyers | 4,194.3 kWh |
| lost in the wires | 235.9 kWh (5.3%) |
| breaches detected | 285 (93 loading, 192 phase) |
| reshapes applied | 38 |
| fallback curtailments | 84 |
| battery discharged | 152.0 kWh |
| baseline life used | 619.2 h |
| P2P life used | 593.4 h |
| life saved | 25.8 h (4.2%) |

**Check 2.1 — determinism.** Two runs must be byte-identical:

```bash
python3 demo.py --days 30 --summary /tmp/a.json >/dev/null
python3 demo.py --days 30 --summary /tmp/b.json >/dev/null
diff /tmp/a.json /tmp/b.json && echo "D1 HOLDS"
```

**Check 2.2 — these figures are not hardcoded.** Change the seed and confirm
they move; change nothing and confirm they do not.

```bash
python3 -c "
from dataclasses import replace
from engine.config import DEFAULT
from server.simulation import build_simulation
a = build_simulation(config=DEFAULT, days=5)
b = build_simulation(config=replace(DEFAULT, seed=777), days=5)
print('same seed  ->', a.run_summary['trades'])
print('diff seed  ->', b.run_summary['trades'])
assert a.run_summary != b.run_summary, 'seed has no effect — figures may be hardcoded'
print('seed genuinely drives the run')"
```

---

## 3. The invariants — where accuracy actually lives

Every one of these is asserted **inside the engine at runtime**, not just in
tests. An `InvariantError` stops the run; it is never downgraded to a warning.

| ID | Claim | Enforced in |
|---|---|---|
| MK1 | traded ≤ min(offered, bid) | `agents/market.py:_assert_market_invariants` |
| MK2 | seller floor ≤ clearing price ≤ buyer ceiling, per trade | same |
| MK3 | identical order lists → identical trades, ids included | `algo/auction.py` |
| ST1 | every party's net position sums to exactly what the DISCOM collected | `agents/settlement.py:_assert_st1` |
| ST2 | a bill line's eight components sum to its `net_inr` | `_assert_st2` |
| ST3 | every bill line traces to a trade or a storage claim | `_assert_st3` |
| CN1 | a consumer never bids above its retail tariff | `agents/consumer.py:bid_ceiling` |
| CN2 | cumulative spend never exceeds the grid-only counterfactual | `agents/consumer.py:_assert_cn2` |
| PR1 | a prosumer's SoC stays within [0, battery_kwh] | `agents/prosumer.py:_assert_soc` |
| P1 | at most 2 clearing passes per block | `sim/runner.py` |
| SN1/SN2 | sentinel is pure; a returned breach has severity ≥ 1.0 | `grid/sentinel.py` |
| BT1–BT4 | battery custody bounds | `grid/battery.py` |
| HL1 | cumulative loss of life is monotonic | `grid/health.py` |
| HL2 | ageing adder ∈ [0, max_ageing_adder], never NaN | `grid/health.py:ageing_adder` |
| **HL4** | **the adder billed in block t was computed no later than t-1** | see §4 |
| FL4 | energy is conserved across the battery layer | `tests/test_fl4_energy_conservation.py` |
| D1 | two identical runs produce byte-identical summaries | §2.1 |

**Check 3.1 — try to break ST1.** Introduce a charge that is collected but not
billed, and confirm the run stops:

```bash
python3 -c "
from engine.agents import settlement as S
from engine.domain import InvariantError
try:
    S._assert_st1(lines=[], collected=1.0)   # nothing billed, a rupee collected
    print('FAIL: ST1 did not fire')
except InvariantError as e:
    print('ST1 fires correctly:', str(e)[:70])"
```

---

## 4. HL4 — the most subtle of the fixes. Verify it specifically.

`AgeingResult` carries **two** adder dicts and they are not interchangeable:

- `adders` — computed from **this** block's thermal state, in force from t+1
- `active_adders` — computed in t-1, in force **now**

Settlement must bill `active_adders`. The property `AgeingResult.ageing_adder`
points at `active_adders` for exactly this reason. Billing `adders` prices a
trade using information that did not exist when it was struck.

**Check 4.1:**

```bash
python3 - <<'EOF'
from engine.agents.settlement import SettlementAgent
from engine.config import DEFAULT as C
from engine.domain import AgeingResult, Trade
from engine.feed import WhitefieldFeed
feed = WhitefieldFeed(C); agent = SettlementAgent(feed.houses(), C, feed=feed)
t = [Trade("T1", 0, "10006", "10000", 2.0, 4.0, 0.0)]

a = AgeingResult(states=[], adders={"DT-1": 9.99}, active_adders={"DT-1": 1.5})
_, buyer = agent.settle(t, a)
assert buyer.ageing_inr == 3.0, f"expected the ACTIVE adder, got {buyer.ageing_inr}"

agent2 = SettlementAgent(feed.houses(), C, feed=feed)
b = AgeingResult(states=[], adders={"DT-1": 2.0}, active_adders={})
_, buyer2 = agent2.settle(t, b)
assert buyer2.ageing_inr == 0.0, "HL4 VIOLATED: a forward adder was billed now"
print("HL4 holds: settlement bills the lagged adder only")
EOF
```

---

## 5. Anti-hallucination audit — the highest-value checks

These are where invented numbers hid before. Run all of them.

**Check 5.1 — no fabricated constants in the UI.** Every figure on screen must
come from the engine.

```bash
python3 temp/checks/no_hardcoded_ui.py
```

It searches for fourteen constants that were previously rendered as if they were
measurements — `4.8 kWp` for every PV premises, `10 kWh LiFePO4` for every
battery, a `0.78` state-of-charge fallback, `148.2` kWh of trading in a block
where nothing traded, `₹4.20` when nothing cleared, `+12%` and `-6%` deltas with
arrows beside them, gauges labelled `Txr_North`/`Txr_South` for transformers
that do not exist, and `186000` of deferred capex.

`UI/src/demoFixture.ts` still contains invented numbers **by design** — it is
the offline fallback — and the check excludes it. What matters is that the app
does not open on it when an engine is configured (§5.4).

**Check 5.2 — the payload contains no placeholders.** `server/payloads.py`
returns `None`/`null` where the engine does not know something, never a
plausible figure. Verify `soc_frac` is null for every premises without a
battery, and never a default:

```bash
python3 - <<'EOF'
from server.simulation import build_simulation
sim = build_simulation(days=2)
by_id = {h["id"]: h for h in sim.scene["houses"]}
bad = []
for b in sim.blocks:
    for hid, st in b["houses"].items():
        if not by_id[hid]["has_battery"] and st["soc_frac"] is not None:
            bad.append((b["block"], hid, st["soc_frac"]))
print("non-battery premises reporting a state of charge:", len(bad), bad[:3])
assert not bad
socs = {st["soc_frac"] for b in sim.blocks for st in b["houses"].values()
        if st["soc_frac"] is not None}
assert 0.78 not in socs or len(socs) > 5, "0.78 placeholder may have returned"
print("no placeholder SoC; distinct SoC values:", len(socs))
EOF
```

**Check 5.3 — the compare figures are derived, not typed.** `deferredCapex`
must equal `(life saved / rated_life_hours) * replacement_cost_inr`:

```bash
python3 - <<'EOF'
from engine.config import DEFAULT as C
from server.simulation import build_simulation
sim = build_simulation(days=2)
s = sim.summary
expect = (s["lifeSavedHours"] / C.rated_life_hours) * C.replacement_cost_inr
assert abs(s["deferredCapex"] - round(expect, 2)) < 0.01, (s["deferredCapex"], expect)
assert s["deferredCapex"] != 186000, "the old fixture constant is back"
print(f"deferredCapex {s['deferredCapex']} derives correctly from "
      f"{s['lifeSavedHours']} h saved")
EOF
```

**Check 5.4 — the UI does not silently fall back to the fixture.** With
`VITE_ENGINE_URL` set, `DemoTransport` must not be constructed. Confirm the
status pill reads "Live engine" and not "Demo data":

```bash
# terminal 1
uvicorn server.app:app --port 8000
# terminal 2
cd UI && printf 'VITE_ENGINE_URL=http://localhost:8000\n' > .env.local && npm run build && npx vite preview --port 4173
# then load http://127.0.0.1:4173 and read the pill in the top right
```

`DemoTransport.start()` deliberately emits status `replay`, never `live`. If
you ever see "Live engine" while the backend is down, that is a regression.

**Check 5.5 — the event trace is complete, not sampled.** `bus.ring` is a
fixed-size deque and `order_submitted` fires ~63×/block, so reading the ring
silently loses decision events. The server subscribes instead. Verify the trace
has one `block_opened` per block:

```bash
python3 -c "
from server.simulation import build_simulation
sim = build_simulation(days=2)
opens = [e for e in sim.events if e['kind'] == 'block_opened']
print(f'{len(opens)} block_opened events for {len(sim.blocks)} blocks')
assert len(opens) == len(sim.blocks), 'events are being dropped'"
```

---

## 6. The LLM agents (Groq)

**The engine must run identically with the LLM off.** That is PRD integration
check 10 and it is the thing to verify hardest.

Configuration lives in `.env` (see `.env.example`):

```
GROQ_API_KEY=...
URJASETU_LLM_ENABLED=true
GROQ_MODEL=llama-3.3-70b-versatile
```

`LLM_ENABLED` requires **both** the flag and a non-empty key. A key without the
flag, or a flag without a key, means off.

**Check 6.1 — off by default, and cuttable.** With no key:

```bash
python3 -c "
from engine.algo import llm
from engine.domain import Breach, StrategyParams, TransformerState
assert llm.LLM_ENABLED is False
prev = StrategyParams(discount=0.61)
assert llm.daily_strategy({}, [], prev) == prev
assert llm.answer('anything', []) == 'unavailable'
st = TransformerState('DT-1', 0.1, 110.0, 1.0, 0.5)
assert 'DT-1 loading breach' in llm.diagnose(Breach('DT-1','loading',1.2,{}), st)
print('LLM off: every call site falls back deterministically')"
```

**Check 6.2 — LM1: the LLM may move only four clamped numbers.** It must never
move `battery_reserve_frac` (that is a grid decision, not a trading one), and
every value must be clamped:

```bash
python3 - <<'EOF'
from unittest.mock import patch
from engine.algo import llm
from engine.domain import StrategyParams
with patch.object(llm, "LLM_ENABLED", True), \
     patch.object(llm, "_call_llm",
                  return_value='{"discount":99,"margin":-5,"bid_aggression":40,'
                               '"battery_reserve_frac":0.99}'):
    p = llm.daily_strategy({}, [], StrategyParams())
assert p.discount == 1.0 and p.margin == 0.02 and p.bid_aggression == 1.5, p
assert p.battery_reserve_frac == 0.20, "LM1 VIOLATED: the LLM moved a frozen field"
print("LM1 holds:", p)
EOF
```

**Check 6.3 — a hostile or broken model cannot corrupt a run.** Timeouts,
malformed JSON, HTTP errors and empty responses must all leave the previous
parameters in place. Verify with `side_effect=TimeoutError`, a non-JSON string,
and `return_value=""`. In every case `daily_strategy` returns `previous`
unchanged, `diagnose` returns the templated line, and `answer` returns
`"unavailable"` — **never a guess**, because a confident wrong answer about a
distribution grid is worse than no answer.

**Check 6.4 — with a real key, the invariants still hold.** Enable the LLM and
re-run the full suite. Every invariant in §3 must still pass. The LLM sits
outside the invariant boundary by construction; if enabling it can break ST1 or
CN2, something has let it reach further than four strategy numbers.

---

## 7. Physics accuracy — the checks that catch silent drift

**Check 7.1 — one power factor everywhere.** A transformer is rated in kVA and
meters report kW. Every module that compares them must divide by the same power
factor. This was a literal `0.95` in `grid/flow.py` and `grid/health.py`, an
inline `/ 0.95` in `engine/sim/baseline.py`, and **absent entirely** from
`grid/sentinel.py` — so the sentinel measured every transformer 5.3% cooler than
the health agent measured the same transformer in the same block.

```bash
python3 temp/checks/one_power_factor.py
```

It parses each module's AST (so docstrings and comments are excluded by
construction) and fails on any float literal equal to 0.95 or 1/0.95 outside
`engine/config.py`, which declares the default, and `engine/physics.py`, which
applies it.

**Check 7.2 — sentinel and health agree.** They must report the same K for the
same block:

```bash
python3 - <<'EOF'
from engine.config import DEFAULT as C
from engine.feed import WhitefieldFeed
from engine.physics import loading_k
from grid.sentinel import GridSentinel
from grid.health import TransformerHealthAgent
feed = WhitefieldFeed(C); h, t = feed.houses(), feed.transformers()
sen, hea = GridSentinel(t, h, C), TransformerHealthAgent(t, C, h)
ticks = feed.ticks(19)
ageing = hea.apply([], ticks)
net = sen._net_kw_by_house([], ticks)
for tr in t:
    members = [x for x in h if x.transformer_id == tr.transformer_id]
    k_sen = loading_k(sum(abs(net[x.house_id]) for x in members),
                      tr.rating_kva, C.power_factor)
    k_hea = ageing.loading_k[tr.transformer_id]
    print(f"{tr.transformer_id}  sentinel {k_sen:.4f}  health {k_hea:.4f}")
    assert abs(k_sen - k_hea) < 1e-3, "the two agents disagree about loading"
print("sentinel and health agree on K")
EOF
```

**Check 7.3 — `Trade.quantity_kwh` is net of curtailment, once.** Call
`engine.trades.delivered_kwh(trade)` rather than reading either field.
`FlowAgent.fallback_curtail` scales `quantity_kwh` by ρ **and** records
`curtailed_fraction = 1 − ρ`, so a reader that multiplies them together applies
the curtailment twice (ρ² instead of ρ). Settlement read the quantity raw and
was right; the sentinel multiplied and was wrong.

Check 4.2 in `verify_agents.py` covers this numerically: a trade of 8.0 kWh at
20% curtailment must deliver 8.0, not 6.4. `engine/trades.py:original_kwh` is
the one place the inverse is deliberately written.

**Check 7.4 — battery accounting reconciles three ways.** Round-trip efficiency
means what leaves a premises' surplus and what lands in its battery are
different numbers. The BatteryBook, the prosumer agents' belief, and the run
summary must all agree:

```bash
python3 - <<'EOF'
from engine.config import DEFAULT as C
from engine.feed import WhitefieldFeed
from engine.sim.pool import AgentPool
from engine.sim.runner import Runner
from engine.agents.settlement import SettlementAgent
from grid import BatteryBook, FlowAgent, GridSentinel, TransformerHealthAgent
feed = WhitefieldFeed(C); h, t = feed.houses(), feed.transformers()
pool = AgentPool(h, C); bb = BatteryBook(h, C)
s = SettlementAgent(h, C, consumers=pool.consumers, feed=feed)
r = Runner(feed, pool, C, settlement=s, sentinel=GridSentinel(t, h, C),
           flow=FlowAgent(t, h, C), health=TransformerHealthAgent(t, C, h),
           batteries=bb, include_baseline=False)
sm = r.run(blocks=240)
book = sum(bb.total_stored_kwh(x.house_id) for x in h if x.has_battery)
agents = sum(p.battery_soc_kwh for p in pool.prosumers.values())
summary = sm["battery_charged_kwh"] - sm["battery_discharged_kwh"]
print(f"BatteryBook {book:.6f} | agents {agents:.6f} | summary {summary:.6f}")
assert abs(book - agents) < 1e-6, "agents' SoC belief has drifted from the book"
assert abs(book - summary) < 1e-6, "the run summary disagrees with the book"
print("battery accounting reconciles three ways")
EOF
```

**Check 7.5 — charging is visible to the meters.** Energy entering a battery is
load at that premises. If it is not, the sentinel and the health agent measure a
street where it never happened, and the seller can sell the kWh it just stored.

```bash
grep -n "_apply_charge" engine/sim/runner.py     # must be called in _tick
```

---

## 8. The three claims the project rests on

**Money conserved (ST1).** Already covered — asserted every block.

**Energy conserved (FL4).** `python3 tests/test_fl4_energy_conservation.py`.
Note what it does and does not prove: it checks battery bookkeeping, physical
bounds, that traded energy is physically backed, and that billed losses actually
remove energy. It deliberately does **not** assert the whole-street balance,
which is an identity by construction and would pass against arbitrarily broken
code. Read the module docstring before judging its coverage.

**Baseline ages at least as fast as P2P.** This is the headline and it is the
easiest to fake.

```bash
python3 -c "
from server.simulation import build_simulation
sim = build_simulation(days=30)
c = sim.compare['transformer_life_hours']
print(f\"baseline {c['baseline']:.1f} h, p2p {c['p2p']:.1f} h, saved {c['saved_hours']:.1f} h\")
assert sim.compare['baseline_ages_at_least_as_fast']
assert c['saved_hours'] > 0, 'saved 0.0 h — protection is doing nothing'
print('claim holds and is non-trivial')"
```

**A `saved_hours` of exactly 0.0 is the specific failure to watch for.** It
means both sides are measuring the same unreshaped street — which is what
happened when scipy was missing and every reshape silently returned
`feasible=False`. The check passes *vacuously* at 0.0 because ≥ is satisfied by
equality. Treat 0.0 as a failure, not a pass.

---

## 9. Prediction — verify it is scored, not decorative

The sentinel is reactive by construction. `Runner._predict_next_block` adds a
one-block-ahead check using the feed's **forecast** (agent belief, noise
included — not truth), and its accuracy is recorded:

```bash
python3 -c "
from server.simulation import build_simulation
sim = build_simulation(days=30)
p = sim.run_summary['breach_prediction']
print(p)
assert p['predicted'] > 0, 'nothing was ever predicted'
assert p['hits'] + p['misses'] == p['predicted']"
```

Expect roughly 48% precision over 30 days. **Do not treat a low number as a
bug** — it is a forecast, it is allowed to be wrong, and it is reported honestly
rather than tuned. What would be a bug is prediction accuracy of 100%, which
would mean it is reading truth rather than the noisy forecast.

Nothing downstream acts on a prediction, deliberately: acting on a forecast
would let a bad forecast curtail a real trade.

---

## 10. Server and UI integration

```bash
uvicorn server.app:app --port 8000
curl -s localhost:8000/api/health | python3 -m json.tool
curl -s localhost:8000/api/run-summary | python3 -m json.tool | head -40
```

`/api/run-summary` includes a `warnings` array. **Read it.** It is populated
when `baseline_ages_at_least_as_fast` fails, or when the CN2 charge-stack cap
had to trim the ageing adder to keep buyers' all-in cost at or below retail.

**Check 10.1 — WebSocket envelope.** Must be
`{"type": "scene"|"summary"|"block"|"event", "data": ...}`:

```bash
pip install websockets
python3 - <<'EOF'
import asyncio, json, websockets
async def main():
    async with websockets.connect("ws://127.0.0.1:8000/ws?cadence=0.1&days=2") as ws:
        for _ in range(8):
            m = json.loads(await ws.recv())
            print(m["type"], str(m["data"])[:90])
asyncio.run(main())
EOF
```

**Check 10.2 — the UI renders engine data with no console errors.** Build and
drive it; the only acceptable network failure is Google Fonts in a sandboxed
environment. Confirm the node count reads 64 (not 14), the agent count reads 87
(64 consumers + 18 prosumers + 5 singletons), and the transformer table shows
four rows DT-1..DT-4 with live hot-spot temperatures.

---

## 11. Known limitations — do not report these as bugs

These are deliberate, documented decisions. Read `DECISIONS.md` before filing
any of them.

1. **Household bills are HIGHER under P2P than under the baseline**
   (≈ −₹12,273 over 30 days). This is real, not an error. The baseline is
   *one-for-one net metering*, which credits exported kWh at the full retail
   tariff — an extremely generous scheme, and the one the project argues
   against. Set `baseline_export_credit: feed_in` in `config.yaml` to compare
   against gross metering instead, and the sign flips. See DECISIONS.md D13.

2. **`deferredCapex` is small** (≈ ₹36 over 30 days). Saving 26 hours out of a
   180,000-hour rating genuinely is worth about that. `deferredCapexAnnualised`
   projects the same rate over a year and is labelled as a projection.

3. **Phase breaches are never reshaped.** Phase imbalance is a property of which
   premises sit on A, B and C. Curtailing a trade cannot change it, and 192 of
   285 breaches are phase. The sentinel reports them for the trace; the flow
   agent is not asked to fix what it cannot.

4. **The ageing adder is average wear per kWh, not the PRD's marginal
   `loss(with) − loss(without)`.** That marginal form is identically zero in
   this model — a trade is a financial contract between two premises on one
   transformer and moves no power that was not already flowing. Documented as
   DECISIONS.md D14.

5. **Most blocks clear no trades.** This street is a net importer in every block
   (~2,060 kWh/day of demand against ~195 kWh of surplus). A block with no
   clearing price is a real and common state, and the UI shows "—" for it rather
   than a placeholder price.

6. **Transformer ratings are overridden in config** (125/63/63/63 kVA) rather
   than read from `transformer_registry.json` (250–500 kVA). At the registry
   ratings peak loading is 21–29% and no breach is reachable at any defensible
   limit — the sentinel, the flow agent and the ageing signal would never fire.
   DECISIONS.md D1.

7. **The CN2 charge-stack cap trims the ageing adder** when energy plus the six
   charges would exceed a buyer's retail tariff. Amount trimmed is reported as
   `ageing_adder_trimmed_inr` in the run summary and surfaced in `warnings`.

---

## 12. What the automated suite does NOT cover

`verify_agents.py` cannot check these. Do them by hand.

- **§5.4** — the UI in a real browser, and specifically that the status pill
  reads "Live engine" rather than "Demo data".
- **§10** — the server's HTTP and WebSocket surface.
- **§6.4** — the invariants with a real Groq key and real network latency. The
  mocked checks prove the fallbacks; only a real key proves the happy path.
- **Render/Vercel deployment** — a cold start, a reconnect across it, and
  `wss://` from an HTTPS origin.
- **Judgement.** Read `DECISIONS.md`. Several figures that look wrong are
  deliberate, and §11 lists them.

---

## 13. Reporting

For each check: state the command, the observed output, and PASS/FAIL. For a
FAIL, give the smallest reproduction you can find and say which invariant or
claim it breaks. Do not modify a check to make it pass.

Pay particular attention to anything in §5 (hallucination) and §7 (physics
drift) — those are the two categories where a regression is invisible on screen
and only a check like these will catch it.
