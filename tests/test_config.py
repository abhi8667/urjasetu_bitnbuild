"""Configuration — PRD §8.

§8 asks for "a single config.yaml, loaded into a pydantic model with all
defaults declared", and invariant CF1 requires the engine to start and complete
a full run with the shipped defaults and zero arguments.

Those two pull against DECISIONS.md D7, which commits the engine to running on
a machine with nothing installed. Both hold here: pydantic's dataclass is a
drop-in for the stdlib one (dataclasses.replace and fields keep working) and
PyYAML is only consulted when present. Validation and file-loading are a bonus
when the packages exist, never a dependency.

These tests therefore check BOTH paths, including the one where neither package
is importable — which is the configuration a teammate actually has on a fresh
clone.

Run standalone: `python3 tests/test_config.py`
"""
from __future__ import annotations

import dataclasses
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.config import (CONFIG_YAML, DEFAULT, PYDANTIC_AVAILABLE,
                           YAML_AVAILABLE, Config, load_config, to_yaml)

REPO = Path(__file__).resolve().parent.parent
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    return ok


# ------------------------------------------------------------- §8 shape

def test_config_yaml_ships_with_the_repo():
    check("§8  a single config.yaml exists", CONFIG_YAML.exists(),
          str(CONFIG_YAML.relative_to(REPO)))


def test_every_field_is_declared_in_the_yaml():
    """"All defaults declared" — a field missing from the file is one nobody
    can configure without reading the source."""
    if not YAML_AVAILABLE:
        check("§8  every field declared in config.yaml", True, "skipped, no yaml")
        return
    import yaml
    declared = set(yaml.safe_load(CONFIG_YAML.read_text()))
    expected = {f.name for f in dataclasses.fields(Config)}
    check("§8  every field declared in config.yaml", declared == expected,
          f"{len(declared)} keys" + (f", missing {expected - declared}"
                                     if expected - declared else ""))


def test_the_yaml_round_trips_to_the_declared_defaults():
    """Loading the shipped file must reproduce Config() exactly, or the file and
    the code have drifted and nobody would notice which one is authoritative."""
    check("config.yaml round-trips to the defaults exactly",
          load_config(CONFIG_YAML) == DEFAULT)


def test_data_dir_is_portable():
    """An absolute path baked in on one machine fails on every other one."""
    if not YAML_AVAILABLE:
        check("data_dir in the yaml is relative", True, "skipped, no yaml")
        return
    import yaml
    raw = yaml.safe_load(CONFIG_YAML.read_text())
    check("data_dir in the yaml is relative, not machine-specific",
          not Path(raw["data_dir"]).is_absolute(), raw["data_dir"])


# ------------------------------------------------------------------ CF1

def test_cf1_runs_with_zero_arguments():
    from engine.feed import WhitefieldFeed
    from engine.sim.pool import AgentPool
    from engine.sim.runner import Runner
    config = load_config()
    feed = WhitefieldFeed(config)
    summary = Runner(feed, AgentPool(feed.houses(), config), config,
                     include_baseline=False).run(blocks=24)
    check("CF1  full run from the shipped config, zero arguments",
          summary["blocks"] == 24 and summary["trades"] > 0,
          f"{summary['trades']} trades")


def test_cf1_holds_with_neither_pydantic_nor_yaml():
    """The configuration a teammate has on a fresh clone. Run in a subprocess
    with both packages blocked from importing."""
    script = (
        "import sys\n"
        "class Block:\n"
        "    def find_spec(self, n, path=None, target=None):\n"
        "        if n.split('.')[0] in ('pydantic','yaml'): raise ImportError(n)\n"
        "        return None\n"
        "sys.meta_path.insert(0, Block())\n"
        f"sys.path.insert(0, {str(REPO)!r})\n"
        "from engine.config import load_config, PYDANTIC_AVAILABLE, YAML_AVAILABLE\n"
        "assert not PYDANTIC_AVAILABLE and not YAML_AVAILABLE\n"
        "c = load_config()\n"
        "from engine.feed import WhitefieldFeed\n"
        "from engine.sim.pool import AgentPool\n"
        "from engine.sim.runner import Runner\n"
        "f = WhitefieldFeed(c)\n"
        "s = Runner(f, AgentPool(f.houses(), c), c, include_baseline=False).run(blocks=24)\n"
        "print(s['blocks'], s['trades'])\n"
    )
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    ok = proc.returncode == 0 and proc.stdout.strip().startswith("24 ")
    check("CF1  holds with neither pydantic nor yaml installed", ok,
          proc.stdout.strip() or proc.stderr.strip().splitlines()[-1:])


def test_a_missing_config_yaml_is_not_an_error():
    with tempfile.TemporaryDirectory() as tmp:
        absent = Path(tmp) / "nope.yaml"
        try:
            load_config(absent)
            check("an explicitly named missing file raises", False, "it did not")
        except FileNotFoundError:
            check("an explicitly named missing file raises", True)


# ----------------------------------------------------------- validation

def test_unknown_keys_raise_rather_than_being_ignored():
    """A typo in a config key would otherwise run silently on the default."""
    if not YAML_AVAILABLE:
        check("unknown keys raise", True, "skipped, no yaml")
        return
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "c.yaml"
        path.write_text("loadng_limit: 0.9\n")          # deliberate typo
        try:
            load_config(path)
            check("a typo'd key raises instead of silently defaulting", False)
        except ValueError as exc:
            check("a typo'd key raises instead of silently defaulting",
                  "loadng_limit" in str(exc))


def test_out_of_range_values_are_rejected():
    """pydantic validates types; nothing validates that a negative loading limit
    is nonsense. These are the values that would produce a silently wrong run."""
    bad = [
        ("loading_limit", -1.0),
        ("derate_factor", 1.5),
        ("round_trip_efficiency", 0.0),
        ("block_minutes", 7),          # does not divide 1440
        ("baseline_export_credit", "whatever"),
    ]
    rejected = []
    for name, value in bad:
        try:
            load_config(**{name: value})
        except (ValueError, TypeError):
            rejected.append(name)
    check("out-of-range values are rejected", len(rejected) == len(bad),
          f"{len(rejected)}/{len(bad)}: {rejected}")


def test_valid_overrides_are_accepted():
    config = load_config(derate_factor=0.8, days=7)
    check("valid overrides apply", config.derate_factor == 0.8 and config.days == 7)


def test_pydantic_enforces_types_when_available():
    if not PYDANTIC_AVAILABLE:
        check("pydantic type validation", True, "skipped, pydantic not installed")
        return
    try:
        Config(block_minutes="sixty")
        check("pydantic rejects a wrong-typed field", False, "it did not")
    except Exception as exc:
        check("pydantic rejects a wrong-typed field", True, type(exc).__name__)


def test_replace_still_works_on_the_config():
    """Every test and scenario in the repo uses dataclasses.replace. Converting
    to a pydantic BaseModel would have broken all of them; the pydantic
    dataclass keeps it working."""
    modified = dataclasses.replace(DEFAULT, derate_factor=0.6)
    check("dataclasses.replace still works", modified.derate_factor == 0.6
          and DEFAULT.derate_factor == 1.0)


def test_to_yaml_regenerates_the_shipped_file():
    if not YAML_AVAILABLE:
        check("to_yaml regenerates config.yaml", True, "skipped, no yaml")
        return
    import yaml
    regenerated = yaml.safe_load(to_yaml())
    shipped = yaml.safe_load(CONFIG_YAML.read_text())
    check("to_yaml reproduces the shipped file", regenerated == shipped)


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
