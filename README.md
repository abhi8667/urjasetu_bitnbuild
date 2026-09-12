# UrjaSetu

P2P energy trading for an Indian LT distribution street, with agents that keep
the transformers alive. Sixty metered premises, four transformers, Whitefield.

```
docs/           the plan — read your own file, plus urjasetu-master-plan.md
data/           the dataset. LOCKED, and only engine/feed.py reads it
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
transformers, and a day is 24 blocks, not 96. `feed.sites()` carries real
Whitefield coordinates and a `building_type` per premises — your layout and your
sprites — but read them off the `scene` payload B publishes, never out of the
JSON, or your fixture drifts from the engine. Build `fixtures/day-one.json` off
the feed rather than by hand, and regenerate it at every integration point.

**B — market.** `config.py`, `domain.py` and `feed.py` are done; yours to own
from here. Next is `bus.py`, then `sim/runner.py`. The feed already satisfies
the `MeterFeed` protocol, so the tick loop has real ticks on day one. Settlement
uses `feed.transmission_loss_pct(house_id)` per premises, not a flat figure —
FL4 has no losses term otherwise. Ignore the `p2p_*_price` columns in the year
CSV: your auction discovers its own price.

**C — grid.** `domain.py` gives you `Breach`, `Trade` and `MeterTick` now.
Build the sentinel against `engine/feed.py` directly — it breaches 162 blocks in
720, all in the 18:00–21:00 window, so you have real cases without injecting
any. Take `rating_kva` from `feed.transformers()`; reading `kva_rating` out of
`transformer_registry.json` gives you the installed 250–500 kVA and your
sentinel will never fire.

**D — algorithms.** Stubs are in `engine/algo/` and importable. Upgrade them in
place, in the order in your plan: auction, forecast, powerflow, reshape_lp,
thermal. Your functions never see the dataset — they take numbers as arguments.
For fixtures, the real ranges are ratings 63–125 kVA, per-DT load 8–140 kVA,
ambient 21–28 °C, 8–15 trades per DT per block, batteries 10 kWh at ±5 kW.

## The three checks that prove it works

Money conserved (ST1). Energy conserved (FL4). Baseline ages faster than P2P
over 30 days. Run the third at hour 24, not hour 33.
