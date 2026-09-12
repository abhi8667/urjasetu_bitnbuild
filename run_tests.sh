#!/usr/bin/env bash
# Run every suite and print a checklist. No arguments.
#
#   ./run_tests.sh          full run (~40s)
#   ./run_tests.sh --quick  skip the slow scenario/derate sweeps (~10s)
#
# Requires Python 3.11+. scipy is needed only for the reshape LP — everything
# else runs on the standard library alone.

cd "$(dirname "$0")" || exit 1
QUICK=""
[ "$1" = "--quick" ] && QUICK=1

pass=0; fail=0; skipped=0
line() { printf '%s\n' "------------------------------------------------------------"; }

have_scipy=$(python3 -c "import scipy" 2>/dev/null && echo yes || echo no)

run() {                      # run <label> <file> [needs_scipy]
    local label="$1" file="$2" needs="$3"
    if [ -n "$needs" ] && [ "$have_scipy" = "no" ]; then
        printf '  SKIP  %-44s (needs scipy)\n' "$label"; skipped=$((skipped+1)); return
    fi
    local out
    out=$(python3 "$file" 2>&1)
    if [ $? -eq 0 ]; then
        printf '  PASS  %-44s %s\n' "$label" "$(echo "$out" | tail -1)"
        pass=$((pass+1))
    else
        printf '  FAIL  %-44s\n' "$label"
        echo "$out" | grep -E "FAIL|ERROR" | head -5 | sed 's/^/          /'
        fail=$((fail+1))
    fi
}

line; echo "  UrjaSetu — test checklist"; line
echo "  python  $(python3 -V 2>&1 | cut -d' ' -f2)"
echo "  scipy   $have_scipy   (reshape LP only; engine runs without it)"
line

echo "  CONTRACTS & DATA"
run "feed contracts (dataset, joins, topology)" tests/test_feed_contracts.py
run "configuration (§8, CF1)"                   tests/test_config.py

echo
echo "  TRACK B — market, agents, money"
run "phase 1  bus, market, tick loop"          tests/test_phase1_market_loop.py
run "phase 2  prosumer, consumer, settlement"  tests/test_phase2_agents_money.py
run "phase 3+4 persistence, baseline, compare" tests/test_phase34_persistence_baseline.py

echo
echo "  TRACK C — grid protection"
if [ "$have_scipy" = "yes" ]; then
    out=$(python3 tests/grid/run_all.py 2>&1)
    if echo "$out" | grep -q "0 failed"; then
        printf '  PASS  %-44s %s\n' "sentinel, battery, flow, health" \
            "$(echo "$out" | grep Overall | sed 's/.*Result: //')"
        pass=$((pass+1))
    else
        printf '  FAIL  %-44s\n' "sentinel, battery, flow, health"
        echo "$out" | grep FAIL | head -5 | sed 's/^/          /'
        fail=$((fail+1))
    fi
else
    printf '  SKIP  %-44s (needs scipy)\n' "sentinel, battery, flow, health"; skipped=$((skipped+1))
fi

echo
echo "  PRD INTEGRATION CHECKS"
run "FL4  energy conservation (§10.2)"         tests/test_fl4_energy_conservation.py scipy
run "run_summary.json completeness (§10, §13)" tests/test_run_summary.py scipy
[ -z "$QUICK" ] && run "breach resolution under derate (§10.6)" tests/test_breach_resolution.py scipy
[ -z "$QUICK" ] && run "cross-scenario sweep (9 configs)"       tests/test_scenarios.py scipy
[ -n "$QUICK" ] && { printf '  SKIP  %-44s (--quick)\n' "breach resolution (§10.6)"; \
                     printf '  SKIP  %-44s (--quick)\n' "cross-scenario sweep"; skipped=$((skipped+2)); }

line
printf '  %d suites passed, %d failed, %d skipped\n' "$pass" "$fail" "$skipped"
line
exit $((fail > 0))
