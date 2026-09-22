"""FastAPI bridge: WebSocket stream + REST, for the Vercel-hosted UI.

    uvicorn server.app:app --reload            local
    uvicorn server.app:app --host 0.0.0.0 --port $PORT     Render

THE WEBSOCKET, `/ws`. Speaks exactly the envelope `UI/src/transport.ts`
declares — `{"type": "scene"|"block"|"event", "data": ...}` — because that
interface already existed and was correct; what was missing was anything on the
other end of it. `LiveTransport` was written, never instantiated, and the UI ran
on a TypeScript fixture of invented numbers instead.

Each connection gets the scene, then blocks on a cadence, then loops. The
simulation itself is shared and immutable, so a hundred viewers cost one engine
run; only the cursor is per-connection, which is what lets someone reconnect
after a Render cold start and pick up rather than restart.

REST is there for everything that is not a stream: the run summary, the ledger,
the compare figures, and `/api/health` so Render has something to poll.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os

import httpx

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from engine import settings
from engine.algo import llm
from server.simulation import SimulationCache

app = FastAPI(
    title="UrjaSetu engine",
    version="1.0.0",
    description="Decentralised energy micro-grid agent network over a real "
                "Whitefield LT distribution street.",
)

# `*` is the shipped default so a demo works without configuration. Put the
# Vercel origin in CORS_ORIGINS for anything real.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

CACHE = SimulationCache()


def _sim(days: int | None = None, derate: float | None = None):
    return CACHE.get(days if days is not None else settings.DAYS,
                     derate if derate is not None else settings.DERATE)


@app.on_event("startup")
async def warm_cache() -> None:
    """Build the default run at boot, off the event loop.

    Three seconds of CPU inside an async startup hook would block every other
    coroutine, so it goes to a worker thread. Doing it at boot rather than on
    first request means the first visitor after a Render cold start waits for
    the container, not for the container AND the simulation.
    """
    await asyncio.to_thread(_sim)


@app.on_event("startup")
async def start_keep_alive() -> None:
    """Render free tier pinger: pings /api/health every 5 minutes to prevent sleep."""
    target = os.environ.get("KEEP_ALIVE_URL") or os.environ.get("RENDER_EXTERNAL_URL")
    if not target:
        return

    url = target.rstrip("/") + "/api/health"

    async def _ping_loop() -> None:
        await asyncio.sleep(60)  # Initial wait for application startup
        async with httpx.AsyncClient(timeout=10.0) as client:
            while True:
                try:
                    await client.get(url)
                except Exception:
                    pass
                await asyncio.sleep(300)  # Ping every 5 minutes

    asyncio.create_task(_ping_loop())


# --------------------------------------------------------------- REST

@app.get("/api/health")
async def health() -> dict:
    """Render polls this. It reports what is actually loaded, not just 200."""
    return {
        "status": "ok",
        "llm": llm.LLM_ENABLED,
        "llm_detail": settings.llm_status(),
        "cached_runs": [{"days": d, "derate": r} for d, r in CACHE.keys()],
        "default_days": settings.DAYS,
        "default_derate": settings.DERATE,
    }


@app.get("/api/scene")
async def scene(days: int | None = None, derate: float | None = None) -> dict:
    return (await asyncio.to_thread(_sim, days, derate)).scene


@app.get("/api/summary")
async def summary(days: int | None = None, derate: float | None = None) -> dict:
    sim = await asyncio.to_thread(_sim, days, derate)
    return sim.summary


@app.get("/api/run-summary")
async def run_summary(days: int | None = None, derate: float | None = None) -> dict:
    """The full PRD §10 artefact, unabridged — every invariant figure the engine
    produced, including the ones the UI does not draw."""
    sim = await asyncio.to_thread(_sim, days, derate)
    return {"run_summary": sim.run_summary, "compare": sim.compare,
            "warnings": sim.warnings, "built_in_seconds": sim.built_in_seconds}


@app.get("/api/blocks")
async def blocks(days: int | None = None, derate: float | None = None,
                 start: int = Query(0, ge=0),
                 limit: int = Query(48, ge=1, le=2000)) -> dict:
    """Paged, because a 30-day run is 720 blocks of dense per-house state and
    handing all of it over in one response is several megabytes."""
    sim = await asyncio.to_thread(_sim, days, derate)
    window = sim.blocks[start:start + limit]
    return {"total": len(sim.blocks), "start": start,
            "count": len(window), "blocks": window}


@app.get("/api/block/{block}")
async def one_block(block: int, days: int | None = None,
                    derate: float | None = None) -> dict:
    sim = await asyncio.to_thread(_sim, days, derate)
    match = next((b for b in sim.blocks if b["block"] == block), None)
    if match is None:
        raise HTTPException(404, f"block {block} is not in this run")
    return match


@app.get("/api/events")
async def events(days: int | None = None, derate: float | None = None,
                 block: int | None = None, limit: int = Query(500, ge=1, le=20000)) -> dict:
    sim = await asyncio.to_thread(_sim, days, derate)
    found = sim.events if block is None else [
        e for e in sim.events if e["block"] == block]
    return {"total": len(found), "events": found[:limit]}


@app.get("/api/replay")
async def replay(days: int = Query(1, ge=1, le=30),
                 derate: float | None = None) -> dict:
    """A complete short run in one response, for the UI's replay mode. Capped at
    a day by default because that is what replay is for; the cap exists so this
    endpoint cannot be used to pull 30 days as a single multi-megabyte body."""
    sim = await asyncio.to_thread(_sim, days, derate)
    return {"scene": sim.scene, "blocks": sim.blocks,
            "events": sim.events, "summary": sim.summary}


@app.get("/api/governance")
async def governance(days: int | None = None, derate: float | None = None) -> dict:
    """Full Governance & Compliance audit trail for the run.

    Returns the complete rule-based findings: every incident with its rule,
    severity, block, and numeric detail; per-day summaries; and cross-day
    fairness aggregates (P2P energy received, curtailment concentration,
    above-average charge days).

    The result is deterministic — identical input produces identical output —
    so committee members can re-run and verify any finding independently.
    """
    sim = await asyncio.to_thread(_sim, days, derate)
    return sim.governance


@app.get("/api/governance/day/{day}")
async def governance_day(day: int, days: int | None = None,
                         derate: float | None = None) -> dict:
    """Governance findings for one simulated day (0-indexed).

    Returns the day summary and all incidents for that day. Useful for the UI
    to load one day at a time without pulling the full 30-day audit payload.
    """
    sim = await asyncio.to_thread(_sim, days, derate)
    day_list: list[dict] = sim.governance.get("days", [])
    match = next((d for d in day_list if d.get("day") == day), None)
    if match is None:
        raise HTTPException(404, f"day {day} not found in governance audit")
    # Attach incident detail for that day
    incidents = [i for i in sim.governance.get("incidents", [])
                 if i.get("day") == day]
    return {**match, "incidents_detail": incidents}


@app.get("/api/briefings")
async def briefings(days: int | None = None, derate: float | None = None) -> dict:
    """Operations briefings — one plain-language summary per simulated day.

    Each entry answers: what changed, what needs attention, what is recommended.
    Produced by a deterministic template that is optionally enriched by the LLM
    (Groq) when the server has a valid API key. The `mode` field in each entry
    is "llm" or "template" so the UI can indicate which path was used.
    """
    sim = await asyncio.to_thread(_sim, days, derate)
    return {"briefings": sim.briefings, "count": len(sim.briefings),
            "llm_enabled": sim.config.llm_enabled}


@app.get("/api/briefings/day/{day}")
async def briefing_day(day: int, days: int | None = None,
                       derate: float | None = None) -> dict:
    """Operations briefing for one simulated day (0-indexed)."""
    sim = await asyncio.to_thread(_sim, days, derate)
    match = next((b for b in sim.briefings if b.get("day") == day), None)
    if match is None:
        raise HTTPException(404, f"briefing for day {day} not found")
    return match


@app.post("/api/ask")
async def ask(payload: dict) -> dict:
    """Operator Q&A over the recent trace, through the Groq-backed LLM agent.

    Returns "unavailable" rather than a guess when the LLM is off or fails —
    a confident wrong answer about a distribution grid is worse than no answer,
    so `answer` never fabricates and this endpoint never papers over it.
    """
    question = str(payload.get("question", "")).strip()
    if not question:
        raise HTTPException(400, "question is required")
    sim = await asyncio.to_thread(_sim)
    recent = sim.events[-120:]
    reply = await asyncio.to_thread(llm.answer, question, recent)
    return {"answer": reply, "llm_enabled": llm.LLM_ENABLED,
            "llm_detail": settings.llm_status()}


@app.get("/")
async def root() -> JSONResponse:
    return JSONResponse({
        "service": "UrjaSetu engine",
        "websocket": "/ws",
        "rest": ["/api/health", "/api/scene", "/api/summary", "/api/run-summary",
                 "/api/blocks", "/api/block/{block}", "/api/events",
                 "/api/replay", "/api/ask",
                 "/api/governance", "/api/governance/day/{day}",
                 "/api/briefings", "/api/briefings/day/{day}"],
        "docs": "/docs",
    })


# ----------------------------------------------------------- WebSocket

@app.websocket("/ws")
async def stream(websocket: WebSocket) -> None:
    await websocket.accept()
    params = websocket.query_params
    try:
        days = int(params.get("days", settings.DAYS))
        derate = float(params.get("derate", settings.DERATE))
        cadence = float(params.get("cadence", settings.STREAM_CADENCE_S))
        cursor = int(params.get("from", 0))
    except ValueError:
        await websocket.close(code=1008, reason="bad query parameter")
        return
    # Clamped so a client cannot ask the server to spin at 1 ms per block.
    cadence = max(0.1, min(60.0, cadence))

    try:
        sim = await asyncio.to_thread(_sim, days, derate)
    except Exception as exc:                        # pragma: no cover
        await websocket.close(code=1011, reason=str(exc)[:120])
        return

    scene = {**sim.scene, "total_blocks": len(sim.blocks)}
    await websocket.send_text(json.dumps({"type": "scene", "data": scene}))
    await websocket.send_text(json.dumps({"type": "summary", "data": sim.summary}))

    events_by_block: dict[int, list[dict]] = {}
    for event in sim.events:
        events_by_block.setdefault(event["block"], []).append(event)

    # Commands arrive on the same socket; a reader task keeps them from blocking
    # the stream. `derate` and `cloud` re-target the cursor at a run built with
    # different parameters rather than mutating anything — the recorded run is
    # immutable and shared, so a command from one viewer cannot alter another's.
    pending: asyncio.Queue[dict] = asyncio.Queue()

    async def reader() -> None:
        try:
            while True:
                raw = await websocket.receive_text()
                with contextlib.suppress(json.JSONDecodeError):
                    await pending.put(json.loads(raw))
        except (WebSocketDisconnect, RuntimeError):
            return

    reader_task = asyncio.create_task(reader())
    try:
        index = max(0, min(cursor, len(sim.blocks) - 1))
        while True:
            block = sim.blocks[index]
            await websocket.send_text(json.dumps({"type": "block", "data": block}))
            for event in events_by_block.get(block["block"], []):
                await websocket.send_text(json.dumps({"type": "event", "data": event}))

            await asyncio.sleep(cadence)

            while not pending.empty():
                message = pending.get_nowait()
                if message.get("type") != "command":
                    continue
                name = message.get("name")
                if name == "derate":
                    # A genuinely different run of the engine, not a number
                    # nudged in a fixture. Costs one simulation the first time.
                    sim = await asyncio.to_thread(_sim, days, 0.8)
                    events_by_block = {}
                    for event in sim.events:
                        events_by_block.setdefault(event["block"], []).append(event)
                    scene = {**sim.scene, "total_blocks": len(sim.blocks)}
                    await websocket.send_text(json.dumps({"type": "scene", "data": scene}))
                    await websocket.send_text(json.dumps({
                        "type": "event",
                        "data": {"block": block["block"], "agent": "operator",
                                 "kind": "command_received",
                                 "text": "Transformers derated to 80% — re-running "
                                         "the engine at the harder rating"}}))
                elif name == "reset":
                    sim = await asyncio.to_thread(_sim, days, settings.DERATE)
                    events_by_block = {}
                    for event in sim.events:
                        events_by_block.setdefault(event["block"], []).append(event)
                    scene = {**sim.scene, "total_blocks": len(sim.blocks)}
                    await websocket.send_text(json.dumps({"type": "scene", "data": scene}))
                elif name == "seek":
                    with contextlib.suppress(TypeError, ValueError):
                        target = int((message.get("args") or {}).get("block", 0))
                        found = next((i for i, b in enumerate(sim.blocks)
                                      if b["block"] == target), None)
                        if found is not None:
                            index = found - 1        # the += 1 below lands on it

            index = (index + 1) % len(sim.blocks)
    except (WebSocketDisconnect, RuntimeError):
        return
    finally:
        reader_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reader_task
