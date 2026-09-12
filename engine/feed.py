"""Meter feed over the locked Whitefield dataset.

Owner: B. This is the only module that knows the dataset exists. Everything the
data does not carry is synthesised here, deterministically, so that no agent
module ever invents a number.

The dataset is fixed: 60 metered premises on 4 transformers, one measured day
(Sat 12 Sep 2025) at hourly resolution, plus a full year of locality-level
aggregate. Three gaps between that and the engine spec are closed here:

  * one day of per-meter data vs. 30-day runs  -> the measured day is replayed
    and scaled per calendar day from the year-long aggregate (DECISIONS.md D3)
  * fields the registry has no column for      -> synthesised (DECISIONS.md D6)
  * EV hubs are transformer-level, not metered -> modelled as pseudo-premises

Provenance: the CSVs are physically modelled from published monthly averages
(NASA POWER, BESCOM, KERC), not measured feeds. Say that, do not overclaim.
"""
from __future__ import annotations

import csv
import json
import random
from collections import defaultdict
from datetime import date, timedelta
from functools import cached_property

from engine.config import Config, DEFAULT
from engine.domain import House, MeterTick, Site, Transformer, TransformerSite

BASE_DAY = date(2025, 9, 12)   # the one measured day in telemetry/
PHASES = ("A", "B", "C")


class WhitefieldFeed:
    """Satisfies the MeterFeed protocol (PRD §3.2)."""

    def __init__(self, config: Config = DEFAULT):
        self.config = config
        self.rng = random.Random(config.seed)
        d = config.data_dir
        self._registry = json.loads((d / "reference" / "device_registry.json").read_text())
        self._transformers = json.loads(
            (d / "reference" / "transformer_registry.json").read_text())["transformers"]
        self._constants = json.loads(
            (d / "reference" / "bangalore_reference_constants.json").read_text())
        self._smart = _read_csv(d / "telemetry" / "smart_meters.csv")
        self._solar = _read_csv(d / "telemetry" / "solar_inverters.csv")
        self._hubs = _read_csv(d / "telemetry" / "ev_charging_hubs.csv")
        self._weather = _read_csv(d / "telemetry" / "weather.csv")
        self._year = _read_csv(d / "bangalore_real_reference_2024.csv")
        self._tick_cache: dict[int, list[MeterTick]] = {}
        self._by_house_cache: dict[int, dict[str, MeterTick]] = {}
        self._noise_cache: dict[tuple[str, int], tuple[float, float]] = {}

    # ------------------------------------------------------------ protocol

    def houses(self) -> list[House]:
        return list(self._houses.values())

    def transformers(self) -> list[Transformer]:
        c = self.config
        out = []
        for t in self._transformers:
            tid = t["transformer_id"]
            rating = c.rating_kva.get(tid, float(t["kva_rating"])) * c.derate_factor
            out.append(Transformer(
                transformer_id=tid,
                rating_kva=rating,
                rated_top_oil_rise_c=55.0,
                rated_hotspot_rise_c=80.0,
                rated_life_hours=c.rated_life_hours,
                replacement_cost_inr=c.replacement_cost_inr,
            ))
        return out

    def sites(self) -> dict[str, Site]:
        """Static per-premises metadata: coordinates, building type, loss.

        A builds the isometric layout from these coordinates — they are the real
        Whitefield positions, so the clusters are geographically honest — but A
        reads them off the `scene` payload B publishes, never out of the JSON.
        """
        return dict(self._sites)

    def transformer_sites(self) -> list[TransformerSite]:
        return [TransformerSite(
            transformer_id=t["transformer_id"],
            name=t["name"],
            lat=float(t["location"]["lat"]),
            lon=float(t["location"]["lon"]),
            registry_kva=float(t["kva_rating"]),
            feeder_id=t["feeder_id"],
        ) for t in self._transformers]

    def transmission_loss_pct(self, house_id: str) -> float:
        """Per-premises loss, 3.25-6.75%, derived in the registry from distance
        to the DT as `3 + 0.02 * distance_m`.

        Settlement must use this figure rather than a flat percentage, and FL4
        ("generation equals consumption plus net battery change plus losses")
        has no losses term without it.
        """
        return self._sites[house_id].transmission_loss_pct

    def ticks(self, block: int) -> list[MeterTick]:
        """Memoised: every agent forecasting off this block would otherwise
        rebuild all 64 ticks, once each, every block. MeterTick is frozen, so
        handing out the same list is safe and nobody can mutate it."""
        cached = self._tick_cache.get(block)
        if cached is not None:
            return cached
        ticks = self._build_ticks(block)
        self._tick_cache[block] = ticks
        return ticks

    def _build_ticks(self, block: int) -> list[MeterTick]:
        if not 0 <= block < self.total_blocks():
            raise IndexError(f"block {block} outside 0..{self.total_blocks() - 1}")
        hour = block % self.config.blocks_per_day
        gen_s, load_s = self._day_scale(block // self.config.blocks_per_day)
        h = self.config.block_hours
        ambient = self._ambient[hour]
        out = []
        for hid in self._houses:
            load_kw, gen_kw = self._measured[(hid, hour)]
            out.append(MeterTick(
                block=block,
                house_id=hid,
                load_kwh=round(load_kw * h * load_s, 6),
                gen_kwh=round(gen_kw * h * gen_s, 6),
                ambient_c=ambient,
            ))
        return out

    def tick_for(self, house_id: str, block: int) -> MeterTick:
        """One premises' tick, without scanning the block."""
        return self._by_house(block)[house_id]

    def _by_house(self, block: int) -> dict[str, MeterTick]:
        cached = self._by_house_cache.get(block)
        if cached is None:
            cached = {t.house_id: t for t in self.ticks(block)}
            self._by_house_cache[block] = cached
        return cached

    def forecast(self, house_id: str, block: int, horizon: int) -> list[MeterTick]:
        """The agent's belief, not truth: truth plus noise.

        The noise is derived from (seed, house_id, block) rather than drawn from a
        running RNG stream, which makes `forecast` a pure function: the same
        (house, block) always returns the same belief, no matter how many times
        or in what order it is called.

        That matters more than it looks. With a shared stream, every call
        advanced the RNG, so an agent calling forecast twice got two different
        answers and D1 held only by accident — the moment C's flow agent also
        called forecast, the sequence would shift and two runs of the same config
        would diverge. Purity here makes determinism structural instead of lucky.

        forecast_noise_frac = 0.0 gives perfect foresight — acceptable for tests
        only, and the run summary must flag it (PRD §3.2).
        """
        out = []
        frac = self.config.forecast_noise_frac
        for b in range(block, min(block + horizon, self.total_blocks())):
            truth = self.tick_for(house_id, b)
            if frac == 0.0:
                out.append(truth)
                continue
            load_noise, gen_noise = self._belief_noise(house_id, b)
            out.append(MeterTick(
                block=truth.block,
                house_id=truth.house_id,
                load_kwh=max(0.0, truth.load_kwh * (1 + load_noise * frac)),
                gen_kwh=max(0.0, truth.gen_kwh * (1 + gen_noise * frac)),
                ambient_c=truth.ambient_c,
            ))
        return out

    def _belief_noise(self, house_id: str, block: int) -> tuple[float, float]:
        """Two reproducible standard-normal-ish draws for this (house, block)."""
        cached = self._noise_cache.get((house_id, block))
        if cached is None:
            stream = random.Random(f"{self.config.seed}:{house_id}:{block}")
            cached = (stream.gauss(0, 1), stream.gauss(0, 1))
            self._noise_cache[(house_id, block)] = cached
        return cached

    def total_blocks(self) -> int:
        return self.config.blocks_per_day * self.config.days

    # -------------------------------------------------------- construction

    @cached_property
    def _houses(self) -> dict[str, House]:
        """Registry entries plus, optionally, the 4 shared EV hubs.

        The hubs draw 18-44 kW on their transformer but sit behind no meter in
        the dataset. Modelled as pseudo-premises with no PV and no battery, so
        they load the DT for the sentinel and pay a bill, without being smeared
        across real households' bills.
        """
        houses: dict[str, House] = {}
        phase_seq: dict[str, int] = defaultdict(int)
        for entry in sorted(self._registry, key=lambda e: e["meter_id"]):
            tid = entry["network"]["transformer_id"]
            eq = entry["equipment"]
            houses[entry["meter_id"]] = House(
                house_id=entry["meter_id"],
                transformer_id=tid,
                # Round-robin within each DT, so a balanced street never shows a
                # phase breach purely from how meters were numbered.
                phase=PHASES[phase_seq[tid] % 3],
                distance_m=float(entry["network"]["distance_from_dt_m"]),
                has_pv=bool(eq["has_rooftop_solar"]),
                pv_kw=float(eq["solar_capacity_kw"]),
                has_battery=bool(eq["has_battery"]),
                battery_kwh=float(eq["battery_capacity_kwh"]),
                battery_max_kw=self.config.battery_max_kw if eq["has_battery"] else 0.0,
                retail_tariff=self._slab_tariff(entry["meter_id"]),
            )
            phase_seq[tid] += 1
        if self.config.include_ev_hubs_as_houses:
            for hub in sorted({r["hub_id"]: r for r in self._hubs}.values(),
                              key=lambda r: r["hub_id"]):
                houses[hub["hub_id"]] = House(
                    house_id=hub["hub_id"],
                    transformer_id=hub["transformer_id"],
                    phase="A",  # three-phase load; carried on A by convention
                    distance_m=80.0,
                    has_pv=False, pv_kw=0.0,
                    has_battery=False, battery_kwh=0.0, battery_max_kw=0.0,
                    retail_tariff=self._constants["tariffs"]["bescom_slabs_inr"]["201+"],
                )
        return houses

    @cached_property
    def _sites(self) -> dict[str, Site]:
        sites: dict[str, Site] = {}
        for entry in self._registry:
            sites[entry["meter_id"]] = Site(
                house_id=entry["meter_id"],
                transformer_id=entry["network"]["transformer_id"],
                lat=float(entry["location"]["lat"]),
                lon=float(entry["location"]["lon"]),
                building_type=entry["building_type"],
                transmission_loss_pct=float(entry["trading"]["transmission_loss_pct"]),
            )
        if self.config.include_ev_hubs_as_houses:
            dt = {t["transformer_id"]: t for t in self._transformers}
            for hub_id, tid in sorted({r["hub_id"]: r["transformer_id"] for r in self._hubs}.items()):
                # The hubs sit at their transformer; loss follows the registry's
                # own relation, 3 + 0.02 * distance_m, at the 80 m we model.
                sites[hub_id] = Site(
                    house_id=hub_id,
                    transformer_id=tid,
                    lat=float(dt[tid]["location"]["lat"]),
                    lon=float(dt[tid]["location"]["lon"]),
                    building_type="evhub",
                    transmission_loss_pct=round(3 + 0.02 * 80.0, 2),
                )
        return sites

    @cached_property
    def _measured(self) -> dict[tuple[str, int], tuple[float, float]]:
        """(house_id, hour) -> (load_kw, gen_kw) for the one measured day.

        smart_meters.csv carries GROSS consumption — a solar premises at noon
        shows consumption and generation and export simultaneously — so load and
        gen map straight onto MeterTick without de-netting.
        """
        gen: dict[tuple[str, int], float] = {}
        for r in self._solar:
            gen[(r["meter_id"], _hour(r["timestamp"]))] = float(r["instantaneous_power_kw"])
        out: dict[tuple[str, int], tuple[float, float]] = {}
        for r in self._smart:
            key = (r["meter_id"], _hour(r["timestamp"]))
            out[key] = (float(r["current_consumption_kw"]), gen.get(key, 0.0))
        if self.config.include_ev_hubs_as_houses:
            for r in self._hubs:
                out[(r["hub_id"], _hour(r["timestamp"]))] = (float(r["power_draw_kw"]), 0.0)
        return out

    @cached_property
    def _ambient(self) -> dict[int, float]:
        return {_hour(r["timestamp"]): float(r["temperature_c"]) for r in self._weather}

    @cached_property
    def _daily_import_kwh(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for r in self._smart:
            out[r["meter_id"]] = max(out.get(r["meter_id"], 0.0), float(r["daily_import_kwh"]))
        return out

    def _slab_tariff(self, meter_id: str) -> float:
        """Effective INR/kWh under BESCOM's stepped slabs.

        The slabs are marginal bands on monthly consumption, not a single rate
        picked by band: a premises consuming 300 units pays 4.10 on the first
        50, 5.55 on the next 50, and so on. Billing the whole draw at the top
        band would put every premises in the dataset on 8.40 and erase the
        difference between a small flat and the commercial hub. The dataset
        gives one day, so monthly consumption is that day x 30.
        """
        slabs = self._constants["tariffs"]["bescom_slabs_inr"]
        bands = [(50, slabs["0-50"]), (50, slabs["51-100"]),
                 (100, slabs["101-200"]), (float("inf"), slabs["201+"])]
        units = self._daily_import_kwh.get(meter_id, 0.0) * 30
        if units <= 0:
            return slabs["0-50"]
        remaining, bill = units, 0.0
        for width, rate in bands:
            take = min(remaining, width)
            bill += take * rate
            remaining -= take
            if remaining <= 0:
                break
        return round(bill / units, 2)

    # ------------------------------------------------------- day scaling

    @cached_property
    def _year_by_day(self) -> dict[tuple[int, int], tuple[float, float]]:
        """(month, day) -> (irradiance sum, demand sum) from the year aggregate."""
        acc: dict[tuple[int, int], list[float]] = defaultdict(lambda: [0.0, 0.0])
        for r in self._year:
            ts = r["timestamp"]
            key = (int(ts[5:7]), int(ts[8:10]))
            acc[key][0] += float(r["irradiance_w_m2"])
            acc[key][1] += float(r["total_demand_kw"])
        return {k: (v[0], v[1]) for k, v in acc.items()}

    def _day_scale(self, day_index: int) -> tuple[float, float]:
        """Generation and load multipliers for simulated day N.

        The measured day is one Saturday in September. Replaying it 30 times
        unchanged would make every day identical and the ageing comparison
        meaningless, so each day is scaled by that calendar day's irradiance and
        demand in the year-long aggregate, relative to 12 Sep. Cloudy days come
        out cloudy; the weekly and seasonal shape survives.
        """
        base = self._year_by_day[(BASE_DAY.month, BASE_DAY.day)]
        d = BASE_DAY + timedelta(days=day_index)
        cur = self._year_by_day.get((d.month, d.day), base)
        gen = cur[0] / base[0] if base[0] else 1.0
        load = cur[1] / base[1] if base[1] else 1.0
        return (_clamp(gen, 0.35, 1.45), _clamp(load, 0.85, 1.15))


def _read_csv(path) -> list[dict]:
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def _hour(timestamp: str) -> int:
    return int(timestamp[11:13])


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))
