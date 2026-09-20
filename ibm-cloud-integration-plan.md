# IBM Cloud Free-Tier Integration Plan for UrjaSetu

## Top-Level Overview

UrjaSetu is a production-grade autonomous multi-agent P2P energy trading simulation platform
running a FastAPI backend (Python) + React/Three.js frontend. It already has one AI integration
point (Groq LLM in `engine/algo/llm.py`) and one ML point (LightGBM risk ranking in
`engine/algo/risk.py`). The goal is to identify and integrate IBM Cloud free-tier services that
add genuine value to this platform for a hackathon demonstration, using IBM's free Lite/Trial
plans available during the hackathon period.

The architecture is clean and designed for extensibility: LLM calls are isolated behind
a single module (`engine/algo/llm.py`), settings are read from environment variables
(`engine/settings.py`), and the simulation engine is decoupled from external services
(graceful fallback exists for all AI/LLM calls).

---

## IBM Services Available Free (Relevant to This Project)

| Service | Free Tier | What it offers UrjaSetu |
|---------|-----------|--------------------------|
| **watsonx.ai** (Lite) | 10 CUH/month, 1 instance | Replace Groq LLM with IBM Granite/Llama model for daily strategy, diagnosis, and operator Q&A |
| **Watson Assistant** (Lite) | 5 skills, 100 entities, 30-day trial | Power the `/api/ask` operator Q&A as a full conversational assistant instead of raw LLM Q&A |
| **IBM App Configuration** (Lite) | 10 flags, 5K API calls/month | Feature flags for `llm_enabled`, `risk_enabled`, `derate_factor` — live config without redeployment |
| **IBM Cloud Container Registry** (Free) | 500 MB, 5 GB pulls/month | Host the Docker image (replaces Docker Hub, avoids pull rate limits) |
| **IBM Cloud Code Engine** (Free tier) | Pay-as-you-go + free vCPU allowance | Host the FastAPI backend as a serverless container app (replaces Render free tier) |
| **IBM Cloud Secrets Manager** (Trial) | 1 instance, all features | Manage `GROQ_API_KEY` / watsonx API key centrally instead of raw env vars |

---

## Sub-Tasks

---

### Sub-Task 1: Replace Groq LLM with IBM watsonx.ai Foundation Models

**Status:** `[x] done`

**Intent:**
Swap the LLM backend in `engine/algo/llm.py` from Groq's OpenAI-compatible endpoint to IBM
watsonx.ai's text generation REST API. The three call sites (`daily_strategy`, `diagnose`,
`answer`) remain functionally identical — only the transport layer changes. watsonx.ai hosts
IBM Granite and Meta Llama models that are well-suited to structured JSON generation (strategy)
and operator prose (diagnosis/Q&A).

This is the highest-value IBM integration because it directly powers a running demo feature
(LLM-guided trading strategy + grid breach diagnosis), and watsonx.ai is IBM's flagship AI product.

**Expected Outcomes:**
- `engine/algo/llm.py` calls `https://<region>.ml.cloud.ibm.com/ml/v1/text/generation` instead of Groq
- `engine/settings.py` gains `WATSONX_API_KEY`, `WATSONX_PROJECT_ID`, `WATSONX_MODEL_ID`, `WATSONX_URL` vars
- `URJASETU_LLM_ENABLED=true` + watsonx credentials enables all three LLM features
- `/api/health` reports `provider: watsonx` in its response
- All existing tests (LLM fallback suite) continue to pass because the fallback contract is unchanged

**Todo List:**
1. Create a watsonx.ai Lite instance on IBM Cloud; note the `Project ID`
2. Generate an IBM Cloud IAM API key for watsonx access
3. Add `WATSONX_API_KEY`, `WATSONX_PROJECT_ID`, `WATSONX_MODEL_ID` (e.g., `ibm/granite-3-3-8b-instruct`), `WATSONX_URL` to `engine/settings.py`
4. Update `engine/settings.py`: add `LLM_PROVIDER` var (default `watsonx`; keep `groq` as fallback option); update `LLM_ENABLED` logic to check the active provider's key
5. Rewrite `_call_llm()` in `engine/algo/llm.py` to route to watsonx.ai `/ml/v1/text/generation` REST endpoint (using `httpx.post`), preserving the exact same raise-on-failure contract so the fallback path is unchanged
6. Update `llm_status()` in `engine/settings.py` to report `provider: watsonx` and model ID
7. Update `server/app.py`'s `/api/health` response to include watsonx model info
8. Test locally by setting env vars + calling `/api/ask`
9. Update `docker-compose.yml` and `render.yaml` (or Code Engine config) with new env var names

**Relevant Context:**
- `engine/algo/llm.py` — the only file that makes LLM calls; `_call_llm()` is the single network call site (monkeypatched in tests)
- `engine/settings.py` — all deployment config (API keys, URLs); env-first, `.env` fallback
- `server/app.py:173` — `/api/ask` endpoint calls `llm.answer()`
- `engine/agents/ai_trading.py` — calls `llm.daily_strategy()`; no changes needed here
- watsonx.ai REST endpoint: `POST https://<region>.ml.cloud.ibm.com/ml/v1/text/generation?version=2023-05-29`
- Auth: IBM IAM token (exchange API key at `https://iam.cloud.ibm.com/identity/token`)
- Request body: `{"model_id": "...", "project_id": "...", "input": "...", "parameters": {"max_new_tokens": 400}}`
- Response path: `response["results"][0]["generated_text"]` (differs from Groq's OpenAI-compatible path)

---

### Sub-Task 2: Add Watson Assistant for Operator Q&A (the `/api/ask` endpoint)

**Status:** `[ ] pending`

**Intent:**
Augment or replace the raw LLM Q&A on `/api/ask` with IBM Watson Assistant. Instead of
directly sending free-form user questions to a foundation model, Watson Assistant provides
a structured conversational layer with pre-defined intents (e.g., "explain breach",
"show trade history", "what is the clearing price?") and entity extraction.

This demonstrates IBM's conversational AI product, improves the demo experience (more
predictable responses for judges), and is an excellent complement to watsonx.ai (Assistant
handles structured Q&A; watsonx handles unstructured strategy generation).

The Lite plan allows 5 action skills, 100 entities, and 30-day trial — more than sufficient
for a hackathon with ~10 core question types about the grid.

**Expected Outcomes:**
- A Watson Assistant instance is provisioned with a "UrjaSetu Grid Operator" assistant
- Key intents defined: `explain_breach`, `show_clearing_price`, `battery_status`, `trade_summary`, `transformer_health`
- When Watson Assistant recognizes a known intent → structured response (injected with live block data from simulation)
- When Watson Assistant does not recognize → falls back to the existing watsonx.ai `llm.answer()` call
- `/api/ask` updated to route through Watson Assistant first, then fallback

**Todo List:**
1. Create a Watson Assistant Lite instance on IBM Cloud
2. Create an assistant and define ~10 core intents around grid operations (breach, clearing price, battery SOC, trades, transformer health)
3. Add a webhook/action integration pointing to a new endpoint `POST /api/wa-webhook` on the FastAPI server — this endpoint will look up real-time simulation data and return it
4. Add `WATSON_ASSISTANT_API_KEY`, `WATSON_ASSISTANT_ID`, `WATSON_ASSISTANT_URL` to `engine/settings.py`
5. Implement `server/app.py` changes: add `POST /api/wa-webhook` (the Watson Assistant action server endpoint) and update `POST /api/ask` to call Watson Assistant's `/v2/assistants/{id}/sessions` and `/v2/assistants/{id}/sessions/{sid}/message` REST APIs
6. Test by asking grid-specific questions through the existing `/api/ask` UI

**Relevant Context:**
- `server/app.py:173` — existing `/api/ask` POST endpoint
- `engine/algo/llm.py:178` — `answer()` function it calls (preserved as fallback)
- Watson Assistant REST: `POST /v2/assistants/{assistant_id}/sessions` then `POST .../message`
- Watson Assistant Lite: 5 skills, 100 entities, 1,000 annotations — enough for a hackathon

---

### Sub-Task 3: IBM App Configuration for Live Feature Flags

**Status:** `[x] done`

**Intent:**
Replace the static `config.yaml` / environment variable approach for runtime feature toggles
with IBM App Configuration. This gives a live dashboard UI to toggle features during the
hackathon demo without redeployment: flip `llm_enabled`, `risk_enabled`, change `derate_factor`,
toggle Watson Assistant vs raw LLM — all in real time.

This is a clean, low-risk addition since UrjaSetu already reads these values from
`engine/settings.py` at startup. The App Configuration SDK polls or receives WebSocket
pushes, so the engine can pick up new values on the next simulation tick.

The Lite plan provides 10 flags, 5,000 API calls/month — more than enough.

**Expected Outcomes:**
- IBM App Configuration Lite instance with flags: `llm_enabled`, `risk_enabled`, `watson_assistant_enabled`, `derate_factor`, `llm_provider`
- `engine/settings.py` gains an App Configuration client that overrides env-var values if the IBM App Config service is reachable
- During the demo, the hackathon judge can flip `llm_enabled` from the IBM Cloud dashboard and the engine picks it up within seconds
- If App Config is unreachable, falls back to env-var values (graceful degradation, matching existing patterns)

**Todo List:**
1. Create IBM App Configuration Lite instance; create 5 flags: `llm_enabled` (bool), `risk_enabled` (bool), `watson_assistant_enabled` (bool), `derate_factor` (numeric), `llm_provider` (string: "watsonx"/"groq")
2. Add `ibm-appconfiguration-python-sdk` to `requirements.txt`
3. Add `APPCONFIGURATION_API_KEY`, `APPCONFIGURATION_INSTANCE_ID`, `APPCONFIGURATION_ENVIRONMENT` to `engine/settings.py`
4. Add an `_init_app_config()` function in `engine/settings.py` that initializes the SDK client if credentials present (non-blocking; graceful skip if package not installed)
5. Wrap `LLM_ENABLED`, `RISK_ENABLED` reads with an `app_config_get(flag_name, default)` helper that returns SDK value if initialized, otherwise env-var value
6. Test by toggling `llm_enabled` in the IBM App Config dashboard and observing the engine behavior change

**Relevant Context:**
- `engine/settings.py` — all feature flag reads (`LLM_ENABLED`, `RISK_ENABLED`); only this file changes
- `config.yaml` — `llm_enabled: false` and `risk_enabled: true` are the current static defaults; App Config overrides these at runtime
- IBM App Configuration Python SDK: `from ibm_appconfiguration import AppConfiguration`

---

### Sub-Task 4: IBM Cloud Container Registry (Replace Docker Hub)

**Status:** `[ ] pending`

**Intent:**
Push the UrjaSetu Docker image to IBM Cloud Container Registry (ICR) instead of Docker Hub.
This prevents Docker Hub's free-tier pull rate limit (429 errors) during the hackathon demo,
which is a real risk when judges and CI/CD systems pull the image multiple times. The ICR
free tier provides 500 MB storage + 5 GB pulls/month — sufficient for a 3-layer Python image.

This is a low-effort, high-reliability improvement that directly makes the demo more robust.

**Expected Outcomes:**
- UrjaSetu engine Docker image pushed to `icr.io/<namespace>/urjasetu-engine:latest`
- `docker-compose.yml` updated to reference ICR image (or build locally as before, but push target updated)
- IBM Cloud Code Engine (Sub-Task 5) configured to pull from ICR (seamless auth via IBM IAM)

**Todo List:**
1. Install IBM Cloud CLI + Container Registry plugin: `ibmcloud plugin install container-registry`
2. Create a namespace: `ibmcloud cr namespace-add urjasetu`
3. Login and tag: `docker tag urjasetu-engine icr.io/urjasetu/urjasetu-engine:latest`
4. Push: `docker push icr.io/urjasetu/urjasetu-engine:latest`
5. Update `render.yaml` or Code Engine deployment to reference the ICR image
6. Add ICR pull secret to `docker-compose.yml` comments/README for local development

**Relevant Context:**
- `Dockerfile` — existing engine Dockerfile at repo root
- `docker-compose.yml` — references `build: .` (local build); change to `image: icr.io/...` for cloud pull
- ICR free plan: 500 MB storage, 5 GB pulls — image is ~400-450 MB (Python + scipy)

---

### Sub-Task 5: IBM Cloud Code Engine (Replace Render Free Tier)

**Status:** `[x] done (code changes — deployment steps below)`

**Intent:**
Deploy the FastAPI backend on IBM Cloud Code Engine instead of Render's free tier. Code Engine
is IBM's managed serverless container platform — no cluster management, scales to zero when
idle (preventing Render's 15-minute sleep that requires the keep-alive pinger in `server/app.py`),
and provides a stable HTTPS endpoint. The existing keep-alive pinger in `start_keep_alive()`
becomes unnecessary.

Code Engine free tier: per-request billing with a free vCPU/memory allowance — the simulation
runs once at startup and is cached (immutable), so ongoing compute cost is near-zero.

**Expected Outcomes:**
- FastAPI backend deployed on Code Engine as an Application (not a Job — it's a long-running HTTP server)
- WebSocket (`/ws`) works on Code Engine (Code Engine supports HTTP/1.1 upgrade for WebSocket)
- The keep-alive pinger in `server/app.py:74` can be conditionally disabled (Code Engine doesn't sleep)
- Frontend `VITE_ENGINE_URL` updated to the Code Engine app URL
- IBM App Config, Secrets Manager credentials injected via Code Engine environment variables

**Todo List:**
1. Install Code Engine plugin: `ibmcloud plugin install code-engine`
2. Create a project: `ibmcloud ce project create --name urjasetu`
3. Configure an image pull secret (ICR access, from Sub-Task 4)
4. Create a Code Engine Application: `ibmcloud ce app create --name urjasetu-engine --image icr.io/urjasetu/urjasetu-engine:latest --port 8000 --min-scale 0 --max-scale 1`
5. Set environment variables on the Code Engine app (watsonx credentials, App Config credentials, CORS_ORIGINS set to Vercel URL)
6. Note the Code Engine app URL; update Vercel frontend env `VITE_ENGINE_URL` to new URL
7. Optionally add a `CODE_ENGINE=true` env flag to `engine/settings.py` to suppress the keep-alive pinger (since Code Engine doesn't sleep)
8. Verify WebSocket streaming works end-to-end from the Vercel frontend

**Relevant Context:**
- `server/app.py:74` — `start_keep_alive()` startup task; can be skipped on Code Engine
- `engine/settings.py` — `PORT`, `CORS_ORIGINS`, `DAYS`, `DERATE` all remain as env vars (no code changes needed)
- `docker-compose.yml` — local dev unchanged; Code Engine is the cloud deployment path only
- `render.yaml` — superseded by Code Engine; keep for reference but mark as legacy
- Code Engine WebSocket: supported via HTTP/1.1 Upgrade; no special config needed

---

### Sub-Task 6: IBM Cloud Secrets Manager (Optional — Credential Hygiene)

**Status:** `[ ] pending`

**Intent:**
Store all API keys (`WATSONX_API_KEY`, `WATSON_ASSISTANT_API_KEY`, `APPCONFIGURATION_API_KEY`)
in IBM Cloud Secrets Manager instead of directly in environment variables. This demonstrates
IBM's secrets lifecycle management, which is relevant to a production energy platform where
API credentials must be rotated and audited.

The Trial plan provides one instance with all features — sufficient for a hackathon.
Code Engine can be configured to read secrets directly from Secrets Manager at startup.

**Expected Outcomes:**
- All IBM API keys stored in Secrets Manager as `arbitrary` secret type
- Code Engine app config references Secrets Manager secrets (via IBM Cloud Service Binding or manual IAM token fetch at startup)
- `engine/settings.py` has an optional `_from_secrets_manager(secret_name)` helper that fetches a secret if `SECRETS_MANAGER_URL` is set, falls back to env-var value otherwise

**Todo List:**
1. Create an IBM Cloud Secrets Manager Trial instance
2. Create secrets: `watsonx-api-key`, `watson-assistant-api-key`, `appconfiguration-api-key`
3. Add `SECRETS_MANAGER_URL`, `SECRETS_MANAGER_API_KEY` to deployment env vars
4. Add `_from_secrets_manager()` helper to `engine/settings.py` using `ibm-platform-services` Python SDK (or raw `httpx` call to Secrets Manager REST API)
5. Wrap the 3 IBM API key reads to try Secrets Manager first, then fall back to env var

**Relevant Context:**
- `engine/settings.py` — all credential reads; the fallback-chain pattern is already established
- Secrets Manager REST: `GET /api/v2/secrets/{id}` with IAM Bearer token
- Low priority — can be skipped if time-constrained; pure security/hygiene improvement, no feature visible to judges

---

## Integration Architecture (After All Sub-Tasks)

```
Vercel Frontend (React + Three.js)
    │
    │  HTTPS + WebSocket
    ▼
IBM Cloud Code Engine
    └── FastAPI (UrjaSetu Engine)
            │
            ├── watsonx.ai (Lite)          ← Sub-Task 1: LLM for strategy, diagnosis, Q&A
            ├── Watson Assistant (Lite)    ← Sub-Task 2: Structured operator Q&A
            ├── App Configuration (Lite)   ← Sub-Task 3: Live feature flags
            ├── IBM Container Registry     ← Sub-Task 4: Image hosting
            └── Secrets Manager (Trial)    ← Sub-Task 6: Credential management
```

---

## Priority Order for Hackathon

1. **Sub-Task 1 (watsonx.ai)** — Highest demo impact; directly visible to judges in the `/api/ask` Q&A and agent trace; straightforward code change in one file
2. **Sub-Task 3 (App Configuration)** — Live demo toggle; lets judges flip features in real time; impressive and low-code
3. **Sub-Task 4 (Container Registry)** — Infrastructure reliability; prevents demo failures from Docker Hub rate limits
4. **Sub-Task 5 (Code Engine)** — Deployment upgrade; makes the backend more reliable than Render free tier
5. **Sub-Task 2 (Watson Assistant)** — More effort; impressive if done but requires NLP design; do after 1 and 3
6. **Sub-Task 6 (Secrets Manager)** — Security hygiene; good to have; lowest demo visibility

---

## Notes on Free Tier Constraints

- **watsonx.ai Lite**: 10 CUH/month. Each LLM call uses ~0.1–0.5 CUH. A 30-day simulation with `llm_enabled: true` calls `daily_strategy()` once per simulated day = ~30 calls. The hackathon demo will not exhaust the free quota.
- **Watson Assistant Lite**: 30-day inactivity deletion applies after the hackathon period; not a concern during the event.
- **App Configuration Lite**: 5,000 API calls/month; the SDK polls ~every 5 min = 8,640 calls/month for a single instance. May need to increase poll interval to 10–15 min or use WebSocket delivery mode (server push).
- **Container Registry Free**: The engine image (Python + scipy + LightGBM) will be ~400–500 MB, right at the 500 MB limit. Keep the image lean; consider `--no-cache` and removing dev dependencies.
- **Secrets Manager Trial**: 1 instance per account, limited time. Use only if demonstrating security posture; otherwise env vars on Code Engine are sufficient for a hackathon.
