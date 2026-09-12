# Data assets — Whitefield microgrid dataset

Reference data for the P2P energy trading system. No simulator, no agent logic —
just the datasets and the scripts that produced them.

**The dataset is locked.** Everything the engine needs but the data does not
carry — phase, battery power limit, retail tariff, transformer thermal
parameters, 30 days instead of one — is synthesised in `engine/feed.py` and
recorded in `DECISIONS.md`. Do not regenerate these files to fix an engine
problem; nothing in them is wrong, and regenerating one re-rolls all five
telemetry files and invalidates every tuned number in `DECISIONS.md`.

**Only `engine/feed.py` reads these files.** Everyone else goes through
`feed.houses()`, `feed.transformers()`, `feed.ticks(block)` and `feed.sites()`.
A second parser means two interpretations of the same number and a bug that
takes three people to find.

## Folder structure

```
data/
├── bangalore_real_reference_2024.csv     ← 8,760 hourly rows, locality-level aggregate (full year)
├── reference/
│   ├── bangalore_reference_constants.json ← tariffs, solar specs, demand curves
│   ├── device_registry.json               ← per-meter static metadata + location + equipment
│   └── transformer_registry.json          ← substation + 4 transformer locations
├── telemetry/                             ← per-device hourly readings, one representative day
│   ├── solar_inverters.csv                ← 18 meters with rooftop solar × 24h = 432 rows
│   ├── home_batteries.csv                 ← 8 meters with battery × 24h = 192 rows
│   ├── ev_charging_hubs.csv               ← 4 shared community hubs × 24h = 96 rows
│   ├── smart_meters.csv                   ← all 60 meters × 24h = 1,440 rows
│   └── weather.csv                        ← locality-wide, 24 rows
└── scripts/
    ├── fetch_real_data.py                 ← regenerates the aggregate hourly CSV
    ├── device_registry.py                 ← regenerates the meter/location/equipment registry
    └── generate_device_telemetry.py       ← regenerates the 5 per-device telemetry CSVs
```

**Two layers of data, on purpose:**
- `bangalore_real_reference_2024.csv` = locality-wide aggregate, full year, for demand/supply/price trend charts
- `telemetry/*.csv` = per-device readings for one representative day, matching the exact 5-source attribute spec (solar inverters, batteries, EV hubs, smart meters, weather)

Every telemetry row's `meter_id` joins back to `reference/device_registry.json` for location, transformer assignment, and transmission loss.

## 1. `bangalore_real_reference_2024.csv`

8,760 hourly rows (365 days × 24 hours) for Bangalore, built from real monthly
solar/weather/tariff averages. Columns:

| Column | Description |
|---|---|
| `timestamp`, `month`, `hour`, `weekday`, `day_type` | Time index |
| `irradiance_w_m2`, `cloud_cover_pct` | Solar input (NASA POWER-derived) |
| `solar_generation_kw` | Computed PV output for the reference system |
| `temperature_c`, `wind_speed_m_s` | Weather (Open-Meteo-derived) |
| `home_demand_kw`, `ev_demand_kw`, `total_demand_kw` | Demand (BESCOM-derived) |
| `balance_kw`, `status` | Surplus/deficit and SURPLUS/DEFICIT/BALANCED flag |
| `grid_price_inr_per_kwh` | KERC Time-of-Day rate for that hour **plus market noise** (σ ≈ ₹0.30, floored at ₹2.50) — not the published tariff verbatim |
| `p2p_sell_price_inr`, `p2p_buy_price_inr` | Derived P2P settlement prices |
| `bescom_feed_in_inr` | Fixed feed-in rate (₹2.25/kWh) |

## 2. `reference/bangalore_reference_constants.json`

Static lookup values: monthly solar irradiance, monthly temperature/cloud/wind,
hourly demand profile, BESCOM tariff slabs, KERC Time-of-Day rates, KERC P2P
wheeling/transaction charges, feed-in tariff, rooftop solar specs.

## 3. `reference/device_registry.json` — **includes location**

One entry per meter (60 total). This is the static device metadata — set once
at install time, looked up by `meter_id` rather than re-sent with every live
reading.

```json
{
  "meter_id": "10000",
  "building_name": "DT-1-B01",
  "install_date": "2025-03-15",
  "location": {
    "lat": 12.972994,
    "lon": 77.744470,
    "address": "Plot 1, ITPL Commercial Hub Layout, Whitefield, Bengaluru 560066"
  },
  "network": {
    "transformer_id": "DT-1",
    "feeder_id": "F1",
    "substation": "Whitefield 66/11 kV",
    "distance_from_dt_m": 125.0,
    "distance_from_substation_m": 696.6
  },
  "trading": {
    "transmission_loss_pct": 5.5,
    "eligible_trade_partners": "same transformer_id only (per KERC P2P physical constraint)"
  },
  "equipment": {
    "has_rooftop_solar": false,
    "solar_capacity_kw": 0,
    "has_battery": false,
    "battery_capacity_kwh": 0,
    "has_home_ev_charger": true
  },
  "meter_type": "smart_meter_dlms_cosem",
  "meter_make": "Genus"
}
```

**Why location matters here:** `transmission_loss_pct` is derived from
`distance_from_dt_m` using a KERC-consistent distance-based model — exactly
`3 + 0.02 × distance_m`, giving 3.25–6.75% across the fleet. Any P2P settlement
calculation should pull this per-meter figure rather than using a flat
percentage, and invariant FL4 ("generation equals consumption plus net battery
change plus losses") has no losses term without it. Reach it through
`feed.transmission_loss_pct(house_id)`, not by parsing this file.

**`eligible_trade_partners`** encodes the physical constraint that P2P trades
can only happen between meters on the same `transformer_id` — electricity
cannot be routed meter-to-meter across transformers.

**`equipment`** tells you which telemetry files to expect a row for — only
meters with `has_rooftop_solar: true` appear in `solar_inverters.csv`, and so on.
Fleet-wide: 18 of 60 meters have solar, 8 have a battery, 10 have a home EV charger.

## 4. `reference/transformer_registry.json` — **includes location**

Location + rating for the substation and all 4 distribution transformers.

> **`kva_rating` here is not what the engine models.** These are the ratings as
> installed (500/250/250/250 kVA). Measured against the telemetry they put peak
> loading at 21–29%, so no transformer ever breaches and the sentinel, flow
> agent and ageing signal never fire. The engine overrides them to 125/63/63/63
> kVA — standard Indian LT sizing for 12–17 premises per DT — in
> `engine/config.py:rating_kva`. Both numbers stay visible: `feed.transformers()`
> gives the modelled rating, `feed.transformer_sites()` gives the installed one.
> Reasoning and measured loadings in `DECISIONS.md` D1.

```json
{
  "substation": {
    "id": "SUB-WHITEFIELD",
    "name": "Whitefield 66/11 kV",
    "location": { "lat": 12.9698, "lon": 77.7500 },
    "capacity_mva": 5
  },
  "transformers": [
    {
      "transformer_id": "DT-1",
      "name": "ITPL Commercial Hub",
      "kva_rating": 500,
      "location": { "lat": 12.9735, "lon": 77.7455 },
      "feeder_id": "F1",
      "distance_from_substation_m": 638.0
    }
    // ... DT-2, DT-3, DT-4
  ]
}
```

## 5. `telemetry/` — per-device readings, matching the 5-source attribute spec

**`solar_inverters.csv`** — one row per solar-equipped meter per hour:
`meter_id, timestamp, instantaneous_power_kw, voltage_v, current_a, temperature_c, daily_energy_kwh, efficiency_pct, status, irradiance_estimate_w_m2`

**`home_batteries.csv`** — one row per battery-equipped meter per hour:
`meter_id, timestamp, soc_pct, power_kw, direction, available_energy_kwh, temperature_c, health_pct`
(`power_kw` is signed: negative = charging, positive = discharging; the data ranges ±5 kW, which is what `config.battery_max_kw` uses)

**`ev_charging_hubs.csv`** — 4 shared community charging hubs (one per transformer), not per-home:
`hub_id, transformer_id, timestamp, power_draw_kw, vehicles_charging, scheduled_end_time, status, flexible_charging_window`

**`smart_meters.csv`** — every one of the 60 meters, every hour (1,440 rows):
`meter_id, timestamp, current_consumption_kw, daily_import_kwh, daily_export_kwh, voltage_rms_v, power_factor, grid_connection_status`

**`weather.csv`** — locality-wide, not per-meter, 24 rows:
`timestamp, cloud_cover_pct, solar_irradiance_w_m2, temperature_c, wind_speed_m_s, precipitation_probability_pct`

All telemetry covers **Sat 12 Sep 2025**, one representative day, hourly resolution.
To extend to a full year, edit `scripts/generate_device_telemetry.py`'s `HOURLY`
list to loop over `bangalore_real_reference_2024.csv` instead of the hardcoded
single-day profile.

## Data sources

**These CSVs are physically modelled, not measured.** No row here was fetched
from an API. `fetch_real_data.py` builds the year from published monthly
averages plus seeded Gaussian noise (`random.seed(42)`); its `get_*_url()`
functions only print the API URLs you would use to replace the modelled series
with measured ones. Say "physically modelled from published averages" in the
pitch — no open Indian dataset pairs household load with rooftop PV at meter
level, and naming that limit reads as rigour rather than overclaiming.

| Data | Basis | Modelled? |
|---|---|---|
| Solar irradiance | Monthly GHI averages for Bangalore, [NASA POWER](https://power.larc.nasa.gov/) | Yes — monthly average × hourly profile × noise |
| Weather | Monthly normals, [Open-Meteo](https://open-meteo.com/) | Yes — monthly normal + hourly offset + noise |
| Demand profile | BESCOM consumption via [opencity.in](https://data.opencity.in/organization/bangalore-electricity-supply-company-limited) | Yes — normalised 24h profile × per-premises scale |
| Tariffs | KERC Combined Tariff Order 2025 | No — published values, used verbatim |
| P2P charges | KERC P2P Solar Energy Transaction Regulations 2024 | No — published values, used verbatim |
| Device registry, locations | — | Yes — synthetic, seeded (`random.seed(7)`) |

## Regenerating the data

```bash
pip install requests pandas
python scripts/fetch_real_data.py             # rebuilds the aggregate hourly CSV + constants JSON
python scripts/device_registry.py             # rebuilds device_registry.json + transformer_registry.json
python scripts/generate_device_telemetry.py   # rebuilds the 5 telemetry CSVs (run this last — it reads device_registry.json)
```
