# UrjaSetu

P2P energy trading for an Indian LT distribution street, with agents that keep
the transformers alive. Sixty metered premises, four transformers, Whitefield.

```
docs/           the plan — read your own file, plus urjasetu-master-plan.md
data/           the dataset. LOCKED, and only engine/feed.py reads it
engine/         contracts, config, feed, the tick loop, the algorithms
grid/           the protection agents: sentinel, flow, battery custody, health
server/         FastAPI bridge — WebSocket + REST, engine to browser
UI/             React + Three.js front end (deploys to Vercel)
tests/          the suites. run_tests.sh runs all twelve
temp/           AGENT_VERIFICATION_GUIDE.md + runnable verification checks
DECISIONS.md    every choice the dataset forced, one line each
```

## Start here

```bash
pip install -r requirements.txt
./run_tests.sh                            # 12 suites, 0 skipped
python3 demo.py --days 30                 # the whole system, ~3 seconds
python3 temp/checks/verify_agents.py      # 50 independent checks
```

scipy is **not optional** — the reshape LP needs it, and without it the flow
agent never reshapes and the grid-protection half of the system silently does
nothing. See DECISIONS.md D15.

## Run it end to end

```bash
# terminal 1 — the engine
uvicorn server.app:app --port 8000

# terminal 2 — the interface
cd UI
printf 'VITE_ENGINE_URL=http://localhost:8000\n' > .env.local
npm install && npm run dev
```

`GET /api/health` reports what is loaded. The WebSocket is `/ws`, speaking
`{"type": "scene"|"summary"|"block"|"event", "data": ...}`.

## Deploying

The engine goes to **Render** (`render.yaml` is a working blueprint; the start
command must bind `0.0.0.0:$PORT`). The UI goes to **Vercel** from `UI/`, with
`VITE_ENGINE_URL` set to the Render service's public URL — it must be `https`,
because a page served over HTTPS cannot open a plaintext `ws://` socket.

Copy `.env.example` to `.env` for local secrets; on Render set the same keys in
the dashboard. `GROQ_API_KEY` and `URJASETU_LLM_ENABLED=true` turn on the LLM
agents; without them the engine runs identically on its deterministic defaults.

## What is already frozen

| Contract | File | Owner |
|---|---|---|
| 1 — algorithm signatures + stubs | `engine/algo/*.py` | D |
| 2 — payload shapes | UI PRD §4.3, `fixtures/day-one.json` | A + B |
| 3 — domain dataclasses | `engine/domain.py` | B |
| config defaults | `engine/config.py` | B |
| meter feed over the locked data | `engine/feed.py` | B |

A signature changes only by announcement, with everyone affected adapting in the
same sitting. Never silently.

## Four tracks, from here

**A — interface.** Topology is 64 nodes (60 premises + 4 EV hubs) on 4
transformers, and a day is 24 blocks, not 96. `feed.sites()` carries real
Whitefield coordinates and a `building_type` per premises — your layout and your
sprites — but read them off the `scene` payload B publishes, never out of the
JSON, or your fixture drifts from the engine. Build `fixtures/day-one.json` off
the feed rather than by hand, and regenerate it at every integration point.

**B — market.** `config.py`, `domain.py` and `feed.py` are done; yours to own
from here. Next is `bus.py`, then `sim/runner.py`. The feed already satisfies
the `MeterFeed` protocol, so the tick loop has real ticks on day one. Settlement
uses `feed.transmission_loss_pct(house_id)` per premises, not a flat figure —
FL4 has no losses term otherwise. Ignore the `p2p_*_price` columns in the year
CSV: your auction discovers its own price.

**C — grid.** `domain.py` gives you `Breach`, `Trade` and `MeterTick` now.
Build the sentinel against `engine/feed.py` directly — it breaches 162 blocks in
720, all in the 18:00–21:00 window, so you have real cases without injecting
any. Take `rating_kva` from `feed.transformers()`; reading `kva_rating` out of
`transformer_registry.json` gives you the installed 250–500 kVA and your
sentinel will never fire.

**D — algorithms.** Stubs are in `engine/algo/` and importable. Upgrade them in
place, in the order in your plan: auction, forecast, powerflow, reshape_lp,
thermal. Your functions never see the dataset — they take numbers as arguments.
For fixtures, the real ranges are ratings 63–125 kVA, per-DT load 8–140 kVA,
ambient 21–28 °C, 8–15 trades per DT per block, batteries 10 kWh at ±5 kW.

## The three checks that prove it works

Money conserved (ST1). Energy conserved (FL4). Baseline ages faster than P2P
over 30 days. Run the third at hour 24, not hour 33.
