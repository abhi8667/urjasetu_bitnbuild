# UrjaSetu — General Bob Rules
# These rules apply to ALL modes (Ask, Agent, Plan).

## Project Identity
- This is **UrjaSetu** ("Bridge of Energy") — a production-grade autonomous
  multi-agent P2P energy trading platform for LT urban distribution feeders.
- Backend: Python 3.11 + FastAPI + SciPy. Frontend: React 18 + Three.js.
- Real dataset: 60 residential premises, 4 distribution transformers (Whitefield, Bengaluru).
- Live deployment: Vercel (frontend) + Render/IBM Code Engine (backend).

## Codebase Layout (always reference these paths)
- `engine/algo/llm.py` — all LLM calls (watsonx.ai default, Groq legacy)
- `engine/settings.py` — all deployment config & IBM credentials
- `engine/agents/ai_trading.py` — trading strategy agent (watsonx/Groq transport)
- `server/app.py` — FastAPI endpoints + WebSocket stream
- `grid/` — 4 protection agents (sentinel, flow, battery, health)
- `tests/test_ibm_integrations.py` — IBM-specific test suite (27 tests)
- `.env.example` — all environment variable documentation
- `ibm-cloud-integration-plan.md` — IBM integration plan and status

## IBM Cloud Integrations (active)
- **watsonx.ai** (Sub-Task 1): `LLM_PROVIDER=watsonx`, `WATSONX_API_KEY`, `WATSONX_PROJECT_ID`
- **App Configuration** (Sub-Task 3): `APPCONFIGURATION_*` vars, live feature flags
- **Code Engine** (Sub-Task 5): `IBM_CODE_ENGINE=true` suppresses keep-alive pinger

## Hard Rules
- Never commit `.env` — only `.env.example` (`.env` is in `.gitignore`)
- Never hardcode API keys, URLs, or credentials in source files
- Always read `DECISIONS.md` before changing engine architecture
- The engine must always run with zero API keys (graceful fallback everywhere)
