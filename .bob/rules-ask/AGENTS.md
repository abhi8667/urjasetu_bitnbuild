# Ask Mode — UrjaSetu Documentation Context (Non-Obvious Only)

## Counterintuitive Structure
- `engine/config.py` ≠ `engine/settings.py`. Config = simulation physics (tariffs, ratings,
  limits — version-controlled). Settings = deployment secrets (API keys, ports — env vars only).
- `engine/domain.py` is the system's **published contract** — frozen dataclasses that all agents
  build against. Treat it as append-only.
- `engine/physics.py` is intentionally tiny (one kW→kVA function). It exists only because
  four modules previously each had their own literal `0.95`, disagreeing silently.
- `engine/trades.py` is an identity function (`delivered_kwh` just returns `quantity_kwh`).
  It exists to be the single declaration of the trade quantity convention.

## Data Reality (locked dataset, Whitefield, Bengaluru)
- Transformer ratings in `transformer_registry.json` (500/250 kVA) are overridden in
  `config.rating_kva` to 125/63/63 kVA. The JSON is not wrong — the override is intentional
  (see DECISIONS.md D1). Do NOT edit the CSV/JSON files.
- Block = 1 hour (not 15 min). The literal `0.25` anywhere in engine code is a bug.
- 30-day run = one measured Saturday scaled seasonally. Not 30 independently measured days.
- 64 nodes = 60 metered premises + 4 EV hubs (the hubs are modelled as `House` objects).
- P2P covers ~9.5% of daily demand. Bill savings are modest; the headline metric is
  **transformer life saved** (hrs), not bill savings (INR).

## Simulation Flow
- The engine runs once at boot (≈3 seconds for 720 blocks) and streams over WebSocket.
  There is no per-connection simulation.
- Evening peak = blocks 17–21 (18:00–21:00). All loading breaches on this street occur here.
  P2P trading occurs 09:00–15:00. The two are temporally disjoint — by design, on this data.
- DT-2 never breaches. It is the healthy control for the compare screen.

## IBM Integrations
- LLM provider is Groq (not IBM watsonx in the current build). `LLM_PROVIDER=watsonx` is
  a planned path but `engine/algo/llm.py` currently speaks Groq's OpenAI-compatible API.
- `URJASETU_LLM_ENABLED=true` alone is not enough — `GROQ_API_KEY` must also be set.
  The settings module ANDs both conditions.
- IBM Code Engine: set `IBM_CODE_ENGINE=true` to suppress keep-alive pings.
