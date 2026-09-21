---
name: create-chart
description: >
  Generates Apache ECharts option JSON schemas for UrjaSetu telemetry.
  Use when the user wants to chart curtailment, state-of-charge (SoC), loading
  fraction, or clearing prices from UrjaSetu simulation data.
---

# create-chart

Generates a complete, valid Apache ECharts `option` object for UrjaSetu
telemetry metrics. The output is a self-contained JSON block the UI can pass
directly to `echarts.init(el).setOption(...)`.

## Step 1 — Identify the metric(s) requested

Determine which metric(s) the user wants to chart. Map them to their canonical
source fields:

| Metric | Source field | Unit |
|--------|-------------|------|
| Curtailment fraction | `run_summary.curtailment_fraction` per block | fraction 0–1 |
| Battery SoC | `run_summary.battery_soc_kwh` per house per block | kWh |
| Transformer loading (K) | `run_summary.transformer_loading_k` per DT per block | fraction (1.0 = rated) |
| P2P clearing price | `run_summary.clearing_price_inr_per_kwh` per block | ₹/kWh |
| Hot-spot temperature | `run_summary.hotspot_c` per DT per block | °C |
| Life saved (cumulative) | `run_summary.life_saved_hours` (scalar or per-DT) | hours |
| Total trades / kWh traded | `run_summary.total_trades`, `run_summary.total_kwh_traded` | count / kWh |

Data source priority:
1. **REST endpoint** `/api/run-summary` (available when the server is running).
2. **File** `data/run_summary.json` (present after a completed local run).

If neither is available, generate the chart schema with illustrative placeholder
data arrays and add a comment `// replace with real data from /api/run-summary`.

## Step 2 — Fetch or read the data

If the server is running, use the `fetch` MCP tool to retrieve:
```
GET http://localhost:8000/api/run-summary
```

Otherwise use `read_file` on `data/run_summary.json`.

Extract the relevant arrays. If the field is nested per-transformer or per-house,
ask the user which transformer(s) / house(s) to plot, or default to all four DTs
(`DT-1`, `DT-2`, `DT-3`, `DT-4`).

## Step 3 — Choose the right chart type

| Metric | Recommended type | Notes |
|--------|-----------------|-------|
| Loading fraction over 30 days | `line` with `markLine` at K=1.0 | Show all 4 DTs as separate series |
| Clearing price per block | `line` or `bar` | Add `markArea` for evening peak blocks 17–21 |
| Curtailment fraction | `bar` (stacked if per-house) | Highlight blocks > 0.1 |
| Battery SoC | `line` | Show per-house or aggregate |
| Hot-spot temperature | `line` with `markLine` at 95 °C and 110 °C | GC-07/GC-08 thresholds |
| Life saved (cumulative) | `line` or `gauge` | Single series |

## Step 4 — Construct the ECharts option object

Build a complete `option` object. Required sections:

```jsonc
{
  "title": { "text": "<Metric Name>", "subtext": "UrjaSetu — Whitefield, Bengaluru" },
  "tooltip": {
    "trigger": "axis",
    "formatter": "<include unit in label, e.g. '{b}: {c} kWh'>"
  },
  "legend": { "data": ["<series names>"] },
  "xAxis": {
    "type": "category",
    "name": "<Block / Hour / Day>",
    "data": [/* block numbers or timestamps */]
  },
  "yAxis": {
    "type": "value",
    "name": "<unit>",
    "min": 0
  },
  "series": [
    {
      "name": "<series label>",
      "type": "line",   // or "bar"
      "data": [/* values */],
      "smooth": true
    }
  ]
}
```

Rules that must always hold:
- **Axes** must carry a `name` with the correct unit (kWh, ₹/kWh, °C, fraction).
- **Tooltip** must show the unit alongside the value.
- **markLine / markArea** must be added for domain thresholds:
  - Loading: `markLine` at `yAxis = 1.0` (rated), styled red.
  - Hot-spot: `markLine` at `yAxis = 95` (GC-07, orange) and `yAxis = 110` (GC-08, red).
  - Clearing price: `markLine` at `yAxis = 7.00` (GC-04, orange).
  - Evening peak: `markArea` over x-axis blocks 17–21 per day, light-orange background.
- **Colors** should follow UrjaSetu palette: DT-1 `#e05c4b`, DT-2 `#4b8fe0`,
  DT-3 `#e0b94b`, DT-4 `#5cb85c`, neutral `#888`.
- All numeric values must come from the real data fetched in Step 2 — never invent numbers.

## Step 5 — Output the result

Emit the final config in an `echarts` code fence:

````echarts
{
  // complete option object here
}
````

After the fence, include a one-sentence description of what the chart shows and
which UrjaSetu metric it visualises, e.g.:

> "Loading fraction (K) for all four Whitefield DTs across 720 blocks (30 days).
> DT-1, DT-3, and DT-4 exceed K = 1.0 during the 18:00–21:00 evening peak;
> DT-2 remains healthy throughout (see DECISIONS.md D1)."

If multiple charts are requested, repeat Steps 3–5 for each metric, producing one
`echarts` fence per chart.
