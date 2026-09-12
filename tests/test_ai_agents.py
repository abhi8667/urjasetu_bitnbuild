"""Focused checks for the ML risk and Groq trading agents.

Run standalone: ``python tests/test_ai_agents.py``.  The normal suite never
needs a network or API key; set GROQ_API_KEY and RUN_GROQ_LIVE_TEST=1 for the
optional live smoke check.
"""
from __future__ import annotations

import os
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.agents.ai_trading import AITradingStrategyAgent
from engine.agents.grid_risk import GridFailureRiskAgent
from engine.agents.settlement import SettlementAgent
from engine.bus import Bus
from engine.config import DEFAULT
from engine.domain import GridRiskPrediction, StrategyParams
from engine.feed import WhitefieldFeed
from engine.sim.pool import AgentPool
from engine.sim.runner import Runner

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))
    return ok


def test_risk_agent_learns_from_existing_feed():
    config = replace(DEFAULT, risk_training_days=14)
    feed = WhitefieldFeed(config)
    agent = GridFailureRiskAgent(feed.transformers(), feed.houses(), config).fit(feed)
    positive, negative = [], []
    horizon = config.risk_horizon_blocks
    for block in range(14 * 24, min(feed.total_blocks() - horizon, 21 * 24)):
        predictions = {p.transformer_id: p for p in agent.predict(block, feed.ticks(block))}
        future_peak = {tid: 0.0 for tid in predictions}
        for future in range(block + 1, block + horizon + 1):
            loads = agent._loading(feed.ticks(future))
            for tid, value in loads.items():
                future_peak[tid] = max(future_peak[tid], value)
        for tid, prediction in predictions.items():
            target = future_peak[tid] >= config.loading_limit
            (positive if target else negative).append(prediction.risk_score)
    separated = positive and negative and sum(positive) / len(positive) > sum(negative) / len(negative)
    check("risk ML learns higher scores before real overloads", bool(separated),
          f"positive={sum(positive)/len(positive):.3f}, negative={sum(negative)/len(negative):.3f}")
    check("risk ML used project data", agent.training_examples > 1000,
          f"{agent.training_examples} labelled transformer-blocks")


def test_groq_primary_and_fallback_models():
    config = replace(DEFAULT, llm_enabled=True)
    calls = []

    def fallback_transport(model, prompt, timeout):
        calls.append(model)
        if model == config.groq_primary_model:
            raise TimeoutError("simulated primary timeout")
        return '{"discount": 0.79, "margin": 0.12}'

    agent = AITradingStrategyAgent(config, transport=fallback_transport)
    old = StrategyParams()
    risk = [GridRiskPrediction("DT-1", 0, 3, 0.9, 1.1, True)]
    new = agent.decide({"ambient_c": 31}, [4.2, 4.5], risk, old)
    check("Groq falls back from Qwen 3.8 to Qwen 3.6", calls == [
        "qwen/qwen3.8-27b", "qwen/qwen3.6-27b"], str(calls))
    check("fallback response changes only safe strategy fields",
          new.discount == 0.79 and new.margin == 0.12 and
          new.battery_reserve_frac == old.battery_reserve_frac and
          new.bid_aggression == old.bid_aggression)


def test_groq_double_failure_is_safe():
    config = replace(DEFAULT, llm_enabled=True)

    def broken(*_):
        raise RuntimeError("offline")

    old = StrategyParams(discount=0.81, margin=0.09)
    agent = AITradingStrategyAgent(config, transport=broken)
    new = agent.decide({}, [], [], old)
    check("both Groq failures preserve the previous strategy", new == old)


def test_agents_are_connected_to_runner_and_each_other():
    config = replace(DEFAULT, llm_enabled=True, risk_training_days=7)
    feed = WhitefieldFeed(config)
    risk = GridFailureRiskAgent(feed.transformers(), feed.houses(), config).fit(feed)
    seen = []

    def mock_groq(model, prompt, timeout):
        seen.append((model, prompt["grid_risk"]))
        return '{"discount": 0.82, "margin": 0.10}'

    strategy = AITradingStrategyAgent(config, transport=mock_groq)
    pool = AgentPool(feed.houses(), config, strategy_agent=strategy)
    settle = SettlementAgent(feed.houses(), config, consumers=pool.consumers, feed=feed)
    bus = Bus(ring_size=5000)
    summary = Runner(feed, pool, config, bus=bus, settlement=settle,
                     risk_agent=risk, include_baseline=False).run(blocks=48)
    risk_events = bus.recent_events("grid_risk_predicted")
    strategy_events = bus.recent_events("ai_strategy_updated")
    check("runner publishes one risk result per transformer and block",
          len(risk_events) == 48 * len(feed.transformers()), str(len(risk_events)))
    check("trading AI receives ML risk and runs once per day",
          len(seen) == 2 and all(context for _, context in seen), str(len(seen)))
    check("strategy reaches every market agent",
          strategy_events and all(a.strategy == pool.strategy
                                  for a in list(pool.prosumers.values()) +
                                  list(pool.consumers.values())))
    check("connected simulation still trades and settles",
          summary["trades"] > 0 and len(settle.ledger) > 0,
          f"{summary['trades']} trades")


def test_optional_live_groq_smoke():
    if not (os.environ.get("GROQ_API_KEY") and
            os.environ.get("RUN_GROQ_LIVE_TEST") == "1"):
        check("optional live Groq smoke", True, "skipped; opt in with RUN_GROQ_LIVE_TEST=1")
        return
    config = replace(DEFAULT, llm_enabled=True)
    agent = AITradingStrategyAgent(config)
    result = agent.decide({"ambient_c": 31.0}, [4.2, 4.5], [], StrategyParams())
    check("optional live Groq smoke", agent.last_model is not None,
          f"model={agent.last_model}, result={result}")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        try:
            fn()
        except Exception as exc:
            RESULTS.append((fn.__name__, False))
            print(f"  ERROR  {fn.__name__}: {type(exc).__name__}: {exc}")
    failed = sum(1 for _, ok in RESULTS if not ok)
    print(f"\n{len(RESULTS)-failed}/{len(RESULTS)} checks passed")
    raise SystemExit(1 if failed else 0)
