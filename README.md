# UrjaSetu

P2P energy trading for an Indian LT distribution street, with agents that keep
the transformers alive. Sixty metered premises, four transformers, Whitefield.

```
docs/           the plan — read your own file, plus urjasetu-master-plan.md
data/           the dataset. LOCKED. do not regenerate it to fix an engine bug
engine/         the shared foundation: contracts, config, feed, algo stubs
tests/          contract smoke tests — stdlib only, run them now
DECISIONS.md    every choice the dataset forced, one line each
```

## Start here

```bash
python tests/test_feed_contracts.py     # 9/9, no dependencies needed
```

That proves the dataset loads, the contracts hold, and breaches are reachable.

## What is already frozen

| Contract | File | Owner |
|---|---|---|
| 1 — algorithm signatures + stubs | `engine/algo/*.py` | D |
| 2 — payload shapes | UI PRD §4.3, `fixtures/day-one.json` | A + B |
| 3 — domain dataclasses | `engine/domain.py` | B |
| config defaults | `engine/config.py` | B |
| meter feed over the locked data | `engine/feed.py` | B |

A signature changes only by announcement, with everyone affected adapting in the
same sitting. Never silently.

## Four tracks, from here

**A — interface.** Topology is 64 nodes (60 premises + 4 EV hubs) on 4
transformers, and a day is 24 blocks, not 96. Build `fixtures/day-one.json` off
`engine/feed.py` rather than by hand, and regenerate it at every integration
point.

**B — market.** `config.py`, `domain.py` and `feed.py` are done; yours to own
from here. Next is `bus.py`, then `sim/runner.py`. The feed already satisfies
the `MeterFeed` protocol, so the tick loop has real ticks on day one.

**C — grid.** `domain.py` gives you `Breach`, `Trade` and `MeterTick` now.
Build the sentinel against `engine/feed.py` directly — it breaches 162 blocks in
720, all in the 18:00–21:00 window, so you have real cases without injecting
any.

**D — algorithms.** Stubs are in `engine/algo/` and importable. Upgrade them in
place, in the order in your plan: auction, forecast, powerflow, reshape_lp,
thermal.

## The three checks that prove it works

Money conserved (ST1). Energy conserved (FL4). Baseline ages faster than P2P
over 30 days. Run the third at hour 24, not hour 33.
