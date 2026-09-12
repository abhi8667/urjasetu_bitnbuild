# DECISIONS

Choices the spec left open, or that the locked dataset forced. One line each on
what we chose and why, so nobody re-litigates it at hour 29 and so a judge gets
a straight answer.

**The dataset is locked.** Every gap between `data/` and the engine spec is
closed in code — `engine/config.py` or `engine/feed.py` — never by regenerating
a CSV.

---

### D1 — Transformer ratings are overridden in config, not read from the JSON

`transformer_registry.json` rates the four DTs at 500/250/250/250 kVA. Measured
against the telemetry, peak loading is **21–29%**. No breach is reachable at any
defensible `loading_limit`, which would leave the sentinel, the flow agent, the
reshape LP and the ageing signal as dead code — that is the entire project.

`config.rating_kva` overrides them to **125/63/63/63 kVA**, standard Indian LT
distribution sizings for 12–17 premises per DT. Measured over a 30-day run:

| DT | premises | rating | peak K | breach blocks / 720 |
|---|---|---|---|---|
| DT-1 | 12 + hub | 125 kVA | 1.27 | 63 |
| DT-2 | 17 + hub | 63 kVA | 0.95 | 0 |
| DT-3 | 14 + hub | 63 kVA | 1.29 | 69 |
| DT-4 | 17 + hub | 63 kVA | 1.32 | 30 |

Breaches land in the **18:00–21:00 evening peak**, on every simulated day, 1–3
transformers at a time. DT-2 never breaches, which gives the compare screen a
healthy control. `config.derate_factor` still forces one on demand for the demo.

**Consequence for the narrative:** the breach is evening-peak-driven, not
solar-export-driven. Daylight surplus is only 195 kWh/day against 2060 kWh of
demand and peak reverse flow is 19.8 kW, so a daylight export breach is not
honestly reachable on this data. The story is therefore: daytime surplus is
stored, the evening peak breaches, and the flow agent discharges batteries and
curtails flexible EV charging instead of cutting anyone off. That is closer to
how Indian LT transformers actually fail, and it is the master plan's own
sentence — "the energy comes back out at the evening peak."

### D2 — A block is one hour, not fifteen minutes

The telemetry is hourly. Interpolating to 15 minutes would invent precision we
do not have. `config.block_minutes = 60`, so 24 blocks a day and 720 in a
30-day run. Anything per-block scales off `config.block_hours` — never a literal
`0.25`. The evening price window moved from blocks 68–84 to `config.evening_blocks`
= 17–21.

### D3 — 30 days are built by scaling the one measured day

`telemetry/` covers a single Saturday. Replaying it unchanged would make every
day identical and the 30-day ageing comparison meaningless. `feed._day_scale`
multiplies each simulated day by that calendar day's irradiance and demand from
the year-long aggregate, relative to 12 Sep, clamped to [0.35, 1.45] on
generation and [0.85, 1.15] on load. Cloudy days come out cloudy and the
seasonal shape survives. Flag it as "one measured day, seasonally scaled" in the
run summary and on the provenance slide.

### D4 — The four EV hubs are modelled as pseudo-premises

The hubs draw 18–44 kW on their transformer but sit behind no meter. Smearing
that across real households would corrupt their bills. Each hub becomes a
`House` with no PV and no battery (`EVHUB-DT1`…`DT4`), so it loads the DT for
the sentinel and pays a bill of its own. 60 metered premises + 4 hubs = **64
nodes**. Toggle with `config.include_ev_hubs_as_houses`.

### D5 — Consumption in the dataset is gross, and maps straight to `MeterTick`

`smart_meters.csv:current_consumption_kw` is gross, not net of own generation —
a solar premises at noon shows consumption, generation and export at the same
time. No de-netting in the feed.

### D6 — Fields the registry has no column for are synthesised in the feed

| Field | Source |
|---|---|
| `House.phase` | round-robin A/B/C **within each transformer**, so a balanced street never shows a phase breach purely from meter numbering |
| `House.battery_max_kw` | 5.0 kW — what `home_batteries.csv` actually ranges to. `data/README.md` previously claimed ±11 kW; the data does not support it |
| `House.retail_tariff` | effective INR/kWh under BESCOM's **stepped** slabs on (daily import × 30). Billing the whole draw at the top band puts all 60 premises on ₹8.40; stepped gives ₹7.18–8.40 |
| `Transformer.rated_top_oil_rise_c` / `rated_hotspot_rise_c` | 55 °C / 80 °C, IEEE C57.91 oil-immersed distribution defaults |
| `Transformer.rated_life_hours` | 180,000 h (C57.91 normal insulation life) |
| `Transformer.replacement_cost_inr` | ₹2.5 lakh, mid-point of the ₹2–3 lakh the master plan quotes |

### D7 — Stdlib only in the scaffold

`pydantic`, `numpy`, `scipy` and `pytest` are all permitted by the PRD and all
absent from a clean machine. `config.py`, `domain.py`, `feed.py` and the algo
stubs use the standard library alone, so all four tracks run `python
tests/test_feed_contracts.py` at hour 0 without installing anything. D brings
`scipy` in with the real LP; B swaps `Config` to pydantic when validation
earns its keep. Neither changes a signature.

### D8 — The feed's RNG is separate from the engine's

PRD §3.4 wants one seeded `numpy.random.Generator` on the tick path. The feed's
`random.Random(config.seed)` is used only for forecast noise and never by an
agent, so determinism (D1) holds: two runs with the same config produce
identical ticks. Verified by `test_feed_is_deterministic`.

### D9 — Provenance: the data is modelled, not measured

`fetch_real_data.py` never calls NASA POWER, Open-Meteo or PVGIS — those
functions print the URLs you would use. The CSVs come from published monthly
averages plus seeded noise, and `grid_price_inr_per_kwh` is the KERC ToD rate
plus σ≈₹0.30 of noise, not the published tariff verbatim. Tariffs and P2P
charges themselves are used verbatim. Say "physically modelled from published
averages"; no open Indian dataset pairs household load with rooftop PV at meter
level, and naming that limit reads as rigour.

### D10 — The compare screen leads with life saved, not bill saved

P2P can cover about **9.5%** of daily demand on this data (195 kWh surplus vs
2060 kWh demand), so "household bill down" will be a modest number. 99% of that
surplus is matchable within the same transformer, so the KERC same-DT constraint
costs almost nothing. Put transformer life saved and DISCOM revenue on screen
largest; quote the bill saving honestly as a percentage of the daytime bill.
`config.max_bid_kwh_per_block` (3.0) caps how much any one buyer can take per
block, so scarce surplus is competed for rather than swallowed by the first bid.

### D11 — Coordinates, building type and loss reach the engine as `Site`, not on `House`

The registry carries real Whitefield lat/lon for all 60 premises, a
`building_type` of `res`/`apt`/`com`, and a per-premises `transmission_loss_pct`
of 3.25–6.75% (exactly `3 + 0.02 × distance_m`). A needs the first two for the
isometric layout and its sprites; settlement needs the third, and FL4 has no
losses term without it.

None of them belong on `House` — Contract 3 is frozen and no agent reasons over
a coordinate. They are exposed as `domain.Site` / `domain.TransformerSite` via
`feed.sites()`, `feed.transformer_sites()` and `feed.transmission_loss_pct()`.
A reads coordinates off the `scene` payload B assembles from these, never out of
the JSON, so the fixture cannot drift from the engine.

`TransformerSite.registry_kva` deliberately keeps the *installed* rating
(500/250 kVA) visible next to the modelled one, so D1 stays auditable rather
than looking like a file someone quietly edited.

### D12 — The CSVs are not updated, and here is when that would change

Nothing in the dataset is wrong: every join, conservation identity and physical
convention checks out. It is insufficient in places, and insufficiency is closed
in the feed. Editing a generated CSV by hand would break its reproducibility
from its own script, which is the thing that makes provenance answerable in Q&A.

The one legitimate reason to regenerate is **rooftop solar penetration**. P2P
covers about 9.5% of demand at 18 of 60 premises, so the household bill saving
on the compare screen will be modest. `data/scripts/device_registry.py:88`
(`random() < 0.30`) is the lever; 0.45 takes the market to roughly 15%.

Gate that on the hour-24 compare numbers, not on a hunch. It re-rolls all five
telemetry files and invalidates the tuned ratings, breach counts and tariffs
recorded above — budget an hour for the re-tune, and do it once.
