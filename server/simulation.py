"""Run the engine once, keep the result, serve it.

WHY PRECOMPUTE. A 30-day run is 720 blocks and takes under three seconds on a
free Render instance. Simulating live, per connection, would buy nothing and
cost a great deal: every viewer would need their own engine state, a Render cold
start would land in the middle of a run, and a restart would lose it. Running
once at boot and streaming the recorded blocks gives every viewer the same
deterministic run (D1 — identical config, identical bytes), makes the whole
thing horizontally scalable, and means a reconnect resumes instead of restarting.

WHAT IS RECORDED. `Runner.on_block` hands over a frozen snapshot of each block,
and a bus subscription captures every decision event as it is published. Both
are one-way: nothing in this module writes engine state, which is the rule
`engine/bus.py` states for the UI and which is what keeps a replay honest.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from engine.agents.settlement import SettlementAgent
from engine.agents.ai_trading import AITradingStrategyAgent
from engine.agents.grid_risk import GridFailureRiskAgent
from engine.agents.governance import GovernanceAgent
from engine.agents.ops_briefing import OpsBriefingAgent
from engine import settings
from engine.bus import Bus
from engine.config import Config, load_config
from engine.feed import WhitefieldFeed
from engine.sim.baseline import Baseline, compare, p2p_economics
from engine.sim.pool import AgentPool
from engine.sim.runner import Runner
from grid import BatteryBook, FlowAgent, GridSentinel, TransformerHealthAgent
from server import payloads

#: Bus topics the UI trace shows. `order_submitted` is deliberately absent: it
#: fires ~63 times a block and would drown the trace in noise that carries no
#: decision. Everything an agent DECIDED is here.
TRACE_TOPICS = (
    "block_opened", "market_cleared", "breach_predicted", "breach_detected",
    "reshape_proposed", "reshape_applied", "fallback_curtailed",
    "ageing_applied", "battery_moved", "delivery_shortfall",
    "bill_lines_posted", "strategy_updated", "grid_risk_predicted",
    "ai_strategy_thinking", "ai_strategy_updated", "ai_strategy_status",
    "governance_audit_completed", "briefing_ready",
)

#: Which agent each topic belongs to, for the UI's agent-theatre column.
_AGENT_OF = {
    "block_opened": "runner",
    "market_cleared": "market",
    "breach_predicted": "sentinel",
    "breach_detected": "sentinel",
    "reshape_proposed": "flow",
    "reshape_applied": "flow",
    "fallback_curtailed": "flow",
    "ageing_applied": "health",
    "battery_moved": "battery",
    "delivery_shortfall": "runner",
    "bill_lines_posted": "settlement",
    "strategy_updated": "ai_trading",
    "grid_risk_predicted": "grid_risk",
    "ai_strategy_thinking": "ai_trading",
    "ai_strategy_updated": "ai_trading",
    "ai_strategy_status": "ai_trading",
    "governance_audit_completed": "governance",
    "briefing_ready": "ops_briefing",
}


@dataclass
class SimulationResult:
    scene: dict
    blocks: list[dict]
    events: list[dict]
    summary: dict
    run_summary: dict
    compare: dict
    config: Config
    built_in_seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)
    # Governance & Compliance audit (populated post-run)
    governance: dict = field(default_factory=dict)
    # Operations briefings, one per simulated day (populated post-run)
    briefings: list[dict] = field(default_factory=list)


class _Recorder:
    """Turns each block snapshot into a UI payload, capturing battery state.

    State of charge is read off the BatteryBook AFTER the block completes, which
    is the only place it is authoritative. The prosumer agents keep their own
    mirror of it, and those two used to disagree by the round-trip conversion
    loss — reading the agents' belief would have put a number on screen that the
    batteries did not contain.
    """

    def __init__(self, feed, config, batteries):
        self.feed = feed
        self.config = config
        self.batteries = batteries
        self.houses_by_id = {h.house_id: h for h in feed.houses()}
        self.transformer_ids = sorted(t.transformer_id for t in feed.transformers())
        self.blocks: list[dict] = []

    def __call__(self, view) -> None:
        soc = {hid: self.batteries.total_stored_kwh(hid)
               for hid, house in self.houses_by_id.items() if house.has_battery}
        self.blocks.append(payloads.block_payload(
            view, self.feed, self.config, soc,
            self.houses_by_id, self.transformer_ids))


def build_simulation(config: Config | None = None, days: int | None = None,
                     derate: float | None = None) -> SimulationResult:
    """Run the whole system once and record it. Blocking, ~3 s for 30 days."""
    from dataclasses import replace

    started = time.perf_counter()
    if config is None:
        config = replace(load_config(), llm_enabled=settings.LLM_ENABLED,
                         groq_primary_model=settings.GROQ_MODEL,
                         groq_fallback_model=settings.GROQ_FALLBACK_MODEL,
                         groq_timeout_seconds=settings.GROQ_TIMEOUT_SECONDS)
    if derate is not None and derate != config.derate_factor:
        config = replace(config, derate_factor=derate)
    blocks = (days or config.days) * config.blocks_per_day

    feed = WhitefieldFeed(config)
    houses, transformers = feed.houses(), feed.transformers()

    # SUBSCRIBE, do not read the ring. The ring is a fixed-size deque and
    # `order_submitted` alone fires about 63 times a block, so any ring small
    # enough to be sane evicts the decision events before the run finishes —
    # sizing it to fit meant holding ~45,000 events to keep ~2,000. Subscribing
    # to the topics the trace actually shows keeps all of them and none of the
    # noise. `Bus.subscribe` existed from the start and had no callers.
    bus = Bus()
    trace: list[dict] = []

    def _capture(event: dict) -> None:
        trace.append({
            "block": event["block"],
            "agent": _AGENT_OF.get(event["topic"], event["agent_id"]),
            "kind": event["topic"],
            "text": payloads.event_text(event["topic"], event["payload"]),
        })

    for topic in TRACE_TOPICS:
        bus.subscribe(topic, _capture)
    risk_agent = None
    if config.risk_enabled:
        # This model learns from the project's simulated feed, not field measurements.
        risk_agent = GridFailureRiskAgent(transformers, houses, config).fit(feed)
    pool = AgentPool(houses, config, strategy_agent=AITradingStrategyAgent(config))
    settlement = SettlementAgent(houses, config, bus=bus,
                                 consumers=pool.consumers, feed=feed)
    batteries = BatteryBook(houses, config)
    health = TransformerHealthAgent(transformers, config, houses)
    recorder = _Recorder(feed, config, batteries)

    runner = Runner(
        feed, pool, config, bus=bus,
        sentinel=GridSentinel(transformers, houses, config),
        flow=FlowAgent(transformers, houses, config),
        health=health,
        settlement=settlement,
        batteries=batteries,
        risk_agent=risk_agent,
        # The counterfactual is computed once below and shared with the compare
        # screen, rather than run twice.
        include_baseline=False,
        on_block=recorder,
    )
    run_summary = runner.run(blocks=blocks)

    baseline = Baseline(feed, config).run(blocks=blocks)
    life = sum(health._cumulative_life_hours.values())
    comparison = compare(
        p2p_economics(feed, config, settlement.ledger, blocks=blocks,
                      loss_of_life_hours=life),
        baseline)
    run_summary["baseline"] = {
        "label": baseline.label,
        "household_bills_inr": baseline.household_bills_inr,
        "discom_energy_revenue_inr": baseline.discom_energy_revenue_inr,
        "discom_charge_revenue_inr": baseline.discom_charge_revenue_inr,
        "export_credits_inr": baseline.export_credits_inr,
        "loss_of_life_hours_total": baseline.loss_of_life_hours,
    }
    run_summary["premises"] = len(houses)

    events = trace

    warnings: list[str] = []
    if not comparison["baseline_ages_at_least_as_fast"]:
        # Surfaced, not swallowed. This is the check the whole argument rests on.
        warnings.append(
            "baseline_ages_at_least_as_fast is FALSE — the P2P path aged the "
            "transformers faster than net metering did. The ageing price signal "
            "is not doing its job.")
    if run_summary.get("ageing_adder_trimmed_inr", 0) > 0:
        warnings.append(
            f"CN2 cap trimmed Rs{run_summary['ageing_adder_trimmed_inr']:.2f} of "
            f"ageing adder to keep buyers' all-in cost at or below retail.")

    # ---- governance & compliance audit (deterministic, post-run) -----------
    gov_agent = GovernanceAgent()
    audit = gov_agent.audit(recorder.blocks, blocks_per_day=config.blocks_per_day)
    governance_dict = audit.as_dict()

    # Publish one summary event so the governance agent appears in the agent stream.
    # Block 0 is used as the anchor — this is a post-run event, not a live one.
    total_b = len(recorder.blocks)
    last_block = recorder.blocks[-1]["block"] if recorder.blocks else 0
    bus.publish("governance_audit_completed", last_block, "governance", {
        "total_incidents": governance_dict["summary"]["total_incidents"],
        "critical": governance_dict["summary"]["critical"],
        "warning": governance_dict["summary"]["warning"],
        "info": governance_dict["summary"]["info"],
        "days_audited": len(governance_dict["days"]),
        "blocks_audited": total_b,
    })

    # ---- operations briefings (LLM optional, falls back to template) -------
    ops_agent = OpsBriefingAgent()
    briefings = ops_agent.brief_all_days(
        governance_dict, recorder.blocks, blocks_per_day=config.blocks_per_day)

    # Publish one summary event so ops_briefing appears in the agent stream.
    if briefings:
        last_b = briefings[-1]
        bus.publish("briefing_ready", last_block, "ops_briefing", {
            "days": len(briefings),
            "mode": last_b.get("mode", "template"),
            "last_day": last_b.get("day", 0),
            "last_headline": (last_b.get("what_changed") or "")[:120],
        })

    scene = payloads.scene_payload(feed, config)
    first_dispatch = next(
        (block["block"] for block in recorder.blocks
         if (block.get("battery") or {}).get("dispatches")),
        None,
    )
    scene["scenario_blocks"] = {"battery_dispatch": first_dispatch}

    return SimulationResult(
        scene=scene,
        blocks=recorder.blocks,
        events=events,
        summary=payloads.summary_payload(run_summary, comparison, config),
        run_summary=run_summary,
        compare=comparison,
        config=config,
        built_in_seconds=round(time.perf_counter() - started, 3),
        warnings=warnings,
        governance=governance_dict,
        briefings=briefings,
    )


class SimulationCache:
    """One built simulation per (days, derate), built on demand and kept.

    Thread-safe because uvicorn serves concurrently and two requests arriving
    together on a cold cache would otherwise both pay the three seconds. The
    lock is held across the build deliberately: the second caller waits for the
    first rather than starting a duplicate run.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[tuple[int, float], SimulationResult] = {}

    def get(self, days: int, derate: float) -> SimulationResult:
        key = (int(days), round(float(derate), 4))
        with self._lock:
            if key not in self._cache:
                self._cache[key] = build_simulation(days=key[0], derate=key[1])
            return self._cache[key]

    def keys(self) -> list[tuple[int, float]]:
        with self._lock:
            return sorted(self._cache)
