# Completion plan — closing the gap to PRD §13 "definition of done"

Written from the audit in this session. Ordered by risk first (unknown-if-it-
even-passes beats known-broken beats known-missing-polish), then by how much
one phase blocks the next. Each phase ends with a concrete pass/fail gate —
don't start the next phase on a gate that hasn't been checked.

---

## Phase 1 — Find out if FL4 actually holds (unknown risk, do this first)

**Why first:** every other number in this project — ST1, the baseline
comparison, the 24h life-saved headline — is downstream of energy being
conserved. Nobody has actually checked it. It might already be broken, in
which case the loss-of-life numbers are compromised and need re-deriving.
Better to find that now than after Phase 3 builds on top of it.

**Task 1.1 — Write the check, don't just assert it exists.**
For every block of a real run: `generation_kwh == consumption_kwh + net_battery_delta_kwh + losses_kwh`, within 1e-6. Sources per block:
- `generation_kwh`, `consumption_kwh` — sum of tick `gen_kwh`/`load_kwh`
- `net_battery_delta_kwh` — `sum(battery_charges) - sum(battery_discharges)` from the flow agent's plan, plus the prosumer's own daylight reserve (D15)
- `losses_kwh` — `Σ trade.quantity_kwh * feed.transmission_loss_pct(seller_id) / 100` for every settled trade

**Task 1.2 — Run it against a real 30-day integrated run** (sentinel + flow + health + batteries all on) and report the actual number, not a guess.

**Gate:** either it holds to 1e-6 and you have a real, asserted invariant — or it doesn't, and you know before anything else is built on it. If it fails, the likely culprits, in order of suspicion: the daylight reserve (D15) moves energy without a matching loss/claim entry; the flow agent's discharge (`discharge_for_owner` / `discharge_custodian_own`) isn't reconciled against what actually left the tick; transmission loss is charged in settlement but never subtracted from the physical balance.

**Effort:** half a day. This is verification, not new mechanism — if it fails, add another half-day to fix whichever term is missing.

---

## Phase 2 — Assemble `run_summary.json` for real

**Why second:** cheap, mechanical, and it's the artifact every other check and the whole demo reads from. Doing it now means Phases 3+ can log against the real schema instead of ad-hoc prints.

**Task 2.1.** Extend `_Accumulator.finalise()` (or wrap it) to also pull from the settlement ledger and health agent, so the one object the PRD requires actually contains:
- total traded kWh, mean clearing price *(have these)*
- count of breaches by kind *(from `bus.recent_events("breach_detected")`, group by `payload["kind"]`)*
- count of reshapes and fallbacks *(same, from `reshape_applied` / `fallback_curtailed`)*
- cumulative loss of life per transformer *(from `health._cumulative_life_hours`)*
- total DISCOM charge revenue *(from `settlement.charges_collected`)*
- the same figures for the baseline path *(from `Baseline.run()`)*

**Task 2.2.** Keep it D1-safe: no wall-clock values, nothing depending on dict iteration order — sort every dict before serializing (same discipline as the existing accumulator).

**Gate:** `run_summary.json` contains every field PRD §10 lists, verified by asserting the key set programmatically, not by eyeballing it. Two identical runs still produce byte-identical output.

**Effort:** half a day.

---

## Phase 3 — Make breach resolution (#6) pass for the right reason

**Why third:** currently "passes" only because fallback curtailment always succeeds by construction — the actual point of the check (does the reshape/LP path work under a forced derate) never engaged. At derate_factor=0.6, the LP found zero feasible solutions and everything went straight to fallback.

**Task 3.1 — Diagnose why 0.6 derate is infeasible.** Likely candidates: the tighter rating leaves no slack for the LP's bidirectional voltage/loading rows simultaneously; or the daylight battery reserve (61 kWh total) isn't enough headroom at this derate to satisfy the constraint on its own, so `linprog` correctly reports infeasible and the fallback is the honest answer.

**Task 3.2 — If it's a genuine physical infeasibility, that's fine — but say so explicitly rather than let it hide behind "100% resolved."** Record in `DECISIONS.md`: at derate ≤ X, only fallback curtailment can restore compliance; above that, the LP contributes. Report both counts (`reshape_applied` vs `fallback_curtailed`) in the acceptance check output, not just "resolved."

**Task 3.3 — If it's a bug (e.g. the LP's bounds are miscomputed at this derate), fix it** and confirm reshape_applied > 0 at 0.6 derate on at least some blocks.

**Gate:** the check reports both counts, and whichever the true answer is (bug fixed, or genuine physical limit documented), it's stated plainly rather than implied by a green checkmark.

**Effort:** half a day to a day, depending on which branch it turns out to be.

---

## Phase 4 — Topology independence (#9) — CLOSED, waived by decision

**Resolved:** option (c). The engine is count-agnostic (verified — any number of
`rating_kva` entries runs identically), the dataset is fixed at four
transformers, and fabricating a second topology to satisfy the check literally
buys nothing. Recorded as DECISIONS.md D21. The engine ships at 9/10 integration
checks with this one deliberately waived.

<details><summary>Original plan, kept for context</summary>

### (superseded) Decide on topology independence (#9), don't silently fail it

**Why fourth:** this is the PRD requirement most in tension with the locked dataset. The dataset is fixed at 60 premises / 4 transformers — there is no `n_transformers=1` feed to run against without fabricating one.

**Task 4.1 — Make the call with the team, not alone.** Three honest options:
  - (a) Accept the deviation and record it: the engine's *code* has no hardcoded transformer count (true today — `config.rating_kva` is a dict, any size), but the *shipped dataset* only exercises 4. Demonstrate independence by constructing a small synthetic feed (a `MeterFeed` implementation with N=1 and N=2 transformers, hand-built, not derived from `WhitefieldFeed`) purely to prove the code path, separate from the real demo data.
  - (b) Build a feed adapter that partitions the real 60 premises into 1 or 2 transformer groups (re-deriving loss/phase/rating for the merged group) — more work, stays on real data.
  - (c) Drop this integration check explicitly as out of scope for the demo dataset and say so in `DECISIONS.md`, rather than leaving it failing silently.

**Task 4.2.** Whichever is chosen, add the test and get it green, or record why it's explicitly waived.

**Gate:** #9 either passes against a real (even if synthetic) 1/2/4-transformer feed, or `DECISIONS.md` states in one paragraph why it's out of scope for this dataset.

**Effort:** (a) half a day; (b) 1–2 days; (c) 15 minutes plus the team conversation.

</details>

---

## Phase 5 — Config format: pydantic + `config.yaml`

**Why fifth, not earlier:** purely a tooling/spec-conformance gap. Nothing about correctness depends on it — the dataclass works today and every test passes against it. Do this once the numbers above are trustworthy, not before.

**Task 5.1.** Convert `engine/config.py`'s `Config` dataclass to a `pydantic.BaseModel` (or `pydantic.dataclasses.dataclass`) with the same fields and defaults. Validate this doesn't change any computed value — rerun the full scenario suite after the conversion and diff outputs against pre-conversion baselines.

**Task 5.2.** Emit a `config.yaml` with the shipped defaults, and load from it if present, falling back to code defaults otherwise (so `CF1` — runs with zero CLI args — still holds either way).

**Task 5.3.** Record the switch in `DECISIONS.md` (there wasn't a decision recorded for using a dataclass in the first place — retroactively close that gap too).

**Gate:** `CF1` passes with the new format; full regression suite (115+ tests) stays green; `DECISIONS.md` updated.

**Effort:** half a day.

---

## Phase 6 — Fill the two untested failure paths (§12)

**Why sixth:** these are specified failure behaviors nobody has actually forced and observed. Low probability of hiding a real bug, but currently just "assumed."

**Task 6.1.** Force a SQLite write failure (e.g. point `Persistence` at a read-only path, or monkeypatch `execute` to raise once) and confirm: retry once, then raise — never silently continue with unpersisted state.

**Task 6.2.** Construct a meter feed with a genuine gap (a house or block missing) and confirm the engine raises at startup validation, not mid-run.

**Gate:** two new tests, both passing, both demonstrating the specified behavior rather than assuming it.

**Effort:** half a day.

---

## Phase 7 — LightGBM and LLM, only if time remains

Per the master plan's own cut order (#4, #5) and this project's stated priorities, these are last on purpose. Do not start this phase before Phases 1–3 are green.

**Task 7.1 (LightGBM).** `pip install lightgbm`, flip `config.lightgbm_enabled = True`, confirm `rank()` trains and predicts without raising, confirm `feature_importances()` returns real values, confirm the disabled-fallback path still works identically to today. This makes the slide's "ML (LightGBM)" row literally true instead of aspirational.

**Task 7.2 (LLM).** Resolve the open `"margin"` vs `bid_aggression` field-name mismatch in `algo/llm.py` with whoever owns `domain.py`. Then either wire `_call_llm` to a real provider, or explicitly decide to leave it stubbed and update the pitch slide's "Type" column to say so (as discussed).

**Gate:** whichever of the two you do, the acceptance checks in PRD §9 (clamped output, timeout produces `llm_enabled=False`-identical run) pass against the real (not stubbed) path.

**Effort:** LightGBM half a day; LLM highly variable depending on provider choice — budget it separately if you commit to it.

---

## Sequencing summary

| Phase | Gate | Est. effort | Blocks |
|---|---|---|---|
| 1 — FL4 check | holds to 1e-6, or fixed until it does | 0.5–1 day | Everything downstream trusts this number |
| 2 — run_summary.json | all required fields present, D1-safe | 0.5 day | Phase 3's reporting |
| 3 — breach resolution (#6) | reshape vs fallback counts reported honestly | 0.5–1 day | — |
| 4 — topology independence (#9) | passes on a real feed, or explicitly waived | 0.25–2 days (decision-dependent) | — |
| 5 — config format | pydantic + yaml, regression clean | 0.5 day | — |
| 6 — untested failure paths | 2 new tests passing | 0.5 day | — |
| 7 — LightGBM / LLM | real path passes §9 checks, or slide corrected | 0.5 day + variable | Nothing — last on purpose |

**Total to a defensible "definition of done":** roughly 3–4 focused days through Phase 6. Phase 7 is optional and explicitly last.

**Do not skip Phase 1 to get to a "greener" checklist faster.** Every other number in this project's pitch depends on energy actually being conserved, and right now nobody has checked it.
