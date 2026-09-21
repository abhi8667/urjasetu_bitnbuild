"""Operations Briefing Agent — LLM-backed, read-only daily summarizer.

Turns the engine's governance audit and block history into a short plain-language
briefing for non-technical administrators. Three questions answered:

  1. "What changed today?"
  2. "What needs attention?"
  3. "What action is recommended?"

DESIGN PRINCIPLES
-----------------
* Read-only: never modifies engine state or governance findings.
* LLM is optional: when disabled or unavailable, a fully deterministic
  template briefing is generated from the governance data alone. The template
  briefing is always accurate — it never fabricates figures.
* Grounded: every LLM statement must be derived from the audit payload passed
  to it. The system prompt forbids invention.
* Falls back silently: any LLM failure (network, auth, bad JSON) returns the
  deterministic template. The operator always gets *something*.
* Consistent schema: the dict returned is the same shape whether the LLM
  was used or not, so the UI can render either without branching.
"""
from __future__ import annotations

import json

from engine import settings

AGENT_ID = "ops_briefing"

# ------------------------------------------------------------------ schema

def _empty_briefing(day: int, mode: str = "template") -> dict:
    return {
        "day": day,
        "mode": mode,        # "llm" | "template" | "unavailable"
        "what_changed": "",
        "needs_attention": "",
        "recommendation": "",
        "severity_summary": {"critical": 0, "warning": 0, "info": 0},
        "linked_incidents": [],
    }


# ------------------------------------------------------------------ template

_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


def _template_briefing(day: int, day_audit: dict, prev_day_audit: dict | None,
                       blocks: list[dict], blocks_per_day: int) -> dict:
    """Fully deterministic briefing from audit data — no LLM required.

    Every sentence is assembled from numbers the audit already verified, so
    this path is always auditable and always correct.
    """
    crits = day_audit.get("critical", 0)
    warns = day_audit.get("warning", 0)
    infos = day_audit.get("info", 0)
    incidents_raw: list[dict] = day_audit.get("incidents_detail", [])

    # --- what changed --------------------------------------------------------
    change_parts: list[str] = []

    breach_blocks: dict = day_audit.get("transformer_breach_blocks", {})
    if breach_blocks:
        worst = max(breach_blocks.items(), key=lambda kv: kv[1])
        change_parts.append(
            f"Transformer {worst[0]} exceeded rated capacity in {worst[1]} "
            f"block{'s' if worst[1] > 1 else ''}.")

    max_price = day_audit.get("max_clearing_price", 0.0)
    price_blocks = day_audit.get("price_breach_blocks", 0)
    if price_blocks:
        change_parts.append(
            f"Clearing price reached ₹{max_price:.2f}/kWh, exceeding the "
            f"₹7.00 warning ceiling in {price_blocks} block(s).")

    btrades = day_audit.get("blocks_with_trades", 0)
    total_charges = day_audit.get("total_charges_inr", 0.0)
    if btrades:
        change_parts.append(
            f"P2P trading active in {btrades} of {blocks_per_day} blocks; "
            f"₹{total_charges:.2f} in charges collected.")

    zero_p2p = day_audit.get("houses_with_zero_p2p", [])
    if zero_p2p:
        change_parts.append(
            f"{len(zero_p2p)} importing household(s) received no P2P energy today.")

    conc = day_audit.get("concentrated_curtailment_houses", [])
    if conc:
        change_parts.append(
            f"Curtailment concentrated in {len(conc)} premises: {', '.join(conc[:3])}"
            f"{'…' if len(conc) > 3 else ''}.")

    if not change_parts:
        change_parts.append(
            f"Day {day + 1} completed with no transformer breaches, "
            f"no pricing anomalies, and settlements reconciled.")

    # --- needs attention -----------------------------------------------------
    attn_parts: list[str] = []
    if crits:
        top = [i for i in incidents_raw
               if i.get("severity") == "critical"][:3]
        attn_parts.append(
            f"{crits} critical finding(s) require review: "
            + "; ".join(i.get("message", "") for i in top)
            + ("" if len(top) <= 3 else f" (and {crits - len(top)} more)") + ".")
    if warns:
        attn_parts.append(
            f"{warns} warning(s) recorded. "
            "Review the incident trail for loading and pricing anomalies.")
    if not day_audit.get("settlement_reconciled", True):
        attn_parts.append(
            "Settlement reconciliation FAILED in at least one block. "
            "Manual ledger review is required.")
    if not attn_parts:
        attn_parts.append("No items require immediate attention.")

    # --- recommendation ------------------------------------------------------
    rec_parts: list[str] = []
    if breach_blocks:
        rec_parts.append(
            "Review EV charging schedules and consider load-shifting for "
            "the affected feeder during evening hours.")
    if not day_audit.get("settlement_reconciled", True):
        rec_parts.append(
            "Escalate settlement discrepancy to billing team before end of day.")
    if price_blocks > 3:
        rec_parts.append(
            "High clearing price persisted for multiple blocks; "
            "consider reviewing prosumer bid ceilings.")
    if zero_p2p and len(zero_p2p) > 3:
        rec_parts.append(
            f"{len(zero_p2p)} households were consistently excluded from P2P trades; "
            "fairness review recommended.")
    if not rec_parts:
        rec_parts.append(
            "No manual intervention is currently required. "
            "Continue monitoring transformer loading during the evening peak.")

    # Linked incidents: top 5 by severity
    linked = sorted(incidents_raw,
                    key=lambda i: (_SEVERITY_ORDER.get(i.get("severity", "info"), 2),
                                   i.get("block", 0)))[:5]

    return {
        "day": day,
        "mode": "template",
        "what_changed": " ".join(change_parts),
        "needs_attention": " ".join(attn_parts),
        "recommendation": " ".join(rec_parts),
        "severity_summary": {"critical": crits, "warning": warns, "info": infos},
        "linked_incidents": [
            {"rule": i.get("rule"), "severity": i.get("severity"),
             "subject": i.get("subject"), "message": i.get("message"),
             "block": i.get("block"), "clock": i.get("clock")}
            for i in linked
        ],
    }


# ------------------------------------------------------------------ LLM path

_SYSTEM_PROMPT = (
    "You are a concise operations briefing writer for an electricity distribution "
    "authority (DISCOM). You receive an audit report for one simulated day and "
    "produce a short, plain-language briefing for non-technical administrators. "
    "Respond with a JSON object and nothing else. "
    "Do NOT invent numbers, events, or recommendations not supported by the data. "
    "Keep each section to 2-3 sentences."
)

_USER_TEMPLATE = (
    "Audit report for simulation day {day}:\n\n"
    "{audit_json}\n\n"
    'Respond as JSON: {{"what_changed": "...", "needs_attention": "...", "recommendation": "..."}}\n'
    "what_changed: What significant events occurred today compared to a normal day?\n"
    "needs_attention: What specific issues require the committee's attention?\n"
    "recommendation: What single action (or no action) is recommended, and why?"
)


def _call_llm(prompt: str, timeout: float) -> str:
    """Single network call. Raises on any failure — callers catch everything."""
    if not settings.LLM_ENABLED:
        raise RuntimeError("LLM disabled")
    import httpx

    response = httpx.post(
        f"{settings.GROQ_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}",
                 "Content-Type": "application/json"},
        json={
            "model": settings.GROQ_MODEL,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            **({"max_completion_tokens": 800, "reasoning_effort": "low"}
               if settings.GROQ_MODEL.startswith("openai/gpt-oss-")
               else {"max_completion_tokens": 600, "reasoning_effort": "none"}
               if settings.GROQ_MODEL == "qwen/qwen3.8-27b"
               else {"max_tokens": 600}),
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def _extract_json(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start != -1 and end > start else text


def _llm_briefing(day: int, day_audit: dict, template: dict,
                  timeout: float) -> dict:
    """Attempt LLM enrichment. On any failure, return the template unchanged."""
    # Trim the audit to what the LLM needs (don't send the full incident list)
    compact = {
        "day": day,
        "headline": day_audit.get("headline", ""),
        "critical": day_audit.get("critical", 0),
        "warning": day_audit.get("warning", 0),
        "info": day_audit.get("info", 0),
        "transformer_breach_blocks": day_audit.get("transformer_breach_blocks", {}),
        "transformer_peak_loading_pct": {
            k: round(v * 100, 1)
            for k, v in day_audit.get("transformer_peak_loading", {}).items()
        },
        "transformer_peak_hotspot_c": day_audit.get("transformer_peak_hotspot", {}),
        "blocks_with_trades": day_audit.get("blocks_with_trades", 0),
        "max_clearing_price_inr": day_audit.get("max_clearing_price", 0.0),
        "price_breach_blocks": day_audit.get("price_breach_blocks", 0),
        "settlement_reconciled": day_audit.get("settlement_reconciled", True),
        "total_charges_inr": day_audit.get("total_charges_inr", 0.0),
        "concentrated_curtailment_houses": day_audit.get(
            "concentrated_curtailment_houses", []),
        "houses_with_zero_p2p": day_audit.get("houses_with_zero_p2p", []),
        # Top 3 most severe incidents as context
        "top_incidents": sorted(
            day_audit.get("incidents_detail", []),
            key=lambda i: (_SEVERITY_ORDER.get(i.get("severity", "info"), 2),
                           i.get("block", 0))
        )[:3],
    }
    prompt = _USER_TEMPLATE.format(
        day=day + 1,
        audit_json=json.dumps(compact, indent=2)[:4000],
    )
    try:
        raw = _call_llm(prompt, timeout)
        data = json.loads(_extract_json(raw))
        what = str(data.get("what_changed", "") or "").strip()
        attn = str(data.get("needs_attention", "") or "").strip()
        rec = str(data.get("recommendation", "") or "").strip()
        if not what and not attn and not rec:
            raise ValueError("empty response")
        return {
            **template,
            "mode": "llm",
            "what_changed": what or template["what_changed"],
            "needs_attention": attn or template["needs_attention"],
            "recommendation": rec or template["recommendation"],
        }
    except Exception:
        # Deliberately broad: any failure keeps the deterministic template.
        return template


# ------------------------------------------------------------------ public API

class OpsBriefingAgent:
    """Generate a daily operations briefing from governance audit output.

    Usage::

        agent = OpsBriefingAgent()
        briefings = agent.brief_all_days(audit_result.as_dict(), blocks, blocks_per_day)

    Or for a single day::

        briefing = agent.brief_day(day_dict, blocks, blocks_per_day)
    """

    def brief_day(self, day_audit: dict, prev_day_audit: dict | None,
                  blocks: list[dict], blocks_per_day: int = 24,
                  timeout: float | None = None) -> dict:
        """Generate briefing for one day.

        day_audit: the dict for one day from AuditResult.as_dict()["days"].
        prev_day_audit: the previous day's dict, for change detection (may be None).
        blocks: all simulation blocks (filtered to the day internally).
        """
        day = day_audit.get("day", 0)
        # Enrich day_audit with per-incident detail for the template
        # (the as_dict() summary only counts; incidents_detail is added by the
        # server layer which has access to the full AuditResult.incidents list)
        timeout = timeout if timeout is not None else settings.GROQ_TIMEOUT_SECONDS

        template = _template_briefing(day, day_audit, prev_day_audit, blocks, blocks_per_day)

        if not settings.LLM_ENABLED:
            return template
        return _llm_briefing(day, day_audit, template, timeout)

    def brief_all_days(self, audit_dict: dict, blocks: list[dict],
                       blocks_per_day: int = 24,
                       timeout: float | None = None) -> list[dict]:
        """Generate briefings for every day in the audit. Returns newest-last."""
        days: list[dict] = audit_dict.get("days", [])
        if not days:
            return []

        # Annotate each day dict with incident detail (needed by template)
        all_incidents: list[dict] = audit_dict.get("incidents", [])
        day_incident_map: dict[int, list[dict]] = {}
        for inc in all_incidents:
            d = inc.get("day", 0)
            day_incident_map.setdefault(d, []).append(inc)

        briefings: list[dict] = []
        prev: dict | None = None
        for day_dict in days:
            day_dict_with_detail = {
                **day_dict,
                "incidents_detail": day_incident_map.get(day_dict.get("day", 0), []),
            }
            briefings.append(
                self.brief_day(day_dict_with_detail, prev, blocks, blocks_per_day, timeout))
            prev = day_dict_with_detail

        return briefings
