"""
Two-mode aircraft placement optimizer.

Mode 1 — AUTOSTACK: Mixed aircraft types → OR-Tools CP-SAT selects best
         non-conflicting set from pre-validated candidates.

Mode 2 — CAPACITY:  Single aircraft type → greedy sequential fill via
         _try_place_single(). Finds true max count without solver overhead.

Both modes share the same geometry engine (composite OBB collision from
collision.py) and the same per-component, per-axis ClearancePolicy.

The legacy `buffer_ft` parameter is preserved on the public entry points and
mapped to a scaled policy via `ClearancePolicy.from_buffer_ft`.
"""

import math
import random
import time
from collections import defaultdict
from ortools.sat.python import cp_model

from .clearance import ClearancePolicy, validate_access
from .collision import (
    _lat_lng_to_meters,
    _obb_overlap,
    _obb_inside_polygon,
    _point_in_polygon,
    aircraft_obbs_collide,
    build_aircraft_obbs,
    SAFETY_BUFFER_FT,
    FT_TO_M,
)
from .autostack import (
    _polygon_centroid,
    _polygon_bounds,
    _meters_to_latlng,
    _shoelace_area,
    _try_place_single,
)

OPERATIONAL_HEADINGS = [0, 60, 120, 180, 240, 300]

HEADING_STRATEGIES = [
    ("Nose-in", [0, 180]),
    ("Angled", [60, 240]),
    ("Mixed", OPERATIONAL_HEADINGS),
]

# Pruning ceilings per mode (was a hard 200 across the board).
PRUNE_MAX_AUTOSTACK = 250
PRUNE_MAX_RAMP = 350
PRUNE_MAX_CAPACITY = 500

# Cap on realism pair_vars added to the CP-SAT model. Kept modest so the
# realism term doesn't dominate solve time — count is already strictly
# preferred via the 1000× weight, so realism only ever breaks ties.
REALISM_PAIR_CAP = 800

# Wall-clock cap for CP-SAT (seconds). The solver returns best-feasible if it
# can't prove optimal in time. Was effectively unbounded before; with the
# realism term added we bound it explicitly.
ORTOOLS_TIME_LIMIT_S = 10.0


# ═══════════════════════════════════════════════════════════════════════
# SHARED GEOMETRY ENGINE
# ═══════════════════════════════════════════════════════════════════════

def _aircraft_inside_zone(obbs, zone_m):
    """Both buffered components must fit inside the zone polygon."""
    return (
        _obb_inside_polygon(obbs["fuselage_buffered"], zone_m) and
        _obb_inside_polygon(obbs["wings_buffered"], zone_m)
    )


def _generate_candidates(zone_m, zone_bounds, aircraft_list, parked_obbs,
                         policy, headings, ref_lat, ref_lng):
    """Generate feasible candidate placements per aircraft.

    Grid-scans positions x headings. A candidate is valid if:
      1. Both buffered OBBs fit inside zone polygon
      2. No collision with any parked aircraft (composite OBB model)
      3. validate_access hook accepts it (no-op today)
    """
    min_x, min_y, max_x, max_y = zone_bounds
    candidates = {}

    boundary_m = max(policy.boundary_lateral_m, policy.boundary_longitudinal_m)

    for ac_idx, ac in enumerate(aircraft_list):
        ws = ac["wingspan_m"]
        ln = ac["length_m"]
        ac_candidates = []
        step = min(ws, ln) * 0.5

        for heading in headings:
            margin = max(ws, ln) / 2 + boundary_m
            y = min_y + margin
            while y <= max_y - margin:
                x = min_x + margin
                while x <= max_x - margin:
                    obbs = build_aircraft_obbs(x, y, heading, ws, ln, policy)
                    if not _aircraft_inside_zone(obbs, zone_m):
                        x += step
                        continue

                    collision = False
                    for parked in parked_obbs:
                        if aircraft_obbs_collide(obbs, parked):
                            collision = True
                            break

                    if not collision:
                        lat, lng = _meters_to_latlng(x, y, ref_lat, ref_lng)
                        cand = {
                            "x": x, "y": y,
                            "heading": heading,
                            "lat": lat, "lng": lng,
                            **obbs,
                        }
                        if validate_access(cand, ac_candidates, zone_m, policy):
                            ac_candidates.append(cand)
                    x += step
                y += step

        candidates[ac_idx] = ac_candidates
    return candidates


def _min_boundary_clearance(x, y, zone_m):
    """Min distance from point to any zone edge."""
    n = len(zone_m)
    min_dist = float('inf')
    for i in range(n):
        x1, y1 = zone_m[i]
        x2, y2 = zone_m[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        seg_len_sq = dx * dx + dy * dy
        if seg_len_sq < 1e-10:
            continue
        t = max(0, min(1, ((x - x1) * dx + (y - y1) * dy) / seg_len_sq))
        px, py = x1 + t * dx, y1 + t * dy
        dist = math.sqrt((x - px) ** 2 + (y - py) ** 2)
        min_dist = min(min_dist, dist)
    return min_dist


def _prune_candidates(candidates, zone_m, aircraft_list, max_total):
    """Spatially diverse pruning — farthest-point sampling."""
    n_aircraft = len(aircraft_list)
    max_per_aircraft = max(10, max_total // max(n_aircraft, 1))
    pruned = {}

    for ac_idx, cands in candidates.items():
        if not cands or len(cands) <= max_per_aircraft:
            pruned[ac_idx] = cands or []
            continue

        scored = [(c, _min_boundary_clearance(c["x"], c["y"], zone_m)) for c in cands]
        scored.sort(key=lambda s: -s[1])

        selected = [scored[0][0]]
        remaining = [s[0] for s in scored[1:]]

        while len(selected) < max_per_aircraft and remaining:
            best_idx = 0
            best_min_dist = -1
            for idx, c in enumerate(remaining):
                min_dist = min(
                    math.sqrt((c["x"] - s["x"]) ** 2 + (c["y"] - s["y"]) ** 2)
                    for s in selected
                )
                if min_dist > best_min_dist:
                    best_min_dist = min_dist
                    best_idx = idx
            selected.append(remaining.pop(best_idx))

        pruned[ac_idx] = selected
    return pruned


def _build_conflict_graph(candidates, aircraft_list, policy):
    """Build conflict pairs using spatial grid partitioning."""
    t_bg = time.perf_counter()
    max_dim = 0
    for ac in aircraft_list:
        max_dim = max(max_dim, ac["wingspan_m"], ac["length_m"])
    cell_size = max_dim + 2 * policy.max_margin_m() + 5

    grid = defaultdict(list)
    total_cands = 0
    for ac_idx, cands in candidates.items():
        total_cands += len(cands)
        for cand_idx, c in enumerate(cands):
            cell_x = int(c["x"] / cell_size)
            cell_y = int(c["y"] / cell_size)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    grid[(cell_x + dx, cell_y + dy)].append(
                        (ac_idx, cand_idx, c))

    conflicts = set()
    checked = set()

    for cell_entries in grid.values():
        for i in range(len(cell_entries)):
            ac_i, ci, cand_a = cell_entries[i]
            for j in range(i + 1, len(cell_entries)):
                ac_j, cj, cand_b = cell_entries[j]
                if ac_i == ac_j:
                    continue

                pair_key = (min((ac_i, ci), (ac_j, cj)),
                            max((ac_i, ci), (ac_j, cj)))
                if pair_key in checked:
                    continue
                checked.add(pair_key)

                if aircraft_obbs_collide(cand_a, cand_b):
                    conflicts.add(pair_key)

    took = time.perf_counter() - t_bg
    print(f"[gen] conflict_graph n_cands={total_cands} conflicts={len(conflicts)} took={took:.3f}s", flush=True)
    return list(conflicts)


def _build_parked_obbs(parked_aircraft, ref_lat, ref_lng, policy):
    """Convert parked aircraft to OBB dicts (4-shape composite)."""
    obbs = []
    for ac in (parked_aircraft or []):
        ws = ac.get("wingspan_m") or 0
        ln = ac.get("length_m") or 0
        if ws <= 0 or ln <= 0:
            continue
        px, py = _lat_lng_to_meters(ac["lat"], ac["lng"], ref_lat, ref_lng)
        hdg = ac.get("heading", 0)
        obbs.append(build_aircraft_obbs(px, py, hdg, ws, ln, policy))
    return obbs


# ═══════════════════════════════════════════════════════════════════════
# Realism scoring (soft tie-breakers — must NEVER outweigh placement count)
# ═══════════════════════════════════════════════════════════════════════

def _heading_consistency_pairs(candidates, aircraft_list, max_dim):
    """Cross-aircraft candidate pairs that share heading and sit close together.

    Used as a soft objective bonus: layouts where neighboring placed candidates
    share a heading are preferred when they tie on placement count.
    """
    pairs = []
    radius = max(1.5 * max_dim, 5.0)
    flat = []  # (ac_idx, cand_idx, x, y, heading)
    for ac_idx, cands in candidates.items():
        for cand_idx, c in enumerate(cands):
            flat.append((ac_idx, cand_idx, c["x"], c["y"], c["heading"]))

    by_heading = defaultdict(list)
    for entry in flat:
        by_heading[entry[4]].append(entry)

    for entries in by_heading.values():
        for i in range(len(entries)):
            ai, ci, xi, yi, _ = entries[i]
            for j in range(i + 1, len(entries)):
                aj, cj, xj, yj, _ = entries[j]
                if ai == aj:
                    continue
                if (xi - xj) ** 2 + (yi - yj) ** 2 <= radius * radius:
                    pairs.append(((ai, ci), (aj, cj)))
                    if len(pairs) >= REALISM_PAIR_CAP * 2:
                        break
            if len(pairs) >= REALISM_PAIR_CAP * 2:
                break
        if len(pairs) >= REALISM_PAIR_CAP * 2:
            break
    return pairs


def _row_alignment_pairs(candidates, row_tolerance_m=4.0):
    """Cross-aircraft candidate pairs whose y-coordinates fall in the same row.

    Used as a soft objective bonus for ramp mode: layouts with aircraft sharing
    rows (within `row_tolerance_m`) look more like a real apron.
    """
    pairs = []
    by_row = defaultdict(list)
    for ac_idx, cands in candidates.items():
        for cand_idx, c in enumerate(cands):
            row_key = int(round(c["y"] / row_tolerance_m))
            by_row[row_key].append((ac_idx, cand_idx))

    for entries in by_row.values():
        for i in range(len(entries)):
            ai, ci = entries[i]
            for j in range(i + 1, len(entries)):
                aj, cj = entries[j]
                if ai == aj:
                    continue
                pairs.append(((ai, ci), (aj, cj)))
                if len(pairs) >= REALISM_PAIR_CAP * 2:
                    break
            if len(pairs) >= REALISM_PAIR_CAP * 2:
                break
        if len(pairs) >= REALISM_PAIR_CAP * 2:
            break
    return pairs


def _sample_pairs(pairs, cap):
    """Down-sample to at most `cap` pairs. Deterministic seed for reproducibility."""
    if len(pairs) <= cap:
        return pairs
    rng = random.Random(0)
    return rng.sample(pairs, cap)


# ═══════════════════════════════════════════════════════════════════════
# MODE 1: AUTOSTACK — OR-Tools CP-SAT selection
# ═══════════════════════════════════════════════════════════════════════

def _ortools_select(candidates, aircraft_list, conflicts, zone_area,
                    adg_weights=None, total_units=0,
                    realism_mode=None):
    """Select best non-conflicting candidate set using OR-Tools CP-SAT.

    Objective: 1000 * placement_count + sum(realism_bonus_pairs).
    The 1000× weight guarantees count strictly dominates; realism only breaks
    ties between equally-good layouts.

    realism_mode: "autostack" → heading-consistency, "ramp" → row-alignment,
                  None → no realism term (used by capacity).
    """
    model = cp_model.CpModel()

    # Binary vars: s[ac_idx, cand_idx] = 1 if selected
    s = {}
    for ac_idx, cands in candidates.items():
        for cand_idx in range(len(cands)):
            s[(ac_idx, cand_idx)] = model.NewBoolVar(f"s_{ac_idx}_{cand_idx}")

    # At most one candidate per aircraft
    for ac_idx, cands in candidates.items():
        if cands:
            model.AddAtMostOne(s[(ac_idx, j)] for j in range(len(cands)))

    # Conflict constraints
    for (ac_i, ci), (ac_j, cj) in conflicts:
        model.AddAtMostOne([s[(ac_i, ci)], s[(ac_j, cj)]])

    count_term = sum(
        s[(ac_idx, j)]
        for ac_idx, cands in candidates.items()
        for j in range(len(cands))
    )

    # Realism soft term — pair selected when both endpoints selected
    realism_pairs = []
    if realism_mode == "autostack":
        max_dim = max(
            (ac["wingspan_m"] for ac in aircraft_list), default=0,
        )
        max_dim = max(max_dim, max(
            (ac["length_m"] for ac in aircraft_list), default=0,
        ))
        realism_pairs = _heading_consistency_pairs(candidates, aircraft_list, max_dim)
    elif realism_mode == "ramp":
        realism_pairs = _row_alignment_pairs(candidates)
    realism_pairs = _sample_pairs(realism_pairs, REALISM_PAIR_CAP)

    pair_vars = []
    for k, ((ai, ci), (aj, cj)) in enumerate(realism_pairs):
        if (ai, ci) not in s or (aj, cj) not in s:
            continue
        pv = model.NewBoolVar(f"r_{k}")
        model.Add(pv <= s[(ai, ci)])
        model.Add(pv <= s[(aj, cj)])
        model.Add(pv >= s[(ai, ci)] + s[(aj, cj)] - 1)
        pair_vars.append(pv)

    if pair_vars:
        model.Maximize(1000 * count_term + sum(pair_vars))
    else:
        model.Maximize(count_term)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = ORTOOLS_TIME_LIMIT_S

    n_vars = sum(len(c) for c in candidates.values())
    t0 = time.perf_counter()
    status = solver.Solve(model)
    print(f"[solve] _ortools_select aircraft={len(aircraft_list)} n_vars={n_vars} conflicts={len(conflicts)} realism_pairs={len(pair_vars)} took={time.perf_counter()-t0:.3f}s status={solver.StatusName(status)}", flush=True)

    empty = {
        "placements": [],
        "unplaced": [a.get("tail_number", "unknown") for a in aircraft_list],
        "utilization": 0,
        "heading_strategy": "optimal",
    }

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return empty

    result_placements = []
    result_unplaced = []
    consumed_units = 0

    for ac_idx, cands in candidates.items():
        ac = aircraft_list[ac_idx]
        selected = False
        for cand_idx, c in enumerate(cands):
            if solver.Value(s[(ac_idx, cand_idx)]) == 1:
                result_placements.append({
                    "tail_number": ac.get("tail_number", ""),
                    "lat": c["lat"], "lng": c["lng"],
                    "heading": c["heading"],
                    "wingspan_m": ac["wingspan_m"],
                    "length_m": ac["length_m"],
                    "adg_class": ac.get("adg_class", 2),
                })
                cls = ac.get("adg_class", 2)
                consumed_units += (adg_weights or {}).get(cls, 1)
                selected = True
                break
        if not selected:
            result_unplaced.append(ac.get("tail_number", "unknown"))

    util = (consumed_units / total_units * 100) if total_units > 0 else 0

    return {
        "placements": result_placements,
        "unplaced": result_unplaced,
        "utilization": round(util, 1),
        "heading_strategy": "optimal",
    }


def optimize_placement(zone_coords, aircraft_to_place, parked_aircraft=None,
                       buffer_ft=SAFETY_BUFFER_FT, headings=None,
                       strategy_label="optimal",
                       adg_weights=None, total_units=0,
                       policy=None):
    """AUTOSTACK MODE: Two-phase optimizer with composite-OBB clearance.

    Pass either `policy` (preferred) or the legacy `buffer_ft` (interpreted as
    a uniform scale on `ClearancePolicy.DEFAULT`).
    """
    if policy is None:
        policy = ClearancePolicy.from_buffer_ft(buffer_ft)

    empty = {"placements": [], "unplaced": [], "utilization": 0,
             "heading_strategy": strategy_label}

    if not zone_coords or len(zone_coords) < 3:
        return empty
    if headings is None:
        headings = OPERATIONAL_HEADINGS
    if parked_aircraft is None:
        parked_aircraft = []

    to_place = [a for a in aircraft_to_place
                if a.get("wingspan_m") and a.get("length_m")]
    if not to_place:
        empty["unplaced"] = [a.get("tail_number", "unknown") for a in aircraft_to_place]
        return empty

    ref_lat, ref_lng = _polygon_centroid(zone_coords)
    zone_m = [_lat_lng_to_meters(c[0], c[1], ref_lat, ref_lng) for c in zone_coords]
    zone_bounds = _polygon_bounds(zone_m)
    zone_area = _shoelace_area(zone_m)

    parked_obbs = _build_parked_obbs(parked_aircraft, ref_lat, ref_lng, policy)

    candidates = _generate_candidates(
        zone_m, zone_bounds, to_place, parked_obbs,
        policy, headings, ref_lat, ref_lng)

    candidates = _prune_candidates(candidates, zone_m, to_place, max_total=PRUNE_MAX_AUTOSTACK)

    conflicts = _build_conflict_graph(candidates, to_place, policy)

    result = _ortools_select(
        candidates, to_place, conflicts, zone_area,
        adg_weights=adg_weights, total_units=total_units,
        realism_mode="autostack")
    result["heading_strategy"] = strategy_label
    return result


# ═══════════════════════════════════════════════════════════════════════
# MODE 1B: RAMP — Row-based layout
# ═══════════════════════════════════════════════════════════════════════

def _find_primary_axis(zone_m):
    """Find the zone's primary axis angle from its longest edge."""
    n = len(zone_m)
    best_length = 0
    best_angle = 0

    for i in range(n):
        x1, y1 = zone_m[i]
        x2, y2 = zone_m[(i + 1) % n]
        dx = x2 - x1
        dy = y2 - y1
        length = math.sqrt(dx * dx + dy * dy)
        if length > best_length:
            best_length = length
            best_angle = math.degrees(math.atan2(dx, dy)) % 360

    return best_angle


def _generate_ramp_grid_candidates(zone_m, zone_bounds, aircraft_list, parked_obbs,
                                   policy, primary_axis_angle, ref_lat, ref_lng):
    """Generate row-column grid candidates with per-aircraft heading choice.

    Row step uses wing lateral margin (rows separated by wingspan + wing buffer).
    Col step uses fuselage longitudinal margin (columns can pack tight nose-tail).
    """
    t_gen = time.perf_counter()
    min_x, min_y, max_x, max_y = zone_bounds

    rad = math.radians(primary_axis_angle)
    cos_h = math.cos(rad)
    sin_h = math.sin(rad)

    row_dx = sin_h
    row_dy = cos_h
    col_dx = cos_h
    col_dy = -sin_h

    heading_in = (primary_axis_angle + 90) % 360
    heading_out = (primary_axis_angle + 270) % 360

    candidates = {}
    total = 0

    for ac_idx, ac in enumerate(aircraft_list):
        ws = ac["wingspan_m"]
        ln = ac["length_m"]
        ac_candidates = []

        row_step = (ws + 2 * policy.wing_lateral_m) * 0.5
        col_step = (ln + 2 * policy.fuselage_longitudinal_m) * 0.5

        for row_i in range(-20, 21):
            for col_i in range(-20, 21):
                px = min_x + (max_x - min_x) / 2 + row_i * row_step * row_dx + col_i * col_step * col_dx
                py = min_y + (max_y - min_y) / 2 + row_i * row_step * row_dy + col_i * col_step * col_dy

                for heading in [heading_in, heading_out]:
                    obbs = build_aircraft_obbs(px, py, heading, ws, ln, policy)
                    if not _aircraft_inside_zone(obbs, zone_m):
                        continue

                    collision = False
                    for parked in parked_obbs:
                        if aircraft_obbs_collide(obbs, parked):
                            collision = True
                            break

                    if not collision:
                        lat, lng = _meters_to_latlng(px, py, ref_lat, ref_lng)
                        cand = {
                            "x": px, "y": py,
                            "heading": heading,
                            "lat": lat, "lng": lng,
                            **obbs,
                        }
                        if validate_access(cand, ac_candidates, zone_m, policy):
                            ac_candidates.append(cand)

        candidates[ac_idx] = ac_candidates
        total += len(ac_candidates)

    print(f"[gen] ramp_grid_candidates aircraft={len(aircraft_list)} cands_total={total} took={time.perf_counter()-t_gen:.3f}s", flush=True)
    return candidates


def optimize_placement_ramp(zone_coords, aircraft_to_place, parked_aircraft=None,
                            buffer_ft=SAFETY_BUFFER_FT,
                            strategy_label="optimal",
                            adg_weights=None, total_units=0,
                            policy=None):
    """RAMP MODE: Row-column grid with per-aircraft heading optimization."""
    if policy is None:
        policy = ClearancePolicy.from_buffer_ft(buffer_ft)

    empty = {"placements": [], "unplaced": [], "utilization": 0,
             "heading_strategy": strategy_label}

    if not zone_coords or len(zone_coords) < 3:
        return empty
    if parked_aircraft is None:
        parked_aircraft = []

    to_place = [a for a in aircraft_to_place
                if a.get("wingspan_m") and a.get("length_m")]
    if not to_place:
        empty["unplaced"] = [a.get("tail_number", "unknown") for a in aircraft_to_place]
        return empty

    ref_lat, ref_lng = _polygon_centroid(zone_coords)
    zone_m = [_lat_lng_to_meters(c[0], c[1], ref_lat, ref_lng) for c in zone_coords]
    zone_bounds = _polygon_bounds(zone_m)
    zone_area = _shoelace_area(zone_m)

    primary_axis = _find_primary_axis(zone_m)

    parked_obbs = _build_parked_obbs(parked_aircraft, ref_lat, ref_lng, policy)

    candidates = _generate_ramp_grid_candidates(
        zone_m, zone_bounds, to_place, parked_obbs,
        policy, primary_axis, ref_lat, ref_lng)

    candidates = _prune_candidates(candidates, zone_m, to_place, max_total=PRUNE_MAX_RAMP)

    conflicts = _build_conflict_graph(candidates, to_place, policy)

    result = _ortools_select(
        candidates, to_place, conflicts, zone_area,
        adg_weights=adg_weights, total_units=total_units,
        realism_mode="ramp")
    result["heading_strategy"] = strategy_label
    return result


RAMP_STRATEGIES = [
    ("Grid-optimized", None),
]


# ═══════════════════════════════════════════════════════════════════════
# MODE 2: CAPACITY — Greedy sequential fill
# ═══════════════════════════════════════════════════════════════════════

def _greedy_fill(zone_m, zone_bounds, existing_items, wingspan, length,
                 headings, policy, max_count=50):
    """Greedily place as many aircraft of given size as possible."""
    placed = list(existing_items)
    count = 0

    while count < max_count:
        result = _try_place_single(
            zone_m, zone_bounds, placed,
            wingspan, length, headings, policy)
        if result is None:
            break
        x, y, heading = result
        placed.append(build_aircraft_obbs(x, y, heading, wingspan, length, policy))
        count += 1

    return count


def _greedy_fill_with_positions(zone_m, zone_bounds, existing_items, wingspan, length,
                                headings, policy, max_count, ref_lat, ref_lng,
                                adg_class=1):
    """Like _greedy_fill but returns positions for map preview."""
    placed = list(existing_items)
    positions = []
    n_headings = len(headings)

    while len(positions) < max_count:
        rotation = len(positions) % n_headings if n_headings > 0 else 0
        rotated_headings = headings[rotation:] + headings[:rotation]

        result = _try_place_single(
            zone_m, zone_bounds, placed,
            wingspan, length, rotated_headings, policy)
        if result is None:
            break
        x, y, heading = result
        placed.append(build_aircraft_obbs(x, y, heading, wingspan, length, policy))
        lat, lng = _meters_to_latlng(x, y, ref_lat, ref_lng)
        positions.append({
            "lat": lat, "lng": lng, "heading": heading,
            "wingspan_m": wingspan, "length_m": length,
            "adg_class": adg_class,
        })

    return positions


def _capacity_fill_ramp_ortools(zone_m, zone_bounds, existing_items, wingspan, length,
                                headings, policy, primary_axis_angle, max_count,
                                ref_lat=0, ref_lng=0, return_positions=False,
                                cache=None):
    """Dense candidate generation → OR-Tools selection for ramp capacity.

    Allows buffer-buffer overlap (the per-component model already enforces wing
    and fuselage envelopes), finding tighter packings than greedy.

    If `cache` is a dict, results are memoized per
    (wingspan, length, policy_hash, id(existing_items), sorted(headings)).
    """
    policy_key = (
        round(policy.wing_lateral_m, 4),
        round(policy.wing_longitudinal_m, 4),
        round(policy.fuselage_lateral_m, 4),
        round(policy.fuselage_longitudinal_m, 4),
        round(policy.boundary_lateral_m, 4),
        round(policy.boundary_longitudinal_m, 4),
    )
    key = None
    if cache is not None:
        key = (round(wingspan, 3), round(length, 3), policy_key,
               id(existing_items), tuple(sorted(headings)))
        hit = cache.get(key)
        if hit and hit["max_count_used"] >= max_count:
            count = min(hit["count"], max_count)
            if return_positions:
                selected = hit["selected"][:max_count]
                positions = [
                    {
                        **dict(zip(("lat", "lng"), _meters_to_latlng(sx, sy, ref_lat, ref_lng))),
                        "heading": sh,
                        "wingspan_m": wingspan,
                        "length_m": length,
                        "adg_class": 1,
                    }
                    for (sx, sy, sh) in selected
                ]
                print(f"[memo] ramp_ortools HIT ws={wingspan:.1f} ln={length:.1f} cached_count={hit['count']} returning=positions({len(positions)})", flush=True)
                return positions
            print(f"[memo] ramp_ortools HIT ws={wingspan:.1f} ln={length:.1f} cached_count={hit['count']} returning=count({count})", flush=True)
            return count
        if hit:
            print(f"[memo] ramp_ortools PARTIAL ws={wingspan:.1f} ln={length:.1f} cached_cap={hit['max_count_used']} requested_cap={max_count} — rebuilding", flush=True)

    t_gen = time.perf_counter()
    min_x, min_y, max_x, max_y = zone_bounds

    # Dense candidate generation: per-axis steps using component-specific margins.
    # Lateral/row spacing controlled by wing margin; longitudinal/col by fuselage margin.
    x_step = (wingspan + 2 * policy.wing_lateral_m) * 0.35
    y_step = (length + 2 * policy.fuselage_longitudinal_m) * 0.35

    all_candidates = []
    seen_positions = set()

    for x_off_frac in [0.0, 0.15, 0.4, 0.7]:
        for y_off_frac in [0.0, 0.15, 0.4, 0.7]:
            x = min_x + wingspan / 2 + x_off_frac * x_step
            while x <= max_x - wingspan / 2:
                y = min_y + length / 2 + y_off_frac * y_step
                while y <= max_y - length / 2:
                    snap_key = (round(x, 1), round(y, 1))
                    if snap_key in seen_positions:
                        y += y_step
                        continue
                    seen_positions.add(snap_key)

                    for heading in headings:
                        obbs = build_aircraft_obbs(x, y, heading, wingspan, length, policy)
                        if not _aircraft_inside_zone(obbs, zone_m):
                            continue

                        collision = False
                        for existing in existing_items:
                            if aircraft_obbs_collide(obbs, existing):
                                collision = True
                                break

                        if not collision:
                            cand = {"x": x, "y": y, "heading": heading, **obbs}
                            if validate_access(cand, all_candidates, zone_m, policy):
                                all_candidates.append(cand)

                    y += y_step
                x += x_step

    # Cap candidate set so the conflict graph stays tractable.
    if len(all_candidates) > PRUNE_MAX_CAPACITY:
        rng = random.Random(0)
        all_candidates = rng.sample(all_candidates, PRUNE_MAX_CAPACITY)

    gen_s = time.perf_counter() - t_gen

    if not all_candidates:
        print(f"[solve] _capacity_fill_ramp_ortools ws={wingspan:.1f}m ln={length:.1f}m n_vars=0 conflicts=0 gen_s={gen_s:.3f} conflict_s=0.000 solve_s=0.000 took={gen_s:.3f}s status=NO_CANDIDATES", flush=True)
        if cache is not None:
            cache[key] = {"count": 0, "selected": [], "max_count_used": max_count}
        return [] if return_positions else 0

    # Build conflict graph between candidates
    t_conf = time.perf_counter()
    n = len(all_candidates)
    conflicts = []
    bucket_size = max(wingspan, length) + 2 * policy.max_margin_m() + 5
    buckets = defaultdict(list)
    for idx, c in enumerate(all_candidates):
        bx = int(c["x"] / bucket_size)
        by = int(c["y"] / bucket_size)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                buckets[(bx + dx, by + dy)].append(idx)

    checked = set()
    for bucket_entries in buckets.values():
        for i in range(len(bucket_entries)):
            for j in range(i + 1, len(bucket_entries)):
                a_idx = bucket_entries[i]
                b_idx = bucket_entries[j]
                pair = (min(a_idx, b_idx), max(a_idx, b_idx))
                if pair in checked:
                    continue
                checked.add(pair)

                if aircraft_obbs_collide(all_candidates[a_idx], all_candidates[b_idx]):
                    conflicts.append((a_idx, b_idx))

    conflict_s = time.perf_counter() - t_conf

    t_solve = time.perf_counter()
    model = cp_model.CpModel()
    s = [model.NewBoolVar(f"c_{i}") for i in range(n)]

    for a_idx, b_idx in conflicts:
        model.AddAtMostOne([s[a_idx], s[b_idx]])

    model.Add(sum(s) <= max_count)
    model.Maximize(sum(s))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = ORTOOLS_TIME_LIMIT_S
    status = solver.Solve(model)
    solve_s = time.perf_counter() - t_solve
    took = gen_s + conflict_s + solve_s
    print(f"[solve] _capacity_fill_ramp_ortools ws={wingspan:.1f}m ln={length:.1f}m n_vars={n} conflicts={len(conflicts)} gen_s={gen_s:.3f} conflict_s={conflict_s:.3f} solve_s={solve_s:.3f} took={took:.3f}s status={solver.StatusName(status)}", flush=True)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        if cache is not None:
            cache[key] = {"count": 0, "selected": [], "max_count_used": max_count}
        return [] if return_positions else 0

    selected = []
    for i in range(n):
        if solver.Value(s[i]) == 1:
            c = all_candidates[i]
            selected.append((c["x"], c["y"], c["heading"]))

    if cache is not None:
        cache[key] = {"count": len(selected), "selected": selected, "max_count_used": max_count}

    if return_positions:
        positions = [
            {
                **dict(zip(("lat", "lng"), _meters_to_latlng(sx, sy, ref_lat, ref_lng))),
                "heading": sh,
                "wingspan_m": wingspan,
                "length_m": length,
                "adg_class": 1,
            }
            for (sx, sy, sh) in selected
        ]
        return positions
    return len(selected)


def _greedy_fill_ramp(zone_m, zone_bounds, existing_items, wingspan, length,
                      headings, policy, primary_axis_angle, max_count=50,
                      cache=None):
    """OR-Tools based ramp fill for maximum packing."""
    return _capacity_fill_ramp_ortools(
        zone_m, zone_bounds, existing_items, wingspan, length,
        headings, policy, primary_axis_angle, max_count,
        cache=cache)


def _greedy_fill_ramp_with_positions(zone_m, zone_bounds, existing_items, wingspan, length,
                                     headings, policy, primary_axis_angle,
                                     max_count, ref_lat, ref_lng, adg_class=1,
                                     cache=None):
    """OR-Tools based ramp fill returning positions for ghost preview."""
    return _capacity_fill_ramp_ortools(
        zone_m, zone_bounds, existing_items, wingspan, length,
        headings, policy, primary_axis_angle, max_count,
        ref_lat=ref_lat, ref_lng=ref_lng, return_positions=True,
        cache=cache)


def compute_zone_capacity_units(zone_coords, adg_dims, buffer_ft=SAFETY_BUFFER_FT,
                                parking_mode="hangar", policy=None):
    """CAPACITY MODE: Compute zone capacity in ADG-I equivalent units.

    For each ADG class, runs greedy fill on an empty zone to find max count.
    Pass either `policy` or the legacy `buffer_ft` (uniform-scale shim).
    """
    if not zone_coords or len(zone_coords) < 3:
        return {"total_units": 0, "max_by_adg": {}, "adg_weights": {}}

    if policy is None:
        policy = ClearancePolicy.from_buffer_ft(buffer_ft)

    ref_lat, ref_lng = _polygon_centroid(zone_coords)
    zone_m = [_lat_lng_to_meters(c[0], c[1], ref_lat, ref_lng) for c in zone_coords]
    zone_bounds = _polygon_bounds(zone_m)

    if parking_mode == "ramp":
        primary_axis = _find_primary_axis(zone_m)
        headings = [(primary_axis + 90) % 360, (primary_axis + 270) % 360]
    else:
        headings = OPERATIONAL_HEADINGS

    max_by_adg = {}
    t_loop = time.perf_counter()
    for adg_class, dims in adg_dims.items():
        ws = dims["wingspan_m"]
        ln = dims["length_m"]
        if ws <= 0 or ln <= 0:
            max_by_adg[adg_class] = 0
            continue

        if parking_mode == "ramp":
            max_by_adg[adg_class] = _greedy_fill_ramp(
                zone_m, zone_bounds, [],
                ws, ln, headings, policy, primary_axis,
                max_count=50)
        else:
            max_by_adg[adg_class] = _greedy_fill(
                zone_m, zone_bounds, [],
                ws, ln, headings, policy,
                max_count=50)
    print(f"[capacity] compute_zone_capacity_units mode={parking_mode} buffer_ft={buffer_ft} adg_classes={len(adg_dims)} took={time.perf_counter()-t_loop:.3f}s max_by_adg={max_by_adg}", flush=True)

    ref_max = max_by_adg.get(1, 0)
    total_units = ref_max

    adg_weights = {}
    for adg_class, max_count in max_by_adg.items():
        if max_count > 0 and ref_max > 0:
            adg_weights[adg_class] = round(ref_max / max_count, 2)
        else:
            adg_weights[adg_class] = 0

    return {
        "total_units": total_units,
        "max_by_adg": max_by_adg,
        "adg_weights": adg_weights,
    }


def optimize_capacity(zone_coords, parked_aircraft, adg_representative_dims,
                      buffer_ft=SAFETY_BUFFER_FT, parking_mode="hangar",
                      policy=None):
    """CAPACITY MODE: Estimate remaining capacity per ADG class with parked obstacles."""
    if not zone_coords or len(zone_coords) < 3:
        return {cls: 0 for cls in adg_representative_dims}

    if policy is None:
        policy = ClearancePolicy.from_buffer_ft(buffer_ft)

    ref_lat, ref_lng = _polygon_centroid(zone_coords)
    zone_m = [_lat_lng_to_meters(c[0], c[1], ref_lat, ref_lng) for c in zone_coords]
    zone_bounds = _polygon_bounds(zone_m)

    if parking_mode == "ramp":
        primary_axis = _find_primary_axis(zone_m)
        headings = [(primary_axis + 90) % 360, (primary_axis + 270) % 360]
    else:
        headings = OPERATIONAL_HEADINGS

    parked_obbs = _build_parked_obbs(parked_aircraft, ref_lat, ref_lng, policy)

    result = {}
    for adg_class, dims in adg_representative_dims.items():
        ws = dims["wingspan_m"]
        ln = dims["length_m"]
        if ws <= 0 or ln <= 0:
            result[adg_class] = 0
            continue

        if parking_mode == "ramp":
            result[adg_class] = _greedy_fill_ramp(
                zone_m, zone_bounds, parked_obbs,
                ws, ln, headings, policy, primary_axis,
                max_count=50)
        else:
            result[adg_class] = _greedy_fill(
                zone_m, zone_bounds, parked_obbs,
                ws, ln, headings, policy,
                max_count=50)

    return result
