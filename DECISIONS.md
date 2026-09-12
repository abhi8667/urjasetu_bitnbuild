# DECISIONS

Choices the spec left open, or that the locked dataset forced. One line each on
what we chose and why, so nobody re-litigates it at hour 29 and so a judge gets
a straight answer.

**The dataset is locked.** Every gap between `data/` and the engine spec is
closed in code — `engine/config.py` or `engine/feed.py` — never by regenerating
a CSV.

---

### D1 — Transformer ratings are overridden in config, not read from the JSON

`transformer_registry.json` rates the four DTs at 500/250/250/250 kVA. Measured
against the telemetry, peak loading is **21–29%**. No breach is reachable at any
defensible `loading_limit`, which would leave the sentinel, the flow agent, the
reshape LP and the ageing signal as dead code — that is the entire project.

`config.rating_kva` overrides them to **125/63/63/63 kVA**, standard Indian LT
distribution sizings for 12–17 premises per DT. Measured over a 30-day run:

| DT | premises | rating | peak K | breach blocks / 720 |
|---|---|---|---|---|
| DT-1 | 12 + hub | 125 kVA | 1.27 | 63 |
| DT-2 | 17 + hub | 63 kVA | 0.95 | 0 |
| DT-3 | 14 + hub | 63 kVA | 1.29 | 69 |
| DT-4 | 17 + hub | 63 kVA | 1.32 | 30 |

Breaches land in the **18:00–21:00 evening peak**, on every simulated day, 1–3
transformers at a time. DT-2 never breaches, which gives the compare screen a
healthy control. `config.derate_factor` still forces one on demand for the demo.

**Consequence for the narrative:** the breach is evening-peak-driven, not
solar-export-driven. Daylight surplus is only 195 kWh/day against 2060 kWh of
demand and peak reverse flow is 19.8 kW, so a daylight export breach is not
honestly reachable on this data. The story is therefore: daytime surplus is
stored, the evening peak breaches, and the flow agent discharges batteries and
curtails flexible EV charging instead of cutting anyone off. That is closer to
how Indian LT transformers actually fail, and it is the master plan's own
sentence — "the energy comes back out at the evening peak."

### D2 — A block is one hour, not fifteen minutes

The telemetry is hourly. Interpolating to 15 minutes would invent precision we
do not have. `config.block_minutes = 60`, so 24 blocks a day and 720 in a
30-day run. Anything per-block scales off `config.block_hours` — never a literal
`0.25`. The evening price window moved from blocks 68–84 to `config.evening_blocks`
= 17–21.

### D3 — 30 days are built by scaling the one measured day

`telemetry/` covers a single Saturday. Replaying it unchanged would make every
day identical and the 30-day ageing comparison meaningless. `feed._day_scale`
multiplies each simulated day by that calendar day's irradiance and demand from
the year-long aggregate, relative to 12 Sep, clamped to [0.35, 1.45] on
generation and [0.85, 1.15] on load. Cloudy days come out cloudy and the
seasonal shape survives. Flag it as "one measured day, seasonally scaled" in the
run summary and on the provenance slide.

### D4 — The four EV hubs are modelled as pseudo-premises

The hubs draw 18–44 kW on their transformer but sit behind no meter. Smearing
that across real households would corrupt their bills. Each hub becomes a
`House` with no PV and no battery (`EVHUB-DT1`…`DT4`), so it loads the DT for
the sentinel and pays a bill of its own. 60 metered premises + 4 hubs = **64
nodes**. Toggle with `config.include_ev_hubs_as_houses`.

### D5 — Consumption in the dataset is gross, and maps straight to `MeterTick`

`smart_meters.csv:current_consumption_kw` is gross, not net of own generation —
a solar premises at noon shows consumption, generation and export at the same
time. No de-netting in the feed.

### D6 — Fields the registry has no column for are synthesised in the feed

| Field | Source |
|---|---|
| `House.phase` | round-robin A/B/C **within each transformer**, so a balanced street never shows a phase breach purely from meter numbering |
| `House.battery_max_kw` | 5.0 kW — what `home_batteries.csv` actually ranges to. `data/README.md` previously claimed ±11 kW; the data does not support it |
| `House.retail_tariff` | effective INR/kWh under BESCOM's **stepped** slabs on (daily import × 30). Billing the whole draw at the top band puts all 60 premises on ₹8.40; stepped gives ₹7.18–8.40 |
| `Transformer.rated_top_oil_rise_c` / `rated_hotspot_rise_c` | 55 °C / 80 °C, IEEE C57.91 oil-immersed distribution defaults |
| `Transformer.rated_life_hours` | 180,000 h (C57.91 normal insulation life) |
| `Transformer.replacement_cost_inr` | ₹2.5 lakh, mid-point of the ₹2–3 lakh the master plan quotes |

### D7 — Stdlib only in the scaffold

`pydantic`, `numpy`, `scipy` and `pytest` are all permitted by the PRD and all
absent from a clean machine. `config.py`, `domain.py`, `feed.py` and the algo
stubs use the standard library alone, so all four tracks run `python
tests/test_feed_contracts.py` at hour 0 without installing anything. D brings
`scipy` in with the real LP; B swaps `Config` to pydantic when validation
earns its keep. Neither changes a signature.

### D7a — pydantic and YAML are optional, which is how §8 and D7 both hold

PRD §8 asks for "a single `config.yaml`, loaded into a `pydantic` model". D7
above commits the engine to running on a machine with nothing installed. Those
pull against each other, and the resolution is that neither package is a
dependency:

- `Config` is declared with `pydantic.dataclasses.dataclass` when pydantic is
  importable and the stdlib `dataclass` when it is not. The pydantic version is
  a genuine drop-in — `dataclasses.replace()` and `fields()` both keep working,
  which matters because every test and scenario in this repo uses `replace()`.
  A `pydantic.BaseModel` would have broken all of them.
- `config.yaml` is read when PyYAML is importable and the file exists. Absent
  either, the declared defaults apply unchanged.

CF1 therefore holds in all four combinations, and the no-packages case — the
one a teammate has on a fresh clone — is asserted in a subprocess with both
imports blocked rather than assumed.

Two things the file does beyond §8, because the failure mode is silent:

- An **unrecognised key raises**. A typo like `loadng_limit: 0.9` would
  otherwise be ignored and the run would proceed on the default, looking
  correct.
- **Range validation runs with or without pydantic.** pydantic checks types;
  nothing checks that `loading_limit: -1` is nonsense. The bounds covered are
  those that produce a silently wrong run rather than a crash.

`data_dir` is stored relative and resolved against the repo root — an absolute
path baked in by whoever generated the file would fail on every other machine.

The conversion changed no computed value: a 30-day run before and after is
identical to the digit (5,582 trades, 4,511.5 kWh, ₹4.76 mean clearing price,
25.1 h of transformer life saved).

### D8 — The feed's RNG is separate from the engine's

PRD §3.4 wants one seeded `numpy.random.Generator` on the tick path. The feed's
`random.Random(config.seed)` is used only for forecast noise and never by an
agent, so determinism (D1) holds: two runs with the same config produce
identical ticks. Verified by `test_feed_is_deterministic`.

### D9 — Provenance: the data is modelled, not measured

`fetch_real_data.py` never calls NASA POWER, Open-Meteo or PVGIS — those
functions print the URLs you would use. The CSVs come from published monthly
averages plus seeded noise, and `grid_price_inr_per_kwh` is the KERC ToD rate
plus σ≈₹0.30 of noise, not the published tariff verbatim. Tariffs and P2P
charges themselves are used verbatim. Say "physically modelled from published
averages"; no open Indian dataset pairs household load with rooftop PV at meter
level, and naming that limit reads as rigour.

### D10 — The compare screen leads with life saved, not bill saved

P2P can cover about **9.5%** of daily demand on this data (195 kWh surplus vs
2060 kWh demand), so "household bill down" will be a modest number. 99% of that
surplus is matchable within the same transformer, so the KERC same-DT constraint
costs almost nothing. Put transformer life saved and DISCOM revenue on screen
largest; quote the bill saving honestly as a percentage of the daytime bill.
`config.max_bid_kwh_per_block` (3.0) caps how much any one buyer can take per
block, so scarce surplus is competed for rather than swallowed by the first bid.

### D11 — Coordinates, building type and loss reach the engine as `Site`, not on `House`

The registry carries real Whitefield lat/lon for all 60 premises, a
`building_type` of `res`/`apt`/`com`, and a per-premises `transmission_loss_pct`
of 3.25–6.75% (exactly `3 + 0.02 × distance_m`). A needs the first two for the
isometric layout and its sprites; settlement needs the third, and FL4 has no
losses term without it.

None of them belong on `House` — Contract 3 is frozen and no agent reasons over
a coordinate. They are exposed as `domain.Site` / `domain.TransformerSite` via
`feed.sites()`, `feed.transformer_sites()` and `feed.transmission_loss_pct()`.
A reads coordinates off the `scene` payload B assembles from these, never out of
the JSON, so the fixture cannot drift from the engine.

`TransformerSite.registry_kva` deliberately keeps the *installed* rating
(500/250 kVA) visible next to the modelled one, so D1 stays auditable rather
than looking like a file someone quietly edited.

### D12 — The CSVs are not updated, and here is when that would change

Nothing in the dataset is wrong: every join, conservation identity and physical
convention checks out. It is insufficient in places, and insufficiency is closed
in the feed. Editing a generated CSV by hand would break its reproducibility
from its own script, which is the thing that makes provenance answerable in Q&A.

The one legitimate reason to regenerate is **rooftop solar penetration**. P2P
covers about 9.5% of demand at 18 of 60 premises, so the household bill saving
on the compare screen will be modest. `data/scripts/device_registry.py:88`
(`random() < 0.30`) is the lever; 0.45 takes the market to roughly 15%.

Gate that on the hour-24 compare numbers, not on a hunch. It re-rolls all five
telemetry files and invalidates the tuned ratings, breach counts and tariffs
recorded above — budget an hour for the re-tune, and do it once.

### D14 — The ageing adder prices average wear per kWh delivered, not marginal wear

PRD §6.6 defines the adder from `loss_hours(with_trades) - loss_hours(without_trades)`.
That quantity is **identically zero** in this model and cannot be otherwise: a
P2P trade is a financial contract between two premises on one transformer and
moves no power that was not already flowing. C's health agent encoded this
honestly — `load_with_kw = base_kw` and `load_without_kw = base_kw` — and the
adder was exactly ₹0.0000/kWh on every transformer for every block.

The adder now prices the **average** wear on the delivering transformer:

```
adder = (loss_of_life_hours_this_block / rated_life_hours)
        * replacement_cost_inr / kWh_delivered_on_that_transformer
```

The divisor is DT throughput, not traded kWh. Every loading breach on this
street falls in 18:00–21:00, when no trade clears at all, so a traded-kWh
divisor zeroes the adder precisely when the iron is being hurt most. F_AA is
exponential in hot-spot temperature, so the signal still climbs steeply exactly
when it should: ₹0.001/kWh on a cool transformer, ₹0.022/kWh on the hottest.

**Known limitation, state it before a judge does.** Trading happens 09:00–15:00
and stress happens 18:00–21:00, so the two are temporally disjoint on this
street and a per-kWh settlement charge collects only ₹0.71 over 30 days. The
signal is real and correctly signed, but it is not yet what changes behaviour —
the battery reserve policy (D15) is. Connecting them properly means making the
*forward* evening adder an input to the prosumer's store-or-sell decision, which
is a design change, not a tuning knob.

### D15 — Daylight surplus is reserved to discharge into the evening peak

`strategy.battery_reserve_frac` (0.20) of each battery-equipped premises'
forecast surplus is held back from the market and stored in its own battery.
The flow agent discharges it when a loading breach appears.

Without this the reshape has a lever with nothing behind it: selling every kWh
at midday leaves the batteries empty at 19:00, which is the only hour the
transformers are actually in trouble. Own-battery only — no claims, no custody —
so this path cannot break FL4.

Measured over 30 days: 61 kWh stored across the eight batteries, 43 reshapes
applied where there were previously 0, and **24.0 hours of transformer life
saved (3.9%)** against the unprotected net-metering baseline. That is the first
non-zero value the project's central claim has ever produced.

### D16 — Loading is measured in kVA everywhere, at one power factor

The sentinel, the flow agent's LP limits, the health agent and the baseline all
now divide net kW by the same `POWER_FACTOR = 0.95`. They did not: the LP's
limit was 5% looser than the sentinel's, so a reshape came back feasible while
the re-check still breached; and the health agent measured in kW while the
baseline measured in kVA, which compounded through the exponential F_AA into a
2× discrepancy in loss of life between the two sides of a comparison that is
only meaningful apples to apples.

If one of these changes, all four change together.

### D17 — The reshape LP was solving the wrong problem: its config override never fired

`algo/reshape_lp.py` resolved its constants through

```python
from engine import config as _cfg
def _cfg_get(name, default):
    return getattr(_cfg, name, default)
```

`_cfg` is the config **module**, which has no attribute called
`RESHAPE_BLOCK_HOURS` or `RESHAPE_VOLTAGE_BAND` — those live on the `Config`
**instance**, under different names. So every lookup fell through to its
hardcoded default, silently, from the day it was written. The LP ran on:

| Constant | LP used | Engine config | Effect |
|---|---|---|---|
| `BLOCK_HOURS` | 0.25 | 1.0 | battery bound `max_kw * 0.25` = **a quarter of the real one** |
| `VOLTAGE_BAND` | 0.05 | 0.06 | tighter band than the sentinel enforces |
| `LOADING_LIMIT` | 0.90 | 1.0 | (harmless — genuinely read from `limits`) |

`BLOCK_HOURS = 0.25` is the 15-minute block from before D2, the same stale
literal already found in the thermal model.

`block_hours` and `voltage_band` are now carried on `ReshapeLimits` and read
per call, so there is one source of truth. The module constants remain only as
a fallback for a caller that passes nothing.

**The fix reduces the reshape success count and that is the correct direction.**
Over a 30-day run at the default derate:

| | reshapes applied | fallbacks | discharged | transformer life |
|---|---|---|---|---|
| Before (stale 0.25) | 43 | 73 | 69.6 kWh | 595.2 h |
| After (correct 1.0) | 24 | 61 | **140.2 kWh** | **594.1 h** |

The old code "succeeded" more often because a 1.25 kWh battery bound is
trivially satisfiable and barely moves the loading. The corrected LP attempts
the real problem, succeeds less often, and moves twice the energy when it does.

### D18 — Most loading breaches on this street are genuinely unreshapable, and that is the honest answer

At a 60% derate, 84 of 261 loading breaches return `feasible=False`. Every one
was audited against the discharge its transformer's batteries actually had at
that moment, and every one had an overload beyond what those batteries could
cover. The refusals are physics, not a formulation bug.

Two structural reasons, both already recorded above:

- Loading breaches fall in 18:00–21:00 (D1), when **no trade clears at all** —
  so trade curtailment, the LP's other lever, has nothing to work with. Battery
  discharge is the only instrument available.
- Eight batteries at 5 kWh per block of discharge, spread across four
  transformers, cannot absorb a 23 kW mean overload.

`tests/test_breach_resolution.py` therefore reports the reshape/fallback split
at three derate levels rather than a single "resolved" tick. PRD §10.6 as
written is close to unfalsifiable — `fallback_curtail` computes a retention
fraction that lands exactly on the limit, so it always resolves — and it passed
while the LP contributed nothing at all.

### D19 — Persistence writes are atomic per block, retried once, then fatal

PRD §12 specifies "SQLite write failure → retry once, then raise — do not
continue with unpersisted state". Neither half existed: there was no retry and
no exception handling anywhere in `persistence.py`.

The atomicity half was the more dangerous one and was not obvious from the
spec line. `write_block` issued four separate `executemany` calls and then
committed, with a nested `save_transformer_state` **committing partway
through**. A failure mid-block therefore left some tables written and others
not — and because the connection kept an open transaction, the *next* block's
commit would sweep those leftovers in alongside its own. "Do not continue with
unpersisted state" has to mean a block lands whole or not at all.

Every write now runs as one transaction through `_atomic()`, using
`with self.db` so a failed attempt rolls back and leaves nothing behind.
`transformer_state` is appended to that same statement list rather than
committed separately: it is the only table that survives a restart, and PS1
(resume with no gap and no double count) depends on it being exactly level with
the block it describes, never ahead or behind.

Retry policy follows the same rule the flow agent learned the hard way:

- **Retried once** — `OperationalError`, `DatabaseError`. Locked, busy, a disk
  hiccup. Two attempts total, never an unbounded loop against a disk that is
  genuinely gone.
- **Never retried** — `ProgrammingError`, `IntegrityError`, `InterfaceError`,
  `NotSupportedError`. Bad SQL or a violated constraint is a bug; retrying one
  hides it briefly and then reports the wrong cause.

After two failures, `PersistenceError` is raised with the original exception
chained. `Persistence.retries_used` counts second attempts — nonzero is not a
failure, but a run that quietly retried a hundred times is telling you
something about the disk.

`tests/test_persistence_failures.py` forces each case rather than assuming it:
a transient failure that recovers, a permanent one that raises, exactly two
attempts, a programming error surfacing unretried on the first attempt, a
partial block write leaving all four tables empty, and a genuinely read-only
database on disk.

### D20 — The feed is validated in full before block 0

PRD §12: "Meter feed gap → raise at startup during feed validation, not
mid-run", and "the engine validates the entire meter feed before block 0. A run
that starts must be able to finish."

No validation existed. A gap surfaced as a `KeyError` at whatever block it
happened to hit — block 400, say — by which point four hundred blocks of
compute are spent, the database holds a partial run, and the cause is hundreds
of blocks behind the symptom.

`WhitefieldFeed.validate()` now checks the whole feed and raises
`FeedValidationError` on the first problem, with the block, the premises and
what was wrong. It covers the defects that would otherwise appear mid-run as a
crash, a silent zero, or a physically impossible number:

- a premises missing from any block, or a reading for one the registry does not
  know
- negative energy, NaN, an ambient temperature outside [-50, 70] °C
- a premises on a transformer the feed does not define
- equipment flags contradicting their capacities (`has_pv` with `pv_kw = 0`)
- a zero retail tariff, an empty premises list, a zero-block feed

**Entire, not sampled.** Checking all 720 blocks costs **0.12 s**, which is
cheap enough that there is no argument for sampling — and it warms the feed's
tick cache, so the run that follows is faster for having been validated.

`Runner` calls it before block 0 and records the report on
`runner.feed_validation`, so a run can show that its feed was verified rather
than assumed. `validate_feed=False` exists for tests that deliberately drive a
partial or stub feed, and is the only way to skip it.

### D21 — Topology independence (§10.9) is deliberately scoped out, not overlooked

PRD §10.9 asks for a full run at `n_transformers` set to 1, 2 and 4 without
code changes. **We are not doing this, by decision, and the reason is the
dataset rather than the engine.**

What is actually true, and was verified rather than assumed:

- **No module assumes a transformer count.** Every one iterates whatever the
  registry provides. The only place `DT-1..DT-4` appears in `engine/` or
  `grid/` is the default value of `config.rating_kva` — a config default, not
  logic. A run with one, two or four entries in that dict completes
  identically, with unlisted transformers falling back to their registry
  ratings.
- **The shipped dataset is fixed at four transformers and sixty premises.**
  `data/` is locked (see the top of `data/README.md`). There is no
  one-transformer feed to run against, and the transformer count comes from
  the registry, not from configuration.

Satisfying §10.9 literally would therefore mean fabricating a synthetic feed
whose only purpose is to exercise a code path we can already show is
count-agnostic, or building an adapter that repartitions sixty real premises
onto fewer transformers and re-derives their losses, phases and ratings. Both
add a second topology to maintain, and neither makes the demo better or the
physics more honest.

**So the engine ships at 9 of 10 integration checks, with this one waived.**
That is a scoping decision, recorded here so it is answerable rather than
discovered. The honest sentence, if asked: *the engine is topology-independent
— nothing in it assumes four transformers — but the dataset we validate against
has four, so that is the topology we claim.*

If a second topology ever becomes genuinely useful, D21 is the entry to revisit,
and the work is a `MeterFeed` implementation, not a change to any agent.


---

## D15 — scipy is a hard dependency, and its absence must be loud

`engine/algo/reshape_lp.py` needs `scipy.optimize.linprog`. The import was lazy
and `grid/flow.py` caught the resulting `ModuleNotFoundError` in a blanket
`except Exception`, returning `feasible=False`. On a machine without scipy the
entire reshape path therefore did nothing, silently — 270 breaches out of 270
fell through to curtailment, `battery_discharged_kwh` was 0.0, and the headline
check ("baseline ages faster than P2P") passed *vacuously* at 0.0 hours saved,
because ≥ is satisfied by equality when both sides measure the same unreshaped
street.

`run_tests.sh` compounded it by SKIPping five of twelve suites — every
grid-agent test and every PRD integration check — and still printing "0 failed".

Now: `requirements.txt` declares it, `grid/flow.py` re-raises `ImportError` as a
`RuntimeError` naming the fix, and `run_tests.sh` exits 1 rather than skipping.
A missing dependency is a failure, not a quieter test run.

## D16 — one power factor, in one place

A transformer is rated in kVA; the meters report kW. The conversion was a
module-level literal `0.95` in `grid/flow.py` and `grid/health.py`, an inline
`/ 0.95` in `engine/sim/baseline.py`, and **absent entirely** from
`grid/sentinel.py`, which compared kW against a kVA rating directly.

The sentinel therefore measured every transformer 5.3% cooler than the health
agent measured the same transformer in the same block, and the LP solved against
a third figure again. F_AA is exponential in hot-spot temperature, so that is
not a rounding difference — and a comparison between two differently-measured
sides is not a comparison.

`config.power_factor` declares it; `engine/physics.py` applies it; nothing else
writes it. `temp/checks/one_power_factor.py` enforces that by AST.

The correction makes loading figures ~5.3% higher across the board, so two grid
tests that had encoded the old arithmetic as their premise were updated: the
sentinel's 100 kW / 100 kVA boundary case (100 kW at 0.95 pf *is* 105.3 kVA and
genuinely overloaded — the kW at the limit is 95.0) and the hour-20 gate's
"shed 5.86 kW to reach K = 0.974" (the real kW ceiling is rating × limit × pf).

## D17 — `Trade.quantity_kwh` is already net of curtailment

`FlowAgent.fallback_curtail` scales `quantity_kwh` by ρ **and** records
`curtailed_fraction = 1 − ρ`. Settlement read the quantity directly and was
right; `GridSentinel` multiplied the two together and was wrong by ρ².

Convention: `quantity_kwh` is what the trade actually moves, and
`curtailed_fraction` is provenance, not a multiplier. `engine/trades.py` holds
the single function that answers the question, so it cannot be re-derived
differently at each call site.

## D18 — HL4 needs two adder dicts, not one

`AgeingResult.adders` is computed from the current block's thermal state and is
in force from t+1. Settlement was reading it, which priced a trade with
information that did not exist when it was struck — retroactive, and exactly
what HL4 forbids. `active_adders` (computed in t-1, in force now) is a separate
field, and the `ageing_adder` property points at it so the default path is the
correct one.

## D19 — eight bill components, and a cap that keeps CN2 true

`platform_fee` (Rs0.25/kWh) and `gst_pct` (5%) were config values that
settlement documented as part of its "six components, every one a config value"
and then never billed — no column, no code path. They are components 7 and 8.

Adding them puts real pressure on CN2 (a buyer's all-in must never exceed the
DISCOM's price). CN1 bounds the *energy* price; it says nothing about energy
plus six charges. Rather than let an assertion stop a run for a reason that is
not a bug, the stack is capped and the **ageing adder** is what gives way — it
is the discretionary price signal, not a statutory charge, and `max_ageing_adder`
already exists because the signal is understood to need bounding. Every trim is
totalled in `run_summary.ageing_adder_trimmed_inr` and surfaced in the API's
`warnings`, so the cap cannot quietly hide a settlement bug.

## D20 — the reshape's allocation is applied, not re-cleared

The runner used to re-run `MarketAgent.clear()` over the reshape's constrained
orders. A uniform-price auction sorts by price and walks the book, and the
reshape prices every constrained pair identically — so the auction rematched
seller A's retained energy against whichever bid sorted first. The LP's
per-trade allocation, which is *precisely* what satisfies the loading and
voltage rows it solved, was discarded before it reached the grid, and the
re-check then measured a different street than the one the LP had made feasible.

`ReshapePlan.constrained_trades` carries the decision; `MarketAgent.apply_reshape`
accepts it and still asserts MK1/MK2, which is what re-clearing was really
providing.

## D21 — the engine is precomputed and streamed, not simulated per connection

A 30-day run is 720 blocks in under three seconds and about 130 MB. The server
runs it once at boot and streams the recorded blocks over a WebSocket.

Simulating live per connection would buy nothing and cost a great deal: every
viewer would need its own engine state, a Render cold start would land mid-run,
and a restart would lose it. One shared immutable run means every viewer sees
the same deterministic sequence (D1), the service scales horizontally, and a
reconnect resumes rather than restarts — which matters because Render's free
tier sleeps after fifteen minutes.
