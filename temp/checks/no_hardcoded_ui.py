#!/usr/bin/env python3
"""Fail if a fabricated constant is back in the UI's RENDERED output.

Comment-aware on purpose. The source carries comments that quote the old
constants ("'+12%' was a constant with an up-arrow beside it...") because
knowing what was wrong is what stops it being reintroduced. A plain grep flags
those and reports a false positive, so this strips comments and string-free
JSX text before searching.

    python3 temp/checks/no_hardcoded_ui.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: Constants that were rendered to screen as if they were measurements.
BANNED = [
    "4.8 kWp",            # PV capacity, for every PV premises
    "10 kWh LiFePO4",     # battery capacity, for every battery premises
    # Contextual, not bare: "0.78" on its own also matches a 3D geometry
    # coordinate. What was wrong was specifically a SoC fallback.
    "soc_frac ?? 0.78",   # state-of-charge fallback
    "148.2",              # traded kWh fallback
    "4.20",               # clearing price fallback
    "+12%", "-6%",        # HUD deltas with arrows, never computed
    "3,450",              # "today's volume"
    "98.6%",              # "grid balance"
    "684",                # cross-subsidy surcharge
    "Txr_North", "Txr_Central", "Txr_South",   # gauges for transformers that do not exist
    "186000",             # deferred capex
]

#: Files whose invented numbers are deliberate — the offline fallback.
ALLOWED = {"src/demoFixture.ts"}


def strip_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", " ", source, flags=re.S)     # /* block */
    source = re.sub(r"\{\s*/\*.*?\*/\s*\}", " ", source, flags=re.S)  # JSX {/* */}
    source = re.sub(r"^\s*//.*$", " ", source, flags=re.M)     # // line
    source = re.sub(r"(?<![:\w])//[^\n\"'`]*$", " ", source, flags=re.M)  # trailing //
    return source


def main() -> int:
    failures: list[str] = []
    for path in sorted((ROOT / "UI" / "src").rglob("*.ts*")):
        rel = path.relative_to(ROOT / "UI").as_posix()
        if rel in ALLOWED:
            continue
        code = strip_comments(path.read_text())
        for needle in BANNED:
            if needle in code:
                for n, line in enumerate(code.splitlines(), 1):
                    if needle in line:
                        failures.append(f"{rel}:{n}  {needle!r}  -> {line.strip()[:90]}")
    if failures:
        print("FAIL — fabricated constants are back in rendered UI code:")
        for line in failures:
            print("  " + line)
        return 1
    print(f"PASS — none of the {len(BANNED)} known fabricated constants appear in "
          f"UI code (demoFixture.ts excluded by design)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
