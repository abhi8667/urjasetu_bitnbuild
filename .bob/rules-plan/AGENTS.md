# Plan Mode — UrjaSetu Architecture Constraints (Non-Obvious Only)

## Coupling That Is Not Obvious From File Structure
- **`grid/health.py` imports `AgeingResult` from `engine/domain.py`** — it does NOT define its
  own result type. Any plan that adds a field to `AgeingResult` must account for settlement,
  health, and runner simultaneously.
- **`GridSentinel` is stateless by invariant (SN1)**. It must never accumulate state between
  calls. Plans that add caching or per-block memory to sentinel break SN1.
- **`MAX_CLEARING_PASSES = 2`** in `engine/sim/runner.py` is hardcoded, not configurable.
  This is intentional — see runner.py comment. Do not make it a config field.
- **Reshape LP uses `ReshapeLimits.block_hours`** (carried per-call), not the module constant
  `BLOCK_HOURS`. The module constant is a last-resort default only. Plans that change block
  duration must propagate through the `ReshapeLimits` object.

## Architectural Constraints
- **Precomputed + streamed, not live**: the simulation is one immutable shared object. Any plan
  involving live per-connection simulation would require a complete rewrite of `server/app.py`
  and `server/simulation.py`.
- **Feed is pre-validated in full before block 0** (`WhitefieldFeed.validate()`). A gap discovered
  mid-run is a design violation. Plans for feed changes must account for the validator.
- **`config.yaml` unrecognised keys raise** — a plan adding new config fields must add them to
  both `engine/config.py:Config` AND `config.yaml`, or the file will reject existing configs.
- **`pydantic.dataclasses.dataclass` not `BaseModel`** — the entire test suite uses
  `dataclasses.replace()`. Any plan to migrate config to `BaseModel` would break every test.

## Performance Constraints
- A 30-day run (720 blocks) completes in ≈3 seconds. Feed pre-validation adds 0.12 s and also
  warms the tick cache — it is not skippable in production.
- The feed's `_day_scale` multiplies each day's readings by a seasonal irradiance/demand
  factor clamped to [0.35, 1.45] gen and [0.85, 1.15] load. Plans that change data generation
  must preserve these bounds to keep the 30-day run deterministic.

## Topology Independence (D21)
- The engine iterates whatever transformers the registry provides — no module hardcodes 4.
  The only place "DT-1..DT-4" appears in engine/grid code is the default value of
  `config.rating_kva`. Plans for topology changes only need a new `MeterFeed` implementation.

## Temporal Disjointness of Trading and Stress
- P2P trades clear 09:00–15:00; loading breaches occur 18:00–21:00. The ageing adder
  collects only ~₹0.71 over 30 days because the two windows don't overlap. A plan to connect
  them properly requires making the *forward* evening adder an input to the prosumer's
  store-or-sell decision — documented in DECISIONS.md D14 as a design change, not a knob.
