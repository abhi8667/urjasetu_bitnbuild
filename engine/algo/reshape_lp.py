"""Constraint-feasible trade reshaping. Target hour 20 — the critical path."""
from __future__ import annotations


from engine.domain import ReshapeSolution, Trade
from engine.algo.powerflow import R_OHM_PER_M, X_OHM_PER_M, V_NOM_V, POWER_FACTOR
import math

# These were wrapped in a `_cfg_get` that read attributes off the engine.config
# MODULE — names that have never existed there — so every lookup fell through to
# its default and the override the helper advertised was not reachable. The
# values that matter (block_hours, loading_limit, voltage_band) are read per-call
# off the limits object the flow agent passes; the rest are plain constants,
# which is what they always were in fact.
BLOCK_HOURS = 1.0            # hourly blocks (DECISIONS.md D2)
LOADING_LIMIT = 0.90         # fraction of rated kVA, if a caller passes none
STORAGE_FEE = 0.05           # INR/kWh — cost of using the battery as a lever
DISCHARGE_FEE = 0.05         # so the LP discharges only when a constraint needs it
VOLTAGE_BAND = 0.05          # +/- per-unit, matches powerflow's per-unit output
_Q_FACTOR = math.tan(math.acos(POWER_FACTOR))


# ----------------------------------------------------------------------
# ASSUMED interfaces — the one thing to confirm with your team.
#
# The given signature solve(trades, limits, batteries, topology) has no
# explicit "current net_kw per house" argument, but the loading and voltage
# constraints cannot be built without it (that's what tells us we're at
# 112% before curtailing anything). This assumes:
#
#   limits[transformer_id] -> object/dict with:
#       .rating_kva     rated kVA
#       .baseline_kw    dict[house_id, float]: each house's net_kw (import
#                       positive, export negative — same convention as
#                       powerflow.py) ASSUMING every trade is fully retained
#                       (c_t=1) and no battery is charging. This is exactly
#                       the figure the sentinel/flow agent already computed
#                       before calling us — the "112%" comes from summing
#                       these.
#       .loading_limit  optional per-transformer override of LOADING_LIMIT
#
#   batteries[house_id] -> current state of charge in kWh (soc_h), for every
#       house with a battery this block. Static battery_kwh capacity and
#       battery_max_kw power limit come from topology[house_id] (House).
#
#   topology[house_id] -> House (transformer_id, distance_m, battery_kwh,
#       battery_max_kw, ...) — same object powerflow.py expects.
#
# If the real shapes differ, only _house_kw_expr() and the two field
# look-ups inside solve() need adjusting — the LP itself (objective,
# bounds, infeasibility handling, the two-pass battery extension, the
# invariants) does not change.
# ----------------------------------------------------------------------


def _get(obj, name, default=None):
    """Attribute access if obj is an object, key access if it's a dict."""
    if obj is None:
        return default
    return getattr(obj, name, default) if not isinstance(obj, dict) else obj.get(name, default)


def _house_kw_expr(house_id, trades, battery_house_ids, baseline_by_house, n_trades,
                   block_hours=BLOCK_HOURS):
    """net_kw(x) = const + dot(coeffs, x) for one house.

    x = [c_0..c_{n-1}, a_0..a_{m-1}, d_0..d_{m-1}] — trade retentions, then one
    ABSORB and one DISCHARGE variable per battery house, both non-negative.

    Splitting the battery into two non-negative variables rather than one signed
    variable is what makes the objective behave: a signed variable with a linear
    cost is driven to whichever bound the sign favours, so batteries would cycle
    every block whether or not a constraint needed it. With two costed variables
    the LP leaves both at zero unless a constraint forces movement.

    Only trades where this house is the SELLER, and this house's own battery
    variable (if any), affect its own net_kw — matches the radial,
    single-house-segment simplification carried over from powerflow.py.
    Curtailing a sale (c_t < 1) throttles that seller's generation for the
    curtailed share, which INCREASES that seller's net_kw (less export).
    Charging a battery is additional local consumption, which ALSO
    increases net_kw. Both push in the same direction — the direction that
    relieves an export-driven (solar-surplus) overload.
    """
    const = baseline_by_house.get(house_id, 0.0)
    coeffs = [0.0] * (n_trades + 2 * len(battery_house_ids))
    for i, t in enumerate(trades):
        if t.seller_id == house_id:
            share = t.quantity_kwh / block_hours
            const += share          # the "+qty/BH" from (1 - c_t) expanded
            coeffs[i] -= share      # the "-c_t*qty/BH" term
    m = len(battery_house_ids)
    for j, h in enumerate(battery_house_ids):
        if h == house_id:
            # Absorbing is extra local load (+); discharging displaces it (-).
            coeffs[n_trades + j] += 1.0 / block_hours
            coeffs[n_trades + m + j] -= 1.0 / block_hours
    return const, coeffs


def _bidirectional_row(const, coeffs, limit, A_ub, b_ub):
    """Add |const + coeffs.x| <= limit as two linear rows."""
    A_ub.append(list(coeffs));            b_ub.append(limit - const)
    A_ub.append([-c for c in coeffs]);    b_ub.append(limit + const)


def _linprog():
    """Imported lazily so that `import engine.algo` — and therefore the whole
    engine, every test suite, and C's grid modules — still works on a machine
    without scipy. The stub path below never needs it."""
    from scipy.optimize import linprog
    return linprog


def solve(trades: list[Trade], limits, batteries=None, topology=None) -> ReshapeSolution:
    """Constraint-feasible trade reshaping (PRD critical path, hour 20).

    Pass 1: curtailment only. c_t in [0,1] per trade — fraction of each
    trade's underlying seller generation actually retained.

    Pass 2: adds b_h >= 0 per battery-equipped house — kWh absorbed this
    block, bounded by both its power limit and its remaining headroom.

    Never raises. If linprog reports infeasible, returns feasible=False
    with an empty solution — the caller (C) handles the fallback.

    Invariants:
      FL_A  every retention value is in [0, 1]
      FL_B  every battery_charge value is >= 0 and within both its limits
      FL_C  a feasible=True solution satisfies every constraint to 1e-6
    """
    # batteries: {house_id: kWh currently stored}. An object is not accepted —
    # passing one used to raise TypeError, which the caller's blanket except
    # turned into feasible=False on every single call.
    batteries = dict(batteries) if batteries else {}

    # One source of truth, taken from the caller. Hardcoding these made the LP
    # solve a strictly harder problem than the sentinel actually enforces, with
    # a battery lever four times weaker than the real one.
    block_hours = BLOCK_HOURS
    voltage_band = VOLTAGE_BAND
    if limits:
        first = next(iter(limits.values()), None)
        block_hours = _get(first, "block_hours", BLOCK_HOURS) or BLOCK_HOURS
        voltage_band = _get(first, "voltage_band", VOLTAGE_BAND) or VOLTAGE_BAND
    trades = list(trades)
    n_trades = len(trades)

    battery_house_ids = [h for h in batteries if topology and h in topology]
    n_batt = len(battery_house_ids)
    n_vars = n_trades + 2 * n_batt

    if n_vars == 0:
        return ReshapeSolution(retention={}, battery_charge={}, feasible=True, objective_value=0.0)

    # --- objective: maximise sum(c_t*qty_t*price_t) - sum(b_h*fee); linprog minimises ---
    cost = [0.0] * n_vars
    for i, t in enumerate(trades):
        cost[i] = -(t.quantity_kwh * t.clearing_price)
    for j in range(n_batt):
        cost[n_trades + j] = STORAGE_FEE            # absorb
        cost[n_trades + n_batt + j] = DISCHARGE_FEE  # discharge

    # --- bounds: c_t in [0,1]; b_h in [0, min(power limit, capacity headroom)] ---
    bounds = [(0.0, 1.0)] * n_trades
    absorb_bounds, discharge_bounds = [], []
    for h in battery_house_ids:
        house = topology[h]
        stored_h = batteries[h]
        power_cap = max(0.0, house.battery_max_kw * block_hours)
        headroom_cap = max(0.0, house.battery_kwh - stored_h)
        absorb_bounds.append((0.0, min(power_cap, headroom_cap)))
        # You cannot discharge energy that is not in the battery.
        discharge_bounds.append((0.0, min(power_cap, max(0.0, stored_h))))
    bounds.extend(absorb_bounds)
    bounds.extend(discharge_bounds)

    # --- build a baseline_kw lookup merged across every transformer in limits ---
    baseline_by_house: dict[str, float] = {}
    if limits:
        for lim in limits.values():
            baseline_by_house.update(_get(lim, "baseline_kw", {}) or {})

    A_ub: list[list[float]] = []
    b_ub: list[float] = []

    # --- per-transformer loading constraint, both directions ---
    if limits:
        for tf_id, lim in limits.items():
            rating_kva = _get(lim, "rating_kva")
            if rating_kva is None:
                continue
            loading_limit = _get(lim, "loading_limit", LOADING_LIMIT)
            limit_kw = rating_kva * loading_limit

            houses_on_tf = [h for h in baseline_by_house
                            if topology and h in topology and topology[h].transformer_id == tf_id]
            # also include battery/seller houses on this transformer even if
            # they weren't in baseline_by_house for some reason
            const_total = 0.0
            coeffs_total = [0.0] * n_vars
            for h in set(houses_on_tf):
                c, co = _house_kw_expr(h, trades, battery_house_ids, baseline_by_house,
                                       n_trades, block_hours)
                const_total += c
                coeffs_total = [a + b for a, b in zip(coeffs_total, co)]

            _bidirectional_row(const_total, coeffs_total, limit_kw, A_ub, b_ub)

    # --- per-house voltage band, both directions (reuses powerflow's exact R/X/Vnom math) ---
    if topology:
        for h in topology:
            house = topology[h]
            distance_m = getattr(house, "distance_m", None)
            if distance_m is None:
                continue
            r_seg = R_OHM_PER_M * distance_m
            x_seg = X_OHM_PER_M * distance_m
            # deviation = -(r_seg*P_w + x_seg*Q_w)/V^2, P_w=P_kw*1000, Q_w=P_w*_Q_FACTOR
            power_coeff = -(r_seg + x_seg * _Q_FACTOR) * 1000.0 / (V_NOM_V ** 2)

            const, coeffs = _house_kw_expr(h, trades, battery_house_ids, baseline_by_house,
                                           n_trades, block_hours)
            if const == 0.0 and all(v == 0.0 for v in coeffs):
                continue  # untouched by any decision variable, no baseline — nothing to constrain

            dev_const = power_coeff * const
            dev_coeffs = [power_coeff * v for v in coeffs]
            _bidirectional_row(dev_const, dev_coeffs, voltage_band, A_ub, b_ub)

    result = _linprog()(
        c=cost,
        A_ub=A_ub if A_ub else None,
        b_ub=b_ub if b_ub else None,
        bounds=bounds,
        method="highs",
    )

    if not result.success:
        return ReshapeSolution(retention={}, battery_charge={}, feasible=False, objective_value=0.0)

    x = result.x
    retention = {trades[i].trade_id: float(max(0.0, min(1.0, x[i]))) for i in range(n_trades)}
    battery_charge = {battery_house_ids[j]: float(max(0.0, x[n_trades + j]))
                      for j in range(n_batt)}
    battery_discharge = {battery_house_ids[j]: float(max(0.0, x[n_trades + n_batt + j]))
                         for j in range(n_batt)}
    objective_value = float(-result.fun)

    return ReshapeSolution(retention=retention, battery_charge=battery_charge,
                           feasible=True, objective_value=objective_value,
                           battery_discharge=battery_discharge)
