# UrjaSetu — General Bob Rules
# These rules apply to ALL modes (Ask, Agent, Plan, Debug).

## Project Identity
- This is **UrjaSetu** ("Bridge of Energy") — a production-grade autonomous
  multi-agent P2P energy trading platform for LT urban distribution feeders.
- Backend: Python 3.11 + FastAPI + SciPy. Frontend: React 18 + Three.js.
- Real dataset: 60 residential premises, 4 distribution transformers (Whitefield, Bengaluru).
- Live deployment: Vercel (frontend) + Render/IBM Code Engine (backend).

## Codebase Layout (always reference these paths)
- `engine/algo/llm.py` — all LLM calls (Groq backend, cuttable)
- `engine/settings.py` — all deployment config & credentials
- `engine/agents/ai_trading.py` — trading strategy agent
- `server/app.py` — FastAPI endpoints + WebSocket stream
- `grid/` — 4 protection agents (sentinel, flow, battery, health)
- `tests/test_ibm_integrations.py` — IBM-specific test suite
- `.env.example` — all environment variable documentation

## Hard Rules
- Never commit `.env` — only `.env.example` (`.env` is in `.gitignore`)
- Never hardcode API keys, URLs, or credentials in source files
- Always read `DECISIONS.md` before changing engine architecture
- The engine must always run with zero API keys (graceful fallback everywhere)
