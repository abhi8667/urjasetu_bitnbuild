# UrjaSetu — Agent Mode Rules
# These rules apply ONLY in Agent mode (implementation, testing, committing).

## Before Writing Any Code
1. Read the relevant source file(s) first — never guess at existing structure.
2. Check `DECISIONS.md` if the change touches engine architecture.
3. Check `ibm-cloud-integration-plan.md` if the change touches IBM integrations.

## Code Standards
- Follow existing patterns exactly — same import style, same docstring format,
  same error-handling approach (broad `except Exception` at LLM call sites is intentional).
- All new settings must go in `engine/settings.py` with env-var + default.
- All new IBM credentials must also appear in `.env.example` with comments.
- New packages go in `requirements.txt` with a comment explaining why.

## Invariants — Never Break These
| Invariant | Location | What it guarantees |
|-----------|----------|--------------------|
| ST1 | `engine/agents/settlement.py` | Money conservation across all trades |
| FL4 | `engine/algo/reshape_lp.py` | Energy conservation (gen = consumption + losses) |
| HL1-HL4 | `grid/health.py` | Thermal monotonicity, bounds, no retroactive adders |
| MK1-MK3 | `engine/algo/auction.py` | Auction qty ≤ min(offered,bid), price in bounds, deterministic |
| SN1-SN2 | `grid/sentinel.py` | Sentinel is pure, severity ≥ 1.0 |
| LM1 | `engine/algo/llm.py` | LLM may only move 4 clamped strategy fields |

## Testing Requirements
- Run `python -m pytest tests/ -v` after EVERY code change.
- All 198 tests must pass (1 skip is intentional — live Groq smoke test).
- New IBM features must have tests in `tests/test_ibm_integrations.py`.
- Tests must never make real network calls — monkeypatch all HTTP boundaries.

## Git Commit Format
```
<imperative verb> <what changed> [(<scope>)]

<optional body: why, not what>

Co-authored-by: Bob <bob@ibm.com>
```
Examples:
- `Add IBM watsonx.ai LLM backend (Sub-Task 1)`
- `Add IBM App Configuration feature flags (Sub-Task 3)`
- `Suppress Code Engine keep-alive pinger (Sub-Task 5)`

## Push Checklist
Before every `git push`:
1. ✅ `python -m pytest tests/ -v` — 198 passed, 1 skipped
2. ✅ No `.env` file staged (`git status` check)
3. ✅ `ibm-cloud-integration-plan.md` updated if a sub-task was completed
4. ✅ Co-author line in commit message
