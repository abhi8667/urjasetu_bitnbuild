#!/usr/bin/env python3
"""
DEVICE TELEMETRY GENERATOR
============================================================
Generates one hourly CSV per data source, covering every attribute
in the agreed spec:

  1. Rooftop Solar Inverters  -> solar_inverters.csv
  2. Home Batteries           -> home_batteries.csv
  3. EV Charging Hubs         -> ev_charging_hubs.csv
  4. Smart Meters             -> smart_meters.csv
  5. Weather API              -> weather.csv

Reads reference/device_registry.json to know which meters have which
equipment (solar/battery/EV) and their location — so telemetry rows
join back to location via meter_id.

Single representative day: Sat 12 Sep 2025, 24 hourly rows per device.
Uses the same Sept-2025 Bangalore solar/weather profile as the rest
of the dataset (NASA POWER / Open-Meteo derived).
"""

import json
import csv
import random
import math
from datetime import datetime, timedelta

random.seed(11)

# Real Sept 2025 Bangalore hourly profile (NASA POWER GHI, Open-Meteo weather)
HOURLY = [
    {"h": 0, "ghi": 0, "t": 21.2, "c": 72, "w": 2.1, "rain": 35},
    {"h": 1, "ghi": 0, "t": 20.8, "c": 74, "w": 1.9, "rain": 38},
    {"h": 2, "ghi": 0, "t": 20.5, "c": 75, "w": 1.8, "rain": 40},
    {"h": 3, "ghi": 0, "t": 20.1, "c": 76, "w": 1.7, "rain": 42},
    {"h": 4, "ghi": 0, "t": 19.9, "c": 76, "w": 1.7, "rain": 42},
    {"h": 5, "ghi": 0, "t": 20.0, "c": 74, "w": 1.9, "rain": 38},
    {"h": 6, "ghi": 38, "t": 21.0, "c": 70, "w": 2.4, "rain": 30},
    {"h": 7, "ghi": 142, "t": 22.4, "c": 66, "w": 2.8, "rain": 25},
    {"h": 8, "ghi": 298, "t": 24.1, "c": 61, "w": 3.2, "rain": 20},
    {"h": 9, "ghi": 461, "t": 25.6, "c": 55, "w": 3.6, "rain": 15},
    {"h": 10, "ghi": 598, "t": 26.8, "c": 50, "w": 4.0, "rain": 12},
    {"h": 11, "ghi": 694, "t": 27.5, "c": 48, "w": 4.3, "rain": 10},
    {"h": 12, "ghi": 742, "t": 27.9, "c": 52, "w": 4.5, "rain": 12},
    {"h": 13, "ghi": 698, "t": 28.1, "c": 58, "w": 4.4, "rain": 18},
    {"h": 14, "ghi": 591, "t": 27.8, "c": 66, "w": 4.1, "rain": 28},
    {"h": 15, "ghi": 437, "t": 27.0, "c": 74, "w": 3.7, "rain": 40},
    {"h": 16, "ghi": 271, "t": 25.9, "c": 79, "w": 3.3, "rain": 48},
    {"h": 17, "ghi": 118, "t": 24.7, "c": 78, "w": 2.9, "rain": 45},
    {"h": 18, "ghi": 22, "t": 23.6, "c": 75, "w": 2.5, "rain": 38},
    {"h": 19, "ghi": 0, "t": 22.8, "c": 73, "w": 2.3, "rain": 32},
    {"h": 20, "ghi": 0, "t": 22.3, "c": 72, "w": 2.2, "rain": 30},
    {"h": 21, "ghi": 0, "t": 21.9, "c": 72, "w": 2.1, "rain": 30},
    {"h": 22, "ghi": 0, "t": 21.6, "c": 73, "w": 2.0, "rain": 32},
    {"h": 23, "ghi": 0, "t": 21.4, "c": 73, "w": 2.0, "rain": 34},
]

DEMAND_FRACTION = [.22,.19,.17,.15,.15,.21,.52,.74,.62,.41,.36,.35,.39,.37,.35,.38,.46,.61,.86,1.0,.91,.72,.49,.34]

DATE = datetime(2025, 9, 12)


def load_registry(path="reference/device_registry.json"):
    with open(path) as f:
        return json.load(f)


# ============================================================
# 1. ROOFTOP SOLAR INVERTERS
# ============================================================
def gen_solar_inverters(registry):
    rows = []
    solar_meters = [m for m in registry if m["equipment"]["has_rooftop_solar"]]
    daily_energy = {m["meter_id"]: 0.0 for m in solar_meters}

    for h in HOURLY:
        for m in solar_meters:
            kw = m["equipment"]["solar_capacity_kw"]
            irr = h["ghi"] * (0.85 + random.random() * 0.3)  # per-panel irradiance variance
            power = max(0, (irr / 1000) * kw * 0.78 * (1 - 0.004 * max(0, h["t"] - 25)))
            power = round(power + random.gauss(0, power * 0.03), 2) if power > 0 else 0.0
            voltage = round(230 + random.gauss(0, 3), 1)
            current = round((power * 1000 / voltage) if power > 0 else 0, 1)
            temp = round(h["t"] + (power / max(kw, 1)) * 25 + random.gauss(0, 1.5), 1)  # panel heats above ambient when producing
            daily_energy[m["meter_id"]] += power
            efficiency = round(random.uniform(95, 98), 1)
            status = "Overtemp" if temp > 58 else ("Running" if power > 0.1 else "Disconnected")

            rows.append({
                "meter_id": m["meter_id"],
                "timestamp": (DATE + timedelta(hours=h["h"])).isoformat(),
                "instantaneous_power_kw": power,
                "voltage_v": voltage,
                "current_a": current,
                "temperature_c": temp,
                "daily_energy_kwh": round(daily_energy[m["meter_id"]], 2),
                "efficiency_pct": efficiency,
                "status": status,
                "irradiance_estimate_w_m2": round(irr, 1),
            })
    return rows


# ============================================================
# 2. HOME BATTERIES
# ============================================================
def gen_home_batteries(registry):
    rows = []
    batt_meters = [m for m in registry if m["equipment"]["has_battery"]]
    soc = {m["meter_id"]: random.uniform(35, 55) for m in batt_meters}  # start of day

    for h in HOURLY:
        for m in batt_meters:
            cap = m["equipment"]["battery_capacity_kwh"]
            # Charge 09-14h (cheap solar ToD), discharge 18-21h (peak ToD), else idle
            if 9 <= h["h"] <= 14 and soc[m["meter_id"]] < 95:
                power = -round(random.uniform(3.0, 5.0), 2)  # negative = charging
                direction = "Charging"
            elif 18 <= h["h"] <= 21 and soc[m["meter_id"]] > 20:
                power = round(random.uniform(3.0, 5.0), 2)  # positive = discharging
                direction = "Discharging"
            else:
                power = 0.0
                direction = "Idle"

            soc[m["meter_id"]] = min(100, max(10, soc[m["meter_id"]] - (power / cap) * 100))
            available = round((soc[m["meter_id"]] / 100) * cap, 2)
            temp = round(h["t"] + abs(power) * 1.2 + random.gauss(0, 1), 1)
            health = round(random.uniform(96, 100), 1)  # slow degradation, near-new fleet

            rows.append({
                "meter_id": m["meter_id"],
                "timestamp": (DATE + timedelta(hours=h["h"])).isoformat(),
                "soc_pct": round(soc[m["meter_id"]], 1),
                "power_kw": power,
                "direction": direction,
                "available_energy_kwh": available,
                "temperature_c": temp,
                "health_pct": health,
            })
    return rows


# ============================================================
# 3. EV CHARGING HUBS (shared community chargers, one per zone)
# ============================================================
def gen_ev_charging_hubs():
    rows = []
    hubs = [
        {"hub_id": "EVHUB-DT1", "transformer_id": "DT-1", "chargers": 2},
        {"hub_id": "EVHUB-DT2", "transformer_id": "DT-2", "chargers": 1},
        {"hub_id": "EVHUB-DT3", "transformer_id": "DT-3", "chargers": 1},
        {"hub_id": "EVHUB-DT4", "transformer_id": "DT-4", "chargers": 2},
    ]
    for h in HOURLY:
        # more vehicles charging in morning departure + evening return windows
        if 7 <= h["h"] <= 9 or (18 <= h["h"] <= 22):
            base_occupancy = 0.6
        elif 0 <= h["h"] <= 5:
            base_occupancy = 0.3  # overnight slow charge
        else:
            base_occupancy = 0.15

        for hub in hubs:
            vehicles = min(hub["chargers"], round(hub["chargers"] * base_occupancy + random.random()))
            power = round(vehicles * random.uniform(6, 22) if vehicles else 0, 2)
            status = "Fault" if random.random() < 0.01 else ("Charging" if vehicles > 0 else ("Waiting" if base_occupancy > 0.3 else "Idle"))
            scheduled_end = None
            if status == "Charging":
                scheduled_end = (DATE + timedelta(hours=h["h"], minutes=random.randint(20, 90))).isoformat()

            rows.append({
                "hub_id": hub["hub_id"],
                "transformer_id": hub["transformer_id"],
                "timestamp": (DATE + timedelta(hours=h["h"])).isoformat(),
                "power_draw_kw": power,
                "vehicles_charging": vehicles,
                "scheduled_end_time": scheduled_end or "",
                "status": status,
                "flexible_charging_window": (h["h"] < 22 and h["h"] >= 18),
            })
    return rows


# ============================================================
# 4. SMART METERS (every building, every hour)
# ============================================================
def gen_smart_meters(registry):
    rows = []
    daily_import = {m["meter_id"]: 0.0 for m in registry}
    daily_export = {m["meter_id"]: 0.0 for m in registry}

    base_load = {"com": 7.5, "apt": 3.8, "res": 1.9}

    for h in HOURLY:
        df = DEMAND_FRACTION[h["h"]]
        for m in registry:
            load = base_load[m["building_type"]] * df * (0.85 + random.random() * 0.3)
            gen = 0.0
            if m["equipment"]["has_rooftop_solar"]:
                kw = m["equipment"]["solar_capacity_kw"]
                gen = max(0, (h["ghi"] / 1000) * kw * 0.78 * (1 - 0.004 * max(0, h["t"] - 25)))
            net = gen - load
            if net < 0:
                daily_import[m["meter_id"]] += -net
            else:
                daily_export[m["meter_id"]] += net

            voltage_rms = round(230 + random.gauss(0, 3.5), 1)
            power_factor = round(random.uniform(0.85, 1.0), 2)
            connection_status = "Disconnected" if random.random() < 0.002 else "Connected"

            rows.append({
                "meter_id": m["meter_id"],
                "timestamp": (DATE + timedelta(hours=h["h"])).isoformat(),
                "current_consumption_kw": round(load, 2),
                "daily_import_kwh": round(daily_import[m["meter_id"]], 2),
                "daily_export_kwh": round(daily_export[m["meter_id"]], 2),
                "voltage_rms_v": voltage_rms,
                "power_factor": power_factor,
                "grid_connection_status": connection_status,
            })
    return rows


# ============================================================
# 5. WEATHER API (locality-wide, one record per hour)
# ============================================================
def gen_weather():
    rows = []
    for h in HOURLY:
        rows.append({
            "timestamp": (DATE + timedelta(hours=h["h"])).isoformat(),
            "cloud_cover_pct": h["c"],
            "solar_irradiance_w_m2": h["ghi"],
            "temperature_c": h["t"],
            "wind_speed_m_s": h["w"],
            "precipitation_probability_pct": h["rain"],
        })
    return rows


def write_csv(rows, filename):
    if not rows:
        return
    with open(filename, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"  {filename}  ({len(rows)} rows)")


if __name__ == "__main__":
    print("Loading device registry...")
    registry = load_registry()
    print(f"  {len(registry)} meters loaded")
    print(f"  {sum(1 for m in registry if m['equipment']['has_rooftop_solar'])} with rooftop solar")
    print(f"  {sum(1 for m in registry if m['equipment']['has_battery'])} with battery")
    print(f"  {sum(1 for m in registry if m['equipment']['has_home_ev_charger'])} with home EV charger")

    print("\nGenerating telemetry (24 hourly rows per device, Sat 12 Sep 2025)...\n")
    write_csv(gen_solar_inverters(registry), "telemetry/solar_inverters.csv")
    write_csv(gen_home_batteries(registry), "telemetry/home_batteries.csv")
    write_csv(gen_ev_charging_hubs(), "telemetry/ev_charging_hubs.csv")
    write_csv(gen_smart_meters(registry), "telemetry/smart_meters.csv")
    write_csv(gen_weather(), "telemetry/weather.csv")

    print("\nDone. Join any telemetry file back to reference/device_registry.json on meter_id")
    print("to get location, transformer assignment, and transmission loss.")
