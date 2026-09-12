"""Runner for all Track C unit tests and checkpoint gates.

Runs under pytest, or standalone with `python3 tests/grid/run_all.py`.
Stdlib only, matching DECISIONS.md D7 and test_feed_contracts.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tests.grid import test_sentinel, test_battery, test_flow, test_health


def main() -> int:
    suites = [
        ("Sentinel (SN1, SN2, Hour-12 Gate)", test_sentinel),
        ("Battery (BT1-BT4, Custody, Claims)", test_battery),
        ("Flow (FL1-FL4, Fallback, Hour-20 Gate)", test_flow),
        ("Health (HL1-HL4, IEEE C57.91, Adders)", test_health),
    ]

    total_passed = 0
    total_failed = 0

    print("=" * 60)
    print("Running Track C (Grid-Side Agents) Test Suite")
    print("=" * 60)

    for name, module in suites:
        print(f"\n--- Suite: {name} ---")
        fns = [getattr(module, attr) for attr in dir(module) if attr.startswith("test_") and callable(getattr(module, attr))]
        failed = 0
        for fn in fns:
            try:
                fn()
                print(f"  PASS  {fn.__name__}")
                total_passed += 1
            except Exception as exc:
                failed += 1
                total_failed += 1
                print(f"  FAIL  {fn.__name__}: {exc}")
        print(f"Summary for {name}: {len(fns) - failed}/{len(fns)} passed")

    print("\n" + "=" * 60)
    print(f"Overall Track C Result: {total_passed} passed, {total_failed} failed")
    print("=" * 60)
    return 1 if total_failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
