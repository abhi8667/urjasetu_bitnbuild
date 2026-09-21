# Agent Mode — UrjaSetu Coding Rules (Non-Obvious Only)

## Before Writing Anything
1. Read the target source file first — never guess at existing structure.
2. Read `DECISIONS.md` if the change touches engine architecture or config.

## Invariant-Safe Patterns
- `Trade.quantity_kwh` is already net of curtailment. Use `engine/trades.py:delivered_kwh()`.
  Do not multiply by `(1 - curtailed_fraction)` — that gives ρ².
- `AgeingResult.ageing_adder` (property) → `active_adders` (t-1). Settlement bills from this.
  `AgeingResult.adders` is the FORWARD figure (for t+1). Never swap them.
- `ReshapePlan.constrained_trades` must be applied via `MarketAgent.apply_reshape()`, never
  re-run through the auction. Re-clearing discards the LP's per-trade allocation.
- All kW→kVA conversions: call `engine/physics.py:apparent_kva(net_kw, config.power_factor)`.
  Writing `/ 0.95` anywhere is a bug; `engine/sim/baseline.py` had one and it caused a 2×
  discrepancy in loss-of-life between the two sides of a comparison.

## Settings Rules
- New deployment setting → `engine/settings.py` + `.env.example` (with comment).
- New simulation parameter → `engine/config.py` (field with default) + `config.yaml`.
- These two files are kept separate on purpose — do not merge them.
- `Config` is a `pydantic.dataclasses.dataclass` that falls back to stdlib. All tests use
  `dataclasses.replace(DEFAULT, ...)`. Never switch to `pydantic.BaseModel`.

## LLM Constraints (LM1)
- The LLM may only move the 3 fields in `engine/algo/llm.py:_CLAMPS`.
- `battery_reserve_frac` is in `_LM1_FROZEN` — the LLM must not touch it, ever.
- Every LLM call site must have a broad `except Exception` fallback returning the
  deterministic answer. The engine must run fully with `GROQ_API_KEY` unset.

## Testing
- Mock all network calls — `patch("urllib.request.urlopen", ...)` for LLM, `httpx` mocks for
  watsonx/IBM App Config.
- Test configs: `dataclasses.replace(DEFAULT, field=value)`, never `Config(field=value)`.
- `validate_feed=False` on Runner only for tests with partial/stub feeds.
- After every change: `python -m pytest tests/ -v` — all pass, 1 skip expected.

## Commit Format
```
<verb> <what> [(<scope>)]

<optional why, not what>

Co-authored-by: Bob <bob@ibm.com>
```
