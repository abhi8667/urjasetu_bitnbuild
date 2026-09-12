#!/usr/bin/env python3
"""Fail if the kW->kVA conversion is written anywhere but engine/physics.py.

A transformer is rated in kVA; the meters report kW. Every module that compares
them must divide by the SAME power factor. It was a literal 0.95 in grid/flow.py
and grid/health.py, an inline `/ 0.95` in engine/sim/baseline.py, and absent
entirely from grid/sentinel.py — so the sentinel measured a transformer 5.3%
cooler than the health agent measured the same transformer in the same block.
F_AA is exponential in hot-spot temperature, so that is not a rounding error.

Comment-aware: the docstrings deliberately quote the old literal.

    python3 temp/checks/one_power_factor.py
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
#: Modules allowed to carry the value. `engine/physics.py` does the conversion;
#: `engine/config.py` DECLARES the default, which is the whole point — it is a
#: configuration value, not a literal in four agents.
HOME = {"engine/physics.py", "engine/config.py"}
#: Values that would be a power factor written by hand.
SUSPECT = {0.95, 1 / 0.95}


def main() -> int:
    failures: list[str] = []
    for path in sorted([*(ROOT / "engine").rglob("*.py"), *(ROOT / "grid").rglob("*.py")]):
        rel = path.relative_to(ROOT).as_posix()
        if rel in HOME:
            continue
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError as exc:                       # pragma: no cover
            failures.append(f"{rel}: will not parse ({exc})")
            continue
        # ast drops comments and docstrings are Expr(Constant(str)), so a
        # numeric literal found here is genuinely in executable code.
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, float):
                if any(abs(node.value - s) < 1e-9 for s in SUSPECT):
                    failures.append(
                        f"{rel}:{node.lineno}  literal {node.value} — use "
                        f"engine.physics.apparent_kva / config.power_factor")
    if failures:
        print("FAIL — the power factor is written outside engine/physics.py:")
        for line in failures:
            print("  " + line)
        return 1
    print("PASS — the power factor is declared once in engine/config.py and "
          "applied once in engine/physics.py; no agent writes it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
