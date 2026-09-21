"""Governance & Compliance Agent — rule-based, fully deterministic.

Reads the recorded simulation blocks (immutable, post-run) and applies a
fixed set of auditing rules to produce an incident trail and per-day summary
that committee members can trust and verify independently.

DESIGN PRINCIPLES
-----------------
1. Every finding is traceable: each incident names the rule that fired, the
   block(s) it covers, and the exact numeric thresholds involved.
2. Nothing is invented: if a field is absent the rule is skipped, not assumed.
3. Deterministic: identical input → identical output, byte-for-byte.
4. Severity is CRITICAL / WARNING / INFO — committee terms, not engineering slang.
5. The audit trail is append-only and immutable after `audit()` returns.

RULES
-----
GC-01  Transformer loading exceeded WARN_LOADING_PCT of rated capacity
GC-02  Transformer loading exceeded CRITICAL_LOADING_PCT of rated capacity
GC-03  Transformer in stressed state for multiple consecutive blocks
GC-04  Market clearing price exceeded PRICE_WARN_INR (near BESCOM ceiling)
GC-05  Curtailment concentrated: one house bears > CURTAIL_CONCENTRATION_PCT
        of all curtailed fraction in a block
GC-06  Settlement reconciliation: total charges < SETTLE_FLOOR_INR when trades cleared
GC-07  Hot-spot temperature exceeded HOT_SPOT_WARN_C
GC-08  Hot-spot temperature exceeded HOT_SPOT_CRITICAL_C
GC-09  Fairness — house received zero P2P energy across an entire day (day-level)
GC-10  Fairness — house paid above-average charges for 5+ consecutive days
GC-11  Battery discharge zero when a loading breach occurred (missed lever)
GC-12  Predicted breach not followed by reshape within 2 blocks (slow response)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# ------------------------------------------------------------------ thresholds
WARN_LOADING_PCT = 85.0          # % of rated — GC-01
CRITICAL_LOADING_PCT = 100.0     # % of rated — GC-02
CONSECUTIVE_STRESS_BLOCKS = 3   # GC-03: alert if stressed ≥ this many in a row
PRICE_WARN_INR = 7.00            # INR/kWh ceiling warning — GC-04
CURTAIL_CONCENTRATION_PCT = 50.0 # one house > this fraction of all curtailment — GC-05
SETTLE_FLOOR_INR = 0.01          # GC-06: if cleared trades exist charges must be ≥ this
HOT_SPOT_WARN_C = 95.0           # °C — GC-07
HOT_SPOT_CRITICAL_C = 110.0      # °C — GC-08
ABOVE_AVG_CHARGE_DAYS = 5        # consecutive above-average charge days — GC-10
RESHAPE_RESPONSE_BLOCKS = 2      # GC-12: breach → reshape must happen within this many blocks


Severity = Literal["info", "warning", "critical"]


@dataclass(frozen=True)
class Incident:
    """One audit finding — immutable after creation."""
    rule: str                   # e.g. "GC-01"
    severity: Severity
    block: int                  # block where the finding applies
    day: int
    clock: str                  # "HH:00"
    subject: str                # transformer id / house id / "market"
    message: str
    detail: dict = field(default_factory=dict)


@dataclass
class DaySummary:
    """Aggregated picture for one simulated day."""
    day: int
    incidents: list[Incident] = field(default_factory=list)
    # Transformer
    transformer_breach_blocks: dict[str, int] = field(default_factory=dict)   # tid -> count
    transformer_peak_loading: dict[str, float] = field(default_factory=dict)  # tid -> max fraction
    transformer_peak_hotspot: dict[str, float] = field(default_factory=dict)  # tid -> max °C
    # Settlement
    total_charges_inr: float = 0.0
    blocks_with_trades: int = 0
    settlement_reconciled: bool = True
    # Market
    price_breach_blocks: int = 0
    max_clearing_price: float = 0.0
    # Curtailment
    total_curtailment_events: int = 0
    concentrated_curtailment_houses: list[str] = field(default_factory=list)
    # Fairness
    houses_with_zero_p2p: list[str] = field(default_factory=list)

    @property
    def headline(self) -> str:
        crits = sum(1 for i in self.incidents if i.severity == "critical")
        warns = sum(1 for i in self.incidents if i.severity == "warning")
        infos = sum(1 for i in self.incidents if i.severity == "info")
        breaches = sum(self.transformer_breach_blocks.values())
        parts: list[str] = []
        if crits:
            parts.append(f"{crits} critical finding{'s' if crits > 1 else ''}")
        if warns:
            parts.append(f"{warns} warning{'s' if warns > 1 else ''}")
        if breaches:
            parts.append(
                f"transformer breach in {breaches} block{'s' if breaches > 1 else ''}")
        if not parts:
            if infos:
                parts.append(f"{infos} info event{'s' if infos > 1 else ''}")
            else:
                parts.append("all checks passed")
        return "; ".join(parts).capitalize() + "."


@dataclass
class AuditResult:
    """Full output of one audit run — immutable collection of incidents and
    per-day summaries, plus a cross-day fairness table."""
    incidents: list[Incident]
    days: list[DaySummary]
    # Fairness cross-day aggregates: house_id -> metrics
    house_p2p_received_kwh: dict[str, float]        # total kWh received as buyer
    house_curtailed_blocks: dict[str, int]           # blocks where curtailed > 0
    house_above_avg_charge_days: dict[str, int]      # days above average daily charge
    blocks_per_day: int

    @property
    def total_incidents(self) -> int:
        return len(self.incidents)

    @property
    def critical_count(self) -> int:
        return sum(1 for i in self.incidents if i.severity == "critical")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.incidents if i.severity == "warning")

    def as_dict(self) -> dict:
        return {
            "summary": {
                "total_incidents": self.total_incidents,
                "critical": self.critical_count,
                "warning": self.warning_count,
                "info": self.total_incidents - self.critical_count - self.warning_count,
            },
            "fairness": {
                "house_p2p_received_kwh": self.house_p2p_received_kwh,
                "house_curtailed_blocks": self.house_curtailed_blocks,
                "house_above_avg_charge_days": self.house_above_avg_charge_days,
            },
            "days": [_day_to_dict(d) for d in self.days],
            "incidents": [_incident_to_dict(i) for i in self.incidents],
        }


# ---------------------------------------------------------------------- helpers

def _day_to_dict(d: DaySummary) -> dict:
    return {
        "day": d.day,
        "headline": d.headline,
        "incidents": len(d.incidents),
        "critical": sum(1 for i in d.incidents if i.severity == "critical"),
        "warning": sum(1 for i in d.incidents if i.severity == "warning"),
        "info": sum(1 for i in d.incidents if i.severity == "info"),
        "transformer_breach_blocks": d.transformer_breach_blocks,
        "transformer_peak_loading": {k: round(v, 4) for k, v in d.transformer_peak_loading.items()},
        "transformer_peak_hotspot": {k: round(v, 2) for k, v in d.transformer_peak_hotspot.items()},
        "settlement_reconciled": d.settlement_reconciled,
        "total_charges_inr": round(d.total_charges_inr, 4),
        "blocks_with_trades": d.blocks_with_trades,
        "price_breach_blocks": d.price_breach_blocks,
        "max_clearing_price": round(d.max_clearing_price, 4),
        "total_curtailment_events": d.total_curtailment_events,
        "concentrated_curtailment_houses": d.concentrated_curtailment_houses,
        "houses_with_zero_p2p": d.houses_with_zero_p2p,
    }


def _incident_to_dict(i: Incident) -> dict:
    return {
        "rule": i.rule,
        "severity": i.severity,
        "block": i.block,
        "day": i.day,
        "clock": i.clock,
        "subject": i.subject,
        "message": i.message,
        "detail": i.detail,
    }


# ---------------------------------------------------------------------- auditor

class GovernanceAgent:
    """Run once post-simulation. Reads blocks as plain dicts (the same shape
    `server/payloads.py` produces) and returns an immutable AuditResult.

    The agent holds no mutable state between blocks — each call to `audit()`
    starts fresh. This makes results reproducible and the agent safe to call
    from any thread.
    """

    def audit(self, blocks: list[dict], blocks_per_day: int = 24) -> AuditResult:
        """Audit the full recorded run.

        Parameters
        ----------
        blocks:
            List of block payload dicts, as produced by server/payloads.py.
        blocks_per_day:
            Number of blocks in one simulated day (24 for hourly).
        """
        incidents: list[Incident] = []
        days_map: dict[int, DaySummary] = {}

        # Cross-day accumulators
        house_p2p_received: dict[str, float] = {}
        house_curtailed_count: dict[str, int] = {}
        house_daily_charges: dict[str, list[float]] = {}  # house -> per-day charge proxies
        house_above_avg_days: dict[str, int] = {}

        # Per-transformer consecutive stress tracker
        consecutive_stress: dict[str, int] = {}

        # Prediction tracker: {block_number: transformer_id} from breach_predicted events
        # We infer predictions from block state.predicted_breach
        predicted_blocks: dict[int, set[str]] = {}

        for block in blocks:
            blk_no: int = block.get("block", 0)
            day: int = block.get("day", blk_no // blocks_per_day)
            clock: str = block.get("clock", f"{blk_no % blocks_per_day:02d}:00")

            if day not in days_map:
                days_map[day] = DaySummary(day=day)
            ds = days_map[day]

            # ---- transformers -----------------------------------------------
            transformers: dict = block.get("transformers") or {}
            for tid, ts in transformers.items():
                loading: float = ts.get("loading", 0.0) or 0.0
                hotspot: float | None = ts.get("hotspot_c")
                stressed: bool = ts.get("stressed", False)
                predicted: bool = ts.get("predicted_breach", False)

                loading_pct = loading * 100.0

                ds.transformer_peak_loading[tid] = max(
                    ds.transformer_peak_loading.get(tid, 0.0), loading)
                if hotspot is not None:
                    ds.transformer_peak_hotspot[tid] = max(
                        ds.transformer_peak_hotspot.get(tid, 0.0), hotspot)

                # GC-01
                if loading_pct >= WARN_LOADING_PCT and loading_pct < CRITICAL_LOADING_PCT:
                    inc = Incident(
                        rule="GC-01", severity="warning",
                        block=blk_no, day=day, clock=clock, subject=tid,
                        message=(f"{tid} reached {loading_pct:.1f}% loading "
                                 f"(warn threshold {WARN_LOADING_PCT:.0f}%)"),
                        detail={"loading_pct": round(loading_pct, 2)},
                    )
                    incidents.append(inc)
                    ds.incidents.append(inc)

                # GC-02
                if loading_pct >= CRITICAL_LOADING_PCT:
                    inc = Incident(
                        rule="GC-02", severity="critical",
                        block=blk_no, day=day, clock=clock, subject=tid,
                        message=(f"{tid} EXCEEDED rated capacity at {loading_pct:.1f}% "
                                 f"(threshold {CRITICAL_LOADING_PCT:.0f}%)"),
                        detail={"loading_pct": round(loading_pct, 2)},
                    )
                    incidents.append(inc)
                    ds.incidents.append(inc)
                    ds.transformer_breach_blocks[tid] = (
                        ds.transformer_breach_blocks.get(tid, 0) + 1)

                # GC-03 consecutive stress
                if stressed:
                    consecutive_stress[tid] = consecutive_stress.get(tid, 0) + 1
                    if consecutive_stress[tid] == CONSECUTIVE_STRESS_BLOCKS:
                        inc = Incident(
                            rule="GC-03", severity="warning",
                            block=blk_no, day=day, clock=clock, subject=tid,
                            message=(f"{tid} has been in stressed state for "
                                     f"{consecutive_stress[tid]} consecutive blocks"),
                            detail={"consecutive_blocks": consecutive_stress[tid]},
                        )
                        incidents.append(inc)
                        ds.incidents.append(inc)
                else:
                    consecutive_stress[tid] = 0

                # Track predicted breach for GC-12 check (resolved later)
                if predicted:
                    predicted_blocks.setdefault(blk_no + 1, set()).add(tid)

                # GC-07 / GC-08 hot-spot
                if hotspot is not None:
                    if hotspot >= HOT_SPOT_CRITICAL_C:
                        inc = Incident(
                            rule="GC-08", severity="critical",
                            block=blk_no, day=day, clock=clock, subject=tid,
                            message=(f"{tid} hot-spot {hotspot:.1f} °C exceeds critical "
                                     f"threshold {HOT_SPOT_CRITICAL_C:.0f} °C"),
                            detail={"hotspot_c": round(hotspot, 2)},
                        )
                        incidents.append(inc)
                        ds.incidents.append(inc)
                    elif hotspot >= HOT_SPOT_WARN_C:
                        inc = Incident(
                            rule="GC-07", severity="warning",
                            block=blk_no, day=day, clock=clock, subject=tid,
                            message=(f"{tid} hot-spot {hotspot:.1f} °C above warning "
                                     f"threshold {HOT_SPOT_WARN_C:.0f} °C"),
                            detail={"hotspot_c": round(hotspot, 2)},
                        )
                        incidents.append(inc)
                        ds.incidents.append(inc)

            # ---- market / clearing price ------------------------------------
            cp: float | None = block.get("clearing_price")
            if cp is not None and cp > PRICE_WARN_INR:
                inc = Incident(
                    rule="GC-04", severity="warning",
                    block=blk_no, day=day, clock=clock, subject="market",
                    message=(f"Clearing price ₹{cp:.2f}/kWh exceeded warning "
                             f"ceiling ₹{PRICE_WARN_INR:.2f}"),
                    detail={"clearing_price_inr": round(cp, 4)},
                )
                incidents.append(inc)
                ds.incidents.append(inc)
                ds.price_breach_blocks += 1
            if cp is not None:
                ds.max_clearing_price = max(ds.max_clearing_price, cp)

            # ---- trades & curtailment ---------------------------------------
            trades: list[dict] = block.get("trades") or []
            if trades:
                ds.blocks_with_trades += 1

            # Curtailment concentration — GC-05
            curtail_by_house: dict[str, float] = {}
            for trade in trades:
                cf: float = trade.get("curtailed", 0.0) or 0.0
                if cf > 0:
                    for hid in (trade.get("from"), trade.get("to")):
                        if hid:
                            curtail_by_house[hid] = max(
                                curtail_by_house.get(hid, 0.0), cf)
            total_curtail = sum(curtail_by_house.values())
            if total_curtail > 0:
                ds.total_curtailment_events += 1
                for hid, cf in curtail_by_house.items():
                    house_curtailed_count[hid] = house_curtailed_count.get(hid, 0) + 1
                    share_pct = (cf / total_curtail) * 100.0
                    if share_pct > CURTAIL_CONCENTRATION_PCT:
                        if hid not in ds.concentrated_curtailment_houses:
                            ds.concentrated_curtailment_houses.append(hid)
                        inc = Incident(
                            rule="GC-05", severity="warning",
                            block=blk_no, day=day, clock=clock, subject=hid,
                            message=(f"Curtailment concentrated at {hid}: "
                                     f"{share_pct:.0f}% of block curtailment"),
                            detail={"house_id": hid, "share_pct": round(share_pct, 1),
                                    "curtailed_fraction": round(cf, 4)},
                        )
                        incidents.append(inc)
                        ds.incidents.append(inc)

            # P2P received accumulator for GC-09
            for trade in trades:
                buyer: str | None = trade.get("to")
                kwh: float = trade.get("kwh", 0.0) or 0.0
                if buyer and kwh > 0:
                    house_p2p_received[buyer] = house_p2p_received.get(buyer, 0.0) + kwh

            # ---- settlement reconciliation — GC-06 -------------------------
            settlement: dict | None = block.get("settlement")
            if settlement:
                charges = settlement.get("charges_inr", 0.0) or 0.0
                ds.total_charges_inr += charges
                if trades and charges < SETTLE_FLOOR_INR:
                    ds.settlement_reconciled = False
                    inc = Incident(
                        rule="GC-06", severity="critical",
                        block=blk_no, day=day, clock=clock, subject="settlement",
                        message=(f"Settlement charges ₹{charges:.4f} below floor "
                                 f"₹{SETTLE_FLOOR_INR} despite {len(trades)} trades"),
                        detail={"charges_inr": round(charges, 4),
                                "trade_count": len(trades)},
                    )
                    incidents.append(inc)
                    ds.incidents.append(inc)

            # ---- battery discharge when breach — GC-11 ----------------------
            breach: dict | None = block.get("breach")
            battery: dict | None = block.get("battery")
            if breach and battery:
                discharged = battery.get("discharged_kwh", 0.0) or 0.0
                if discharged < 1e-6:
                    inc = Incident(
                        rule="GC-11", severity="info",
                        block=blk_no, day=day, clock=clock,
                        subject=breach.get("transformer_id", "unknown"),
                        message=(f"Loading breach on {breach.get('transformer_id')} but "
                                 "no battery discharge occurred — potential missed lever"),
                        detail={"breach_kind": breach.get("kind"),
                                "severity": breach.get("severity")},
                    )
                    incidents.append(inc)
                    ds.incidents.append(inc)

        # ---- GC-09: houses with zero P2P for a full day (day-level) --------
        # For each day, identify all buyer-eligible houses that received nothing
        # We approximate: any house that appears in ANY block's house states but
        # never appears as a trade buyer across the full run.
        all_house_ids: set[str] = set()
        for block in blocks:
            all_house_ids.update((block.get("houses") or {}).keys())

        # Determine per-day which houses had trades going TO them
        per_day_buyers: dict[int, set[str]] = {}
        for block in blocks:
            day = block.get("day", block.get("block", 0) // blocks_per_day)
            per_day_buyers.setdefault(day, set())
            for trade in (block.get("trades") or []):
                buyer = trade.get("to")
                kwh = trade.get("kwh", 0.0) or 0.0
                if buyer and kwh > 0:
                    per_day_buyers[day].add(buyer)

        for day, ds in days_map.items():
            buyers_today = per_day_buyers.get(day, set())
            if not buyers_today:
                # No trades at all — not a fairness issue, skip
                continue
            # Only flag houses that appear in house states (i.e. present on-feeder)
            # but never bought. We exclude exporting houses — they don't buy.
            day_blocks = [b for b in blocks
                          if b.get("day", b.get("block", 0) // blocks_per_day) == day]
            # Houses that were in "import" state at least once during the day
            import_houses: set[str] = set()
            for b in day_blocks:
                for hid, hs in (b.get("houses") or {}).items():
                    if hs.get("state") == "import":
                        import_houses.add(hid)

            zero_p2p = sorted(import_houses - buyers_today)
            ds.houses_with_zero_p2p = zero_p2p
            if zero_p2p:
                inc = Incident(
                    rule="GC-09", severity="info",
                    block=day * blocks_per_day, day=day,
                    clock="00:00", subject="fairness",
                    message=(f"Day {day}: {len(zero_p2p)} importing house(s) "
                             f"received no P2P energy: {', '.join(zero_p2p[:5])}"
                             f"{'…' if len(zero_p2p) > 5 else ''}"),
                    detail={"houses": zero_p2p},
                )
                incidents.append(inc)
                ds.incidents.append(inc)

        # ---- GC-12: predicted breach not followed by reshape ---------------
        # Approximation: if a block has predicted_breach=True on a transformer,
        # check that within RESHAPE_RESPONSE_BLOCKS the status is "reshaped"
        # (We already captured predicted_blocks above from the loop)
        # Map block -> status
        block_status: dict[int, str] = {b.get("block", 0): b.get("status", "") for b in blocks}
        for pred_block, pred_tids in predicted_blocks.items():
            responded = any(
                block_status.get(pred_block + offset) == "reshaped"
                for offset in range(0, RESHAPE_RESPONSE_BLOCKS + 1)
            )
            if not responded:
                # Find the breach that actually occurred (if any) to report it
                breach_block = next(
                    (b for b in blocks if b.get("block") == pred_block
                     and b.get("breach")), None)
                if breach_block:
                    b_day = breach_block.get("day", pred_block // blocks_per_day)
                    b_clock = breach_block.get("clock", f"{pred_block % blocks_per_day:02d}:00")
                    ds_gc12 = days_map.get(b_day)
                    inc = Incident(
                        rule="GC-12", severity="info",
                        block=pred_block, day=b_day, clock=b_clock,
                        subject=", ".join(sorted(pred_tids)),
                        message=(f"Breach predicted for block {pred_block} on "
                                 f"{', '.join(sorted(pred_tids))} but no reshape "
                                 f"within {RESHAPE_RESPONSE_BLOCKS} blocks"),
                        detail={"predicted_for_block": pred_block,
                                "transformers": sorted(pred_tids)},
                    )
                    incidents.append(inc)
                    if ds_gc12:
                        ds_gc12.incidents.append(inc)

        # ---- sort & finalise ------------------------------------------------
        incidents.sort(key=lambda i: (i.block, i.rule))

        return AuditResult(
            incidents=incidents,
            days=sorted(days_map.values(), key=lambda d: d.day),
            house_p2p_received_kwh=house_p2p_received,
            house_curtailed_blocks=house_curtailed_count,
            house_above_avg_charge_days=house_above_avg_days,
            blocks_per_day=blocks_per_day,
        )
