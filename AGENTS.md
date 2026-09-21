# AGENTS.md

This file provides guidance to agents when working with code in this repository.

## Commands

```bash
# Full test suite (pytest, all tests must pass — 1 skip is intentional: live Groq smoke test)
python -m pytest tests/ -v

# Run a single test file
python -m pytest tests/test_phase1_market_loop.py -v

# Run a single test by name
python -m pytest tests/test_phase1_market_loop.py::test_bus_dispatches_in_subscription_order -v

# Grid-agent sub-suite (sentinel, battery, flow, health)
python -m pytest tests/grid/ -v

# Shell-based full checklist runner (checks scipy first, exits 1 on any failure)
./run_tests.sh             # full (~40s)
./run_tests.sh --quick     # skips slow scenario/derate sweeps (~10s)

# Run server locally
uvicorn server.app:app --reload
```

> `scipy` is **not optional** — if it is missing, the reshape LP silently returns `feasible=False`
> on every breach and the flow agent does nothing. `run_tests.sh` exits 1 if scipy is absent.

## Architecture — Non-Obvious

- **Engine runs once at server boot**, result is shared/immutable. `server/app.py` streams
  pre-computed blocks over WebSocket; it never re-simulates per connection (D21).
- **Tick loop sequence** (in `engine/sim/runner.py`):
  `ticks → orders → clear → check → [reshape → apply (not re-clear) → fallback] → age → settle`
- **`ReshapePlan.constrained_trades`** is the LP's decision as trades and must be **applied directly**
  via `MarketAgent.apply_reshape()` — never re-run through the auction (D20).
- **`Trade.quantity_kwh` is already net of curtailment** — `curtailed_fraction` is provenance only,
  not a multiplier. Use `engine/trades.py:delivered_kwh()` rather than reading either field directly.
- **`AgeingResult.ageing_adder`** (the property) returns `active_adders` (computed in t-1), NOT
  `adders` (current block). Settlement must bill from `active_adders` to satisfy HL4.
- **`engine/physics.py:apparent_kva()`** is the single kW→kVA conversion. Never write `/ 0.95`
  inline; the power factor must come from `config.power_factor`.
- **Transformer ratings** in `transformer_registry.json` (500/250 kVA) are overridden in
  `config.rating_kva` to 125/63/63/63 kVA to produce breaches. Do not change the JSON.
- **Block = 1 hour** (`block_minutes=60`). Never use the literal `0.25` (stale 15-min block
  assumption). Everything per-block must scale from `config.block_hours`.

## Settings & Configuration

- **Deployment env vars** → `engine/settings.py` (API keys, ports, CORS, LLM enable)
- **Simulation parameters** → `engine/config.py` + `config.yaml` (tariffs, limits, topology)
- These two are kept deliberately separate — `config.yaml` is version-controlled;
  `.env` is never committed.
- `Config` uses `pydantic.dataclasses.dataclass` when pydantic is available, falls back to stdlib
  `dataclass`. **`dataclasses.replace()` and `fields()` are used everywhere** — do not switch to
  `pydantic.BaseModel` (it would break all tests and scenario utilities).
- Unrecognised keys in `config.yaml` **raise** (not silently ignored).
- `LLM_ENABLED` requires **both** `URJASETU_LLM_ENABLED=true` AND a non-empty `GROQ_API_KEY`.
  Setting only one of them produces `enabled: false`.

## Invariants — Never Break

| ID | File | What it guarantees |
|----|------|--------------------|
| ST1 | `engine/agents/settlement.py` | Money conservation across all trades |
| FL4 | `engine/algo/reshape_lp.py` | Energy conservation (gen = consumption + losses) |
| HL1-HL4 | `grid/health.py` | Thermal monotonicity, bounds, no retroactive adders |
| MK1-MK3 | `engine/algo/auction.py` | Auction qty ≤ min(offered,bid), price in bounds, deterministic |
| SN1-SN2 | `grid/sentinel.py` | Sentinel is pure, severity ≥ 1.0 |
| LM1 | `engine/algo/llm.py` | LLM may only move 4 clamped fields in `_CLAMPS`; `battery_reserve_frac` is frozen |

## Code Style

- Every module starts with `from __future__ import annotations`.
- Domain types in `engine/domain.py` are **frozen dataclasses** — state changes create new
  instances. This file is the system's published contract; treat it as append-only.
- **Broad `except Exception`** at LLM call sites is intentional (graceful fallback).
- Module docstrings describe what the file used to get wrong and why it was fixed — follow
  this pattern when fixing bugs.
- New settings: add to `engine/settings.py` with env-var + default, and to `.env.example`
  with a comment.
- New packages: add to `requirements.txt` with a comment explaining why.
- Tests may be run standalone with `python3 tests/<file>.py` (no pytest required) — each test
  file adds its own `sys.path.insert` for this reason.

## Testing Conventions

- **Network calls must be monkeypatched** — no real HTTP in tests.
  Use `unittest.mock.patch` or inject a `transport=` mock.
- The **1 intentional skip** is the live Groq smoke test in `tests/test_groq_requests.py`.
- Grid-agent tests live in `tests/grid/` and have their own `run_all.py`.
- Use `dataclasses.replace(DEFAULT, ...)` to construct test configs, not `Config(...)` from
  scratch (avoids coupling to default values).
- `validate_feed=False` on `Runner` is the only way to skip feed pre-validation
  (only for tests with partial/stub feeds).

## Key Files Quick Reference

| Path | Role |
|------|------|
| `engine/domain.py` | Frozen dataclasses — the system's shared contract |
| `engine/config.py` + `config.yaml` | Simulation parameters (tariffs, limits) |
| `engine/settings.py` | Deployment env vars (API keys, ports) |
| `engine/physics.py` | Single kW↔kVA conversion — use `apparent_kva()`, never `/ 0.95` |
| `engine/trades.py` | Single source of truth for `delivered_kwh()` |
| `engine/algo/llm.py` | All LLM calls; cuttable — engine runs fully with no API key |
| `engine/algo/thermal.py` | IEEE C57.91 hot-spot + ageing (F_AA = 1.0 at 110 °C) |
| `engine/sim/runner.py` | The tick loop |
| `grid/sentinel.py` | Pure constraint checker (SN1: never mutates state) |
| `grid/health.py` | Transformer thermal health + HL1-HL4 |
| `grid/flow.py` | Reshape + fallback curtailment |
| `server/app.py` | FastAPI + WebSocket stream |
| `DECISIONS.md` | **Read before changing engine architecture** |
