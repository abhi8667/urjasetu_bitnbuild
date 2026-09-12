"""run_summary.json — PRD §10 and §13.

§10 names exactly what the artifact must contain; §13 makes its completeness
part of the definition of done. The summary previously carried the market
figures only, so the grid, money and baseline halves of the run — the numbers
the whole project argues about — were computed and then discarded.

Run standalone: `python3 tests/test_run_summary.py`
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.agents.settlement import SettlementAgent
from engine.config import DEFAULT as CONFIG
from engine.feed import WhitefieldFeed
from engine.sim.pool import AgentPool
from engine.sim.runner import Runner
from grid import BatteryBook, FlowAgent, GridSentinel, TransformerHealthAgent

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    return ok


def _run(blocks=240, with_grid=True, include_baseline=True):
    feed = WhitefieldFeed(CONFIG)
    houses, transformers = feed.houses(), feed.transformers()
    pool = AgentPool(houses, CONFIG)
    settle = SettlementAgent(houses, CONFIG, consumers=pool.consumers, feed=feed)
    kw = {}
    if with_grid:
        kw = dict(sentinel=GridSentinel(transformers, houses, CONFIG),
                  flow=FlowAgent(transformers, houses, CONFIG),
                  health=TransformerHealthAgent(transformers, CONFIG, houses),
                  batteries=BatteryBook(houses, CONFIG))
    runner = Runner(feed, pool, CONFIG, settlement=settle,
                    include_baseline=include_baseline, **kw)
    return runner, runner.run(blocks=blocks)


# --------------------------------------------------- §10 required fields

def test_every_field_prd_names_is_present():
    """§10: total traded kWh, mean clearing price, breaches by kind, reshape and
    fallback counts, cumulative loss of life per transformer, DISCOM charge
    revenue, and the same figures for the baseline path."""
    _, summary = _run()
    required = {
        "traded_kwh": "total traded kWh",
        "mean_clearing_price_inr": "mean clearing price",
        "breaches_by_kind": "count of breaches by kind",
        "reshapes_applied": "count of reshapes",
        "fallback_curtailments": "count of fallbacks",
        "loss_of_life_hours": "cumulative loss of life per transformer",
        "discom_charge_revenue_inr": "total DISCOM charge revenue",
        "baseline": "the same figures for the baseline path",
    }
    missing = [f"{k} ({desc})" for k, desc in required.items() if k not in summary]
    check("§10  every required field present", not missing,
          f"{len(required)} fields" + (f", missing: {missing}" if missing else ""))


def test_loss_of_life_is_per_transformer_not_aggregate():
    _, summary = _run()
    life = summary.get("loss_of_life_hours", {})
    check("loss of life is broken out per transformer",
          isinstance(life, dict) and len(life) == 4,
          f"{sorted(life)}")


def test_breaches_are_broken_out_by_kind():
    _, summary = _run()
    kinds = summary.get("breaches_by_kind", {})
    check("breaches are counted by kind",
          isinstance(kinds, dict) and sum(kinds.values()) == summary["breaches_total"],
          f"{kinds}")


def test_baseline_figures_allow_a_real_comparison():
    _, summary = _run()
    base = summary.get("baseline", {})
    needed = {"household_bills_inr", "discom_charge_revenue_inr",
              "loss_of_life_hours_total"}
    check("baseline carries comparable figures", needed <= set(base),
          f"baseline ages {base.get('loss_of_life_hours_total', 0):.1f} h vs "
          f"p2p {summary.get('loss_of_life_hours_total', 0):.1f} h")


# ------------------------------------------------------------ D1 safety

def test_summary_carries_nothing_wall_clock():
    """D1 can only hold if no timing value reaches the summary. Timing lives on
    Runner.median_tick_ms instead."""
    _, summary = _run(blocks=48)
    leaked = [k for k in summary
              if any(w in k.lower() for w in ("_ms", "seconds", "elapsed", "wall"))]
    check("no wall-clock value in the summary", not leaked, str(leaked))


def test_two_identical_runs_produce_byte_identical_json():
    """D1, on the real artifact rather than the in-memory dict."""
    _, first = _run(blocks=96)
    _, second = _run(blocks=96)
    a = json.dumps(first, indent=2, sort_keys=True)
    b = json.dumps(second, indent=2, sort_keys=True)
    check("§10.1  two runs give byte-identical JSON", a == b,
          f"{len(a)} bytes")


def test_summary_writes_to_disk():
    runner, _ = _run(blocks=48)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "run_summary.json"
        runner.write_run_summary(path)
        on_disk = json.loads(path.read_text())
        check("run_summary.json is emitted and reloads",
              on_disk == runner.summary, f"{path.stat().st_size} bytes")


# ------------------------------------------------- degrades gracefully

def test_summary_still_valid_without_the_grid_agents():
    """C's modules are optional; the summary must not require them."""
    _, summary = _run(blocks=48, with_grid=False)
    ok = "traded_kwh" in summary and "loss_of_life_hours" not in summary
    check("summary degrades cleanly with no grid agents", ok,
          "market and money fields present, grid fields absent")


def test_baseline_can_be_skipped_for_speed():
    _, summary = _run(blocks=48, include_baseline=False)
    check("baseline is optional (include_baseline=False)",
          "baseline" not in summary)


def test_energy_figures_are_internally_consistent():
    """traded = delivered + transmission loss, to 1e-6. Ties the summary's own
    numbers to FL4 rather than letting them drift apart."""
    _, summary = _run()
    traded = summary["traded_kwh"]
    delivered = summary["delivered_kwh"]
    lost = summary["transmission_loss_kwh"]
    check("traded == delivered + losses in the summary itself",
          abs(traded - (delivered + lost)) < 1e-6,
          f"{traded:.4f} vs {delivered:.4f} + {lost:.4f}")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        try:
            fn()
        except Exception as exc:
            RESULTS.append((fn.__name__, False))
            print(f"  ERROR  {fn.__name__}: {type(exc).__name__}: {exc}")
    failed = sum(1 for _, ok in RESULTS if not ok)
    print(f"\n{len(RESULTS) - failed}/{len(RESULTS)} checks passed")
    sys.exit(1 if failed else 0)
