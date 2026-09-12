#!/usr/bin/env python3
"""
DEVICE REGISTRY — Static metadata for every meter/inverter/battery in the microgrid
=====================================================================================
This is SEPARATE from the live telemetry stream. Registry data is set ONCE when a
device is installed and rarely changes. Live sensors only need to transmit a
`meter_id` — everything below is looked up from this registry, not re-sent every
10 seconds.

Why this matters for P2P trading:
  - transformer_id determines who CAN trade with whom (same-DT only, physically)
  - lat/lon + distance_from_dt_m feeds the KERC-mandated transmission loss calc
  - distance-based loss (3-8%) must be subtracted from every P2P settlement
"""

import json
import math
import random

random.seed(7)

# ============================================================
# WHITEFIELD, BENGALURU — real reference point
# ============================================================
# Substation location (approximate real coordinates for Whitefield area)
SUBSTATION_LATLON = (12.9698, 77.7500)  # Whitefield 66/11 kV

# Transformers placed at realistic offsets (~200-500m from substation)
# Real BESCOM DTs are typically 150-400m apart in dense residential layouts
TRANSFORMERS = {
    "DT-1": {"name": "ITPL Commercial Hub", "kva": 500, "latlon": (12.9735, 77.7455), "feeder": "F1"},
    "DT-2": {"name": "Kadugodi Residential", "kva": 250, "latlon": (12.9718, 77.7562), "feeder": "F2"},
    "DT-3": {"name": "Brookefield Apartments", "kva": 250, "latlon": (12.9658, 77.7448), "feeder": "F3"},
    "DT-4": {"name": "Whitefield Central", "kva": 250, "latlon": (12.9660, 77.7555), "feeder": "F4"},
}


def haversine_m(latlon1, latlon2):
    """Distance in metres between two lat/lon points."""
    lat1, lon1 = latlon1
    lat2, lon2 = latlon2
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def jitter_latlon(center, max_offset_m=180):
    """Scatter a point randomly within max_offset_m of a center (for placing houses around a DT)."""
    lat, lon = center
    # ~111,320 m per degree latitude; longitude scales by cos(latitude)
    d_lat = (random.uniform(-1, 1) * max_offset_m) / 111320
    d_lon = (random.uniform(-1, 1) * max_offset_m) / (111320 * math.cos(math.radians(lat)))
    return (round(lat + d_lat, 6), round(lon + d_lon, 6))


def transmission_loss_pct(distance_m):
    """
    KERC / standard LT distribution loss model.
    Real-world LT line losses run ~3-8% depending on distance from transformer,
    conductor gauge, and load — this is a simplified distance-based approximation
    consistent with the range used in KERC P2P settlement guidance.
    """
    base = 3.0
    extra = min(5.0, distance_m / 250 * 5.0)  # scales up to +5% at 250m+
    return round(base + extra, 2)


def build_registry(num_meters=60):
    """Generate the static device registry: one row per meter, fixed at install time."""
    registry = []
    meters_per_dt = {"DT-1": 12, "DT-2": 17, "DT-3": 14, "DT-4": 17}
    building_kind = {"DT-1": "com", "DT-2": "res", "DT-3": "apt", "DT-4": "res"}
    meter_id = 0

    for dt_id, count in meters_per_dt.items():
        dt = TRANSFORMERS[dt_id]
        kind = building_kind[dt_id]
        for i in range(count):
            latlon = jitter_latlon(dt["latlon"], max_offset_m=160)
            dist = round(haversine_m(dt["latlon"], latlon), 1)

            # Equipment assignment — consistent with original locality spec:
            # ~30% of buildings have rooftop solar, a third of those also have a battery,
            # ~10% have a home EV charger. Commercial units get bigger solar systems.
            has_solar = random.random() < 0.30
            has_battery = has_solar and random.random() < 0.33
            has_ev = random.random() < 0.10
            if has_solar:
                solar_kw = {"com": 15, "apt": 8, "res": 3}[kind]
            else:
                solar_kw = 0

            registry.append({
                "meter_id": f"{dt_id[-1]}{meter_id:04d}",          # e.g. "10001"
                "building_name": f"{dt_id}-B{i+1:02d}",
                "building_type": kind,
                "install_date": "2025-03-15",                       # static, set once
                "location": {
                    "lat": latlon[0],
                    "lon": latlon[1],
                    "address": f"Plot {i+1}, {dt['name']} Layout, Whitefield, Bengaluru 560066",
                },
                "network": {
                    "transformer_id": dt_id,
                    "feeder_id": dt["feeder"],
                    "substation": "Whitefield 66/11 kV",
                    "distance_from_dt_m": dist,
                    "distance_from_substation_m": round(haversine_m(SUBSTATION_LATLON, latlon), 1),
                },
                "trading": {
                    "transmission_loss_pct": transmission_loss_pct(dist),
                    "eligible_trade_partners": "same transformer_id only (per KERC P2P physical constraint)",
                },
                "equipment": {
                    "has_rooftop_solar": has_solar,
                    "solar_capacity_kw": solar_kw,
                    "has_battery": has_battery,
                    "battery_capacity_kwh": 10 if has_battery else 0,
                    "has_home_ev_charger": has_ev,
                },
                "meter_type": "smart_meter_dlms_cosem",
                "meter_make": random.choice(["Genus", "Indra", "Schneider", "Landis+Gyr"]),
            })
            meter_id += 1
    return registry


def build_transformer_registry():
    """Static metadata for the 4 transformers + substation."""
    out = {
        "substation": {
            "id": "SUB-WHITEFIELD",
            "name": "Whitefield 66/11 kV",
            "location": {"lat": SUBSTATION_LATLON[0], "lon": SUBSTATION_LATLON[1]},
            "capacity_mva": 5,
        },
        "transformers": []
    }
    for dt_id, dt in TRANSFORMERS.items():
        out["transformers"].append({
            "transformer_id": dt_id,
            "name": dt["name"],
            "kva_rating": dt["kva"],
            "location": {"lat": dt["latlon"][0], "lon": dt["latlon"][1]},
            "feeder_id": dt["feeder"],
            "distance_from_substation_m": round(haversine_m(SUBSTATION_LATLON, dt["latlon"]), 1),
        })
    return out


if __name__ == "__main__":
    registry = build_registry()
    tf_registry = build_transformer_registry()

    with open("/tmp/device_registry.json", "w") as f:
        json.dump(registry, f, indent=2)
    with open("/tmp/transformer_registry.json", "w") as f:
        json.dump(tf_registry, f, indent=2)

    print(f"{'='*70}")
    print("DEVICE REGISTRY — sample entries")
    print(f"{'='*70}\n")
    for r in registry[:2] + registry[29:31]:
        print(json.dumps(r, indent=2))
        print()

    print(f"{'='*70}")
    print("TRANSFORMER REGISTRY")
    print(f"{'='*70}\n")
    print(json.dumps(tf_registry, indent=2))

    print(f"\n{'='*70}")
    print(f"Total meters registered: {len(registry)}")
    print(f"Avg distance from own DT: {sum(r['network']['distance_from_dt_m'] for r in registry)/len(registry):.1f} m")
    print(f"Avg transmission loss: {sum(r['trading']['transmission_loss_pct'] for r in registry)/len(registry):.2f}%")
    print(f"Files written: /tmp/device_registry.json, /tmp/transformer_registry.json")
    print(f"{'='*70}")
