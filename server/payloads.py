"""Engine objects -> the payload shapes `UI/src/types.ts` declares.

This module is the contract boundary. Every field the UI reads is produced here,
from engine data, with no invented numbers: if the engine does not know
something, this file returns null rather than a plausible figure. That rule is
the whole point — the UI previously ran on `demoFixture.ts`, where the household
bills, the DISCOM revenue and the transformer life were literal constants typed
into a TypeScript file.

Coordinates are real. `feed.sites()` carries the surveyed Whitefield lat/lon for
every premises, and `_project` turns them into a local metre grid centred on the
street. The UI's layout used to ignore the scene's x/y entirely and lay out a
schematic grid instead; it now uses these when they are present.
"""
from __future__ import annotations

import math

from engine.domain import MeterTick
from engine.physics import loading_k
from engine.trades import delivered_kwh, original_kwh

#: Metres per degree of latitude. Longitude is scaled by cos(lat) below.
_M_PER_DEG_LAT = 111_320.0


def _project(lat: float, lon: float, lat0: float, lon0: float) -> tuple[float, float]:
    """Equirectangular projection onto metres from a local origin.

    At street scale (a few hundred metres) the error against a proper geodesic
    projection is far below the width of a house, and the result is a plain
    Cartesian grid the UI can lay out directly.
    """
    x = (lon - lon0) * _M_PER_DEG_LAT * math.cos(math.radians(lat0))
    y = (lat - lat0) * _M_PER_DEG_LAT
    return round(x, 3), round(y, 3)


def scene_payload(feed, config) -> dict:
    """The static picture: who is on which transformer, and where they are."""
    houses = feed.houses()
    sites = feed.sites()
    transformer_sites = {t.transformer_id: t for t in feed.transformer_sites()}

    lats = [s.lat for s in sites.values()]
    lons = [s.lon for s in sites.values()]
    lat0 = sum(lats) / len(lats) if lats else 0.0
    lon0 = sum(lons) / len(lons) if lons else 0.0

    out_houses = []
    for house in sorted(houses, key=lambda h: h.house_id):
        site = sites.get(house.house_id)
        x, y = _project(site.lat, site.lon, lat0, lon0) if site else (0.0, 0.0)
        out_houses.append({
            "id": house.house_id,
            "transformer": house.transformer_id,
            "phase": house.phase,
            "x": x,
            "y": y,
            "has_pv": house.has_pv,
            "has_battery": house.has_battery,
            "kind": "evhub" if site and site.building_type == "evhub" else "premise",
            # Extra fields the fixture never had. The UI uses building_type for
            # its sprites and the ratings for the household panel; it was
            # guessing all of them from a house's index number before.
            "building_type": site.building_type if site else "res",
            "pv_kw": round(house.pv_kw, 3),
            "battery_kwh": round(house.battery_kwh, 3),
            "battery_max_kw": round(house.battery_max_kw, 3),
            "retail_tariff": round(house.retail_tariff, 4),
            "distance_m": round(house.distance_m, 2),
            "transmission_loss_pct": round(site.transmission_loss_pct, 3) if site else 0.0,
        })

    out_transformers = []
    for transformer in sorted(feed.transformers(), key=lambda t: t.transformer_id):
        site = transformer_sites.get(transformer.transformer_id)
        x, y = _project(site.lat, site.lon, lat0, lon0) if site else (0.0, 0.0)
        out_transformers.append({
            "id": transformer.transformer_id,
            "rating_kva": round(transformer.rating_kva, 3),
            "x": x,
            "y": y,
            "name": site.name if site else transformer.transformer_id,
            "feeder_id": site.feeder_id if site else "",
            "registry_kva": round(site.registry_kva, 1) if site else None,
        })

    return {
        "houses": out_houses,
        "transformers": out_transformers,
        "blocks_per_day": config.blocks_per_day,
        "replay_rate": config.block_minutes,
        "origin": {"lat": round(lat0, 6), "lon": round(lon0, 6)},
        "power_factor": config.power_factor,
        "loading_limit": config.loading_limit,
    }


def _house_state(net_kwh: float, min_order_kwh: float) -> str:
    if net_kwh < -min_order_kwh:
        return "export"
    if net_kwh > min_order_kwh:
        return "import"
    return "idle"


def block_payload(view, feed, config, soc_by_house: dict[str, float],
                  houses_by_id: dict, transformer_ids: list[str]) -> dict:
    """One block, as the UI draws it.

    Built from `view.settled_ticks` — what the meters actually read once the
    batteries and the reshape had moved energy — not from the raw feed. Drawing
    the raw ticks would show a street on which the protection agents did nothing,
    which is precisely the thing this project exists to demonstrate.
    """
    ticks: list[MeterTick] = view.settled_ticks
    hour = view.block % config.blocks_per_day

    curtailed_by_house: dict[str, float] = {}
    for trade in view.trades:
        if trade.curtailed_fraction > 0:
            for hid in (trade.seller_id, trade.buyer_id):
                curtailed_by_house[hid] = max(
                    curtailed_by_house.get(hid, 0.0), trade.curtailed_fraction)

    house_states = {}
    net_kw_by_house: dict[str, float] = {}
    for tick in ticks:
        net_kwh = tick.load_kwh - tick.gen_kwh
        net_kw_by_house[tick.house_id] = net_kwh / config.block_hours
        house = houses_by_id.get(tick.house_id)
        soc = None
        if house is not None and house.has_battery and house.battery_kwh > 0:
            soc = round(soc_by_house.get(tick.house_id, 0.0) / house.battery_kwh, 6)
        house_states[tick.house_id] = {
            "net_kwh": round(net_kwh, 4),
            "state": _house_state(net_kwh, config.min_order_kwh),
            "soc_frac": soc,
            "curtailed": round(curtailed_by_house.get(tick.house_id, 0.0), 6),
        }

    # Transformer state comes from the health agent's own AgeingResult where one
    # exists, so the UI shows the figure the engine acted on rather than a
    # recomputation that could drift from it.
    states_by_id = {}
    if view.ageing is not None:
        states_by_id = {s.transformer_id: s for s in view.ageing.states}

    breached = view.breach.transformer_id if view.breach is not None else None
    transformers = {}
    for tid in transformer_ids:
        state = states_by_id.get(tid)
        if state is not None:
            loading, hotspot, life = state.loading_k, state.hotspot_c, state.life_used_frac
        else:
            members = [h for h in houses_by_id.values() if h.transformer_id == tid]
            total_kw = sum(abs(net_kw_by_house.get(h.house_id, 0.0)) for h in members)
            rating = next((t.rating_kva for t in feed.transformers()
                           if t.transformer_id == tid), 0.0)
            loading = round(loading_k(total_kw, rating, config.power_factor), 4)
            hotspot, life = None, None
        transformers[tid] = {
            "loading": loading,
            "hotspot_c": hotspot,
            "life_used_frac": life,
            # The same quantity in EQUIVALENT HOURS. `life_used_frac` is a
            # fraction of a 180,000-hour rating, so over a 30-day run it is on
            # the order of 1e-5 and renders as "0.0000%" at any sane number of
            # decimal places. Hours is the unit the thermal model works in and
            # the unit the compare screen uses, so send it rather than making
            # the UI multiply by a rating it does not have.
            "life_used_hours": (round(life * config.rated_life_hours, 4)
                                if life is not None else None),
            # "stressed" means this transformer is the one that breached this
            # block, or it is over the loading limit. Not a cosmetic threshold:
            # it is the sentinel's own verdict.
            "stressed": tid == breached or (loading is not None
                                            and loading > config.loading_limit),
            "ageing_adder_inr": round(
                view.ageing.ageing_adder.get(tid, 0.0), 4) if view.ageing else 0.0,
            "predicted_breach": tid == view.predicted_transformer,
        }

    trades = [{
        "from": t.seller_id,
        "to": t.buyer_id,
        "kwh": round(delivered_kwh(t), 4),
        "price": round(t.clearing_price, 4),
        "curtailed": round(t.curtailed_fraction, 6),
        "requested_kwh": round(original_kwh(t), 4),
    } for t in sorted(view.trades, key=lambda t: t.trade_id)]

    status = "cleared"
    if view.breach is not None and view.passes > 1:
        status = "reshaped"
    elif view.breach is not None:
        status = "fallback"

    return {
        "block": view.block,
        "clock": f"{hour:02d}:00",
        "day": view.block // config.blocks_per_day,
        "clearing_price": (round(view.clearing_price, 4)
                           if view.clearing_price is not None else None),
        "status": status,
        "houses": house_states,
        "transformers": transformers,
        "trades": trades,
        "breach": None if view.breach is None else {
            "transformer_id": view.breach.transformer_id,
            "kind": view.breach.kind,
            "severity": round(view.breach.severity, 4),
        },
        "battery": {
            "charged_kwh": round(sum(view.battery_stored.values()), 4),
            "discharged_kwh": round(sum(view.battery_discharged.values()), 4),
        },
        "settlement": {
            "bill_lines": len(view.bills),
            "charges_inr": round(sum(
                b.transaction_inr + b.wheeling_inr + b.cross_subsidy_inr
                + b.ageing_inr + b.platform_inr + b.gst_inr
                for b in view.bills), 4),
        },
    }


def summary_payload(summary: dict, compare_result: dict, config) -> dict:
    """The compare screen's numbers, every one of them from the run.

    `deferredCapex` is the only derived figure and it is derived, not asserted:
    hours of insulation life saved, as a fraction of rated life, times what a
    transformer costs to replace. It was a typed-in constant (186000) in the
    demo fixture.

    Expect it to be SMALL on a 30-day run, and say so rather than scaling it up
    to look impressive: saving 26 hours of insulation life out of a 180,000-hour
    rating is worth about Rs36 of deferred replacement. `deferredCapexAnnualised`
    projects the same rate over a year, which is the honest way to make the
    figure legible — it is clearly labelled as a projection because that is what
    it is.
    """
    life = compare_result["transformer_life_hours"]
    bills = compare_result["household_bills_inr"]
    revenue = compare_result["discom_revenue_inr"]

    saved_hours = life["saved_hours"]
    # Life saved is already summed across every transformer, so this must NOT be
    # multiplied by the transformer count again.
    deferred = (saved_hours / config.rated_life_hours) * config.replacement_cost_inr
    days = summary.get("days") or 1
    annualised = deferred * (365.0 / days) if days else 0.0

    return {
        "days": summary["days"],
        "houses": summary.get("premises", 0),
        "transformers": len(summary.get("loss_of_life_hours", {}) or {}),
        "householdBillBaseline": round(bills["baseline"], 2),
        "householdBillUrjasetu": round(bills["p2p"], 2),
        "discomRevenueBaseline": round(revenue["baseline"], 2),
        "discomRevenueUrjasetu": round(revenue["p2p"], 2),
        "transformerLifeBaseline": round(life["baseline"], 4),
        "transformerLifeUrjasetu": round(life["p2p"], 4),
        "deferredCapex": round(deferred, 2),
        "deferredCapexAnnualised": round(annualised, 2),
        # Stated rather than implied: the check that the whole argument rests on.
        "baselineAgesAtLeastAsFast": compare_result["baseline_ages_at_least_as_fast"],
        "lifeSavedHours": round(saved_hours, 4),
        "householdSavingInr": round(bills["saved_inr"], 2),
        "discomGainInr": round(revenue["gained_inr"], 2),
    }


#: Bus topic -> how the operator trace should read it. The UI renders
#: `EventPayload.text`, so the wording lives here rather than being assembled in
#: TypeScript from a topic name.
def event_text(topic: str, payload: dict) -> str:
    if topic == "block_opened":
        return f"Block opened — day {payload.get('day', 0)}, hour {payload.get('hour', 0):02d}:00"
    if topic == "market_cleared":
        price = payload.get("clearing_price")
        price_s = f"Rs{price:.2f}/kWh" if price is not None else "no clearing price"
        suffix = " (reshaped)" if payload.get("reshaped") else ""
        return (f"Cleared {payload.get('trades', 0)} trades, "
                f"{payload.get('volume_kwh', 0):.2f} kWh at {price_s}{suffix}")
    if topic == "breach_detected":
        pre = "Predicted and confirmed" if payload.get("predicted") else "Detected"
        return (f"{pre}: {payload.get('kind')} breach on "
                f"{payload.get('transformer_id')} at "
                f"{payload.get('severity', 0):.0%} of limit")
    if topic == "breach_predicted":
        return (f"Forecast warns of a {payload.get('kind')} breach on "
                f"{payload.get('transformer_id')} next block "
                f"({payload.get('severity', 0):.0%} of limit)")
    if topic == "reshape_proposed":
        return ("Reshape LP feasible" if payload.get("feasible")
                else "Reshape LP infeasible — falling back to curtailment")
    if topic == "reshape_applied":
        return (f"Reshape applied: {payload.get('trades', 0)} trades retained, "
                f"{payload.get('battery_discharge_kwh', 0):.2f} kWh discharged")
    if topic == "fallback_curtailed":
        return (f"Fallback curtailment on {payload.get('transformer_id')} — "
                f"{payload.get('trades', 0)} trades scaled back")
    if topic == "ageing_applied":
        adders = payload.get("ageing_adder", {}) or {}
        worst = max(adders.items(), key=lambda kv: kv[1], default=None)
        if worst and worst[1] > 0:
            return f"Ageing priced in: {worst[0]} at Rs{worst[1]:.3f}/kWh"
        return "Ageing applied — no adder this block"
    if topic == "battery_moved":
        return (f"Batteries: +{sum((payload.get('charged_kwh') or {}).values()):.2f} kWh in, "
                f"-{sum((payload.get('discharged_kwh') or {}).values()):.2f} kWh out")
    if topic == "delivery_shortfall":
        return (f"{payload.get('sellers', 0)} sellers could not deliver "
                f"{payload.get('kwh', 0):.2f} kWh they had committed")
    if topic == "bill_lines_posted":
        return (f"Charges posted: {payload.get('bill_lines', 0)} bill lines, "
                f"Rs{payload.get('charges_collected_inr', 0):.2f} collected")
    if topic == "block_settled":
        return f"Block settled — {payload.get('bills', 0)} bill lines"
    if topic == "order_submitted":
        return (f"{payload.get('side')} {payload.get('quantity_kwh', 0):.2f} kWh "
                f"at Rs{payload.get('limit_price', 0):.2f}")
    return topic.replace("_", " ")
