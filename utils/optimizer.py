"""
Two-mode aircraft placement optimizer.

Mode 1 — AUTOSTACK: Mixed aircraft types → OR-Tools CP-SAT selects best
         non-conflicting set from pre-validated candidates. No variable limits.

Mode 2 — CAPACITY:  Single aircraft type → greedy sequential fill via
         _try_place_single(). Finds true max count without solver overhead.

Both modes share the same geometry engine (OBB collision from collision.py).

All buffer values are in FEET, converted to meters internally.
"""

import math
import time
from collections import defaultdict
from ortools.sat.python import cp_model

from .collision import (
    _lat_lng_to_meters,
    _compute_obb_corners,
    _obb_overlap,
    _obb_inside_polygon,
    _point_in_polygon,
    SAFETY_BUFFER_FT,
    FT_TO_M,
    WING_SPAN_RATIO,
    WING_CHORD_RATIO,
    FUSELAGE_WIDTH_RATIO,
    FUSELAGE_LENGTH_RATIO,
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


# ═══════════════════════════════════════════════════════════════════════
# SHARED GEOMETRY ENGINE
# ═══════════════════════════════════════════════════════════════════════

def _generate_candidates(zone_m, zone_bounds, aircraft_list, parked_obbs,
                         buffer_m, headings, ref_lat, ref_lng):
    """Generate feasible candidate placements per aircraft.

    Grid-scans positions x headings. A candidate is valid if:
      1. Buffered OBB fits inside zone polygon
      2. No collision with any parked aircraft (exact OBB model)
    """
    min_x, min_y, max_x, max_y = zone_bounds
    candidates = {}

    for ac_idx, ac in enumerate(aircraft_list):
        ws = ac["wingspan_m"]
        ln = ac["length_m"]
        ac_candidates = []
        step = min(ws, ln) * 0.5

        for heading in headings:
            margin = max(ws, ln) / 2 + buffer_m
            y = min_y + margin
            while y <= max_y - margin:
                x = min_x + margin
                while x <= max_x - margin:
                    buffered = _compute_obb_corners(x, y, heading, ws, ln, buffer_m)
                    if not _obb_inside_polygon(buffered, zone_m):
                        x += step
                        continue

                    fuselage = _compute_obb_corners(
                        x, y, heading,
                        ws * FUSELAGE_WIDTH_RATIO, ln * FUSELAGE_LENGTH_RATIO, 0)
                    wings = _compute_obb_corners(
                        x, y, heading,
                        ws * WING_SPAN_RATIO, ln * WING_CHORD_RATIO, 0)

                    collision = False
                    for parked in parked_obbs:
                        if (_obb_overlap(fuselage, parked["buffered"]) or
                            _obb_overlap(wings, parked["buffered"]) or
                            _obb_overlap(parked["fuselage"], buffered) or
                            _obb_overlap(parked["wings"], buffered)):
                            collision = True
                            break

                    if not collision:
                        lat, lng = _meters_to_latlng(x, y, ref_lat, ref_lng)
                        ac_candidates.append({
                            "x": x, "y": y,
                            "heading": heading,
                            "lat": lat, "lng": lng,
                        })
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


def _prune_candidates(candidates, zone_m, aircraft_list, max_total=200):
    """Spatially diverse pruning — farthest-point sampling.

    With OR-Tools (no variable limit), we can keep many more candidates
    than with Gurobi. Pruning is for quality, not license limits.
    """
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


def _build_conflict_graph(candidates, aircraft_list, buffer_m):
    """Build conflict pairs using spatial grid partitioning."""
    max_dim = 0
    for ac in aircraft_list:
        max_dim = max(max_dim, ac["wingspan_m"], ac["length_m"])
    cell_size = max_dim + buffer_m * 2 + 5

    grid = defaultdict(list)
    for ac_idx, cands in candidates.items():
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

                ws_a, ln_a = aircraft_list[ac_i]["wingspan_m"], aircraft_list[ac_i]["length_m"]
                ws_b, ln_b = aircraft_list[ac_j]["wingspan_m"], aircraft_list[ac_j]["length_m"]

                fuse_a = _compute_obb_corners(cand_a["x"], cand_a["y"], cand_a["heading"],
                    ws_a * FUSELAGE_WIDTH_RATIO, ln_a * FUSELAGE_LENGTH_RATIO, 0)
                wings_a = _compute_obb_corners(cand_a["x"], cand_a["y"], cand_a["heading"],
                    ws_a * WING_SPAN_RATIO, ln_a * WING_CHORD_RATIO, 0)
                buff_a = _compute_obb_corners(cand_a["x"], cand_a["y"], cand_a["heading"],
                    ws_a, ln_a, buffer_m)

                fuse_b = _compute_obb_corners(cand_b["x"], cand_b["y"], cand_b["heading"],
                    ws_b * FUSELAGE_WIDTH_RATIO, ln_b * FUSELAGE_LENGTH_RATIO, 0)
                wings_b = _compute_obb_corners(cand_b["x"], cand_b["y"], cand_b["heading"],
                    ws_b * WING_SPAN_RATIO, ln_b * WING_CHORD_RATIO, 0)
                buff_b = _compute_obb_corners(cand_b["x"], cand_b["y"], cand_b["heading"],
                    ws_b, ln_b, buffer_m)

                if (_obb_overlap(fuse_a, buff_b) or
                    _obb_overlap(wings_a, buff_b) or
                    _obb_overlap(fuse_b, buff_a) or
                    _obb_overlap(wings_b, buff_a)):
                    conflicts.add(pair_key)

    return list(conflicts)


def _build_parked_obbs(parked_aircraft, ref_lat, ref_lng, buffer_m):
    """Convert parked aircraft to OBB structures."""
    obbs = []
    for ac in (parked_aircraft or []):
        ws = ac.get("wingspan_m") or 0
        ln = ac.get("length_m") or 0
        if ws <= 0 or ln <= 0:
            continue
        px, py = _lat_lng_to_meters(ac["lat"], ac["lng"], ref_lat, ref_lng)
        hdg = ac.get("heading", 0)
        obbs.append({
            "fuselage": _compute_obb_corners(
                px, py, hdg, ws * FUSELAGE_WIDTH_RATIO, ln * FUSELAGE_LENGTH_RATIO, 0),
            "wings": _compute_obb_corners(
                px, py, hdg, ws * WING_SPAN_RATIO, ln * WING_CHORD_RATIO, 0),
            "buffered": _compute_obb_corners(px, py, hdg, ws, ln, buffer_m),
        })
    return obbs


# ═══════════════════════════════════════════════════════════════════════
# MODE 1: AUTOSTACK — OR-Tools CP-SAT selection
# ═══════════════════════════════════════════════════════════════════════

def _ortools_select(candidates, aircraft_list, conflicts, zone_area,
                    adg_weights=None, total_units=0):
    """Select best non-conflicting candidate set using OR-Tools CP-SAT.

    All binary, all linear. No variable limits. No solver time cap —
    the solver runs until OPTIMAL or natural termination.
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

    # Maximize placed count
    model.Maximize(sum(
        s[(ac_idx, j)]
        for ac_idx, cands in candidates.items()
        for j in range(len(cands))
    ))

    solver = cp_model.CpSolver()

    n_vars = sum(len(c) for c in candidates.values())
    t0 = time.perf_counter()
    status = solver.Solve(model)
    print(f"[solve] _ortools_select aircraft={len(aircraft_list)} n_vars={n_vars} conflicts={len(conflicts)} took={time.perf_counter()-t0:.3f}s status={solver.StatusName(status)}", flush=True)

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
                       adg_weights=None, total_units=0):
    """AUTOSTACK MODE: Two-phase optimizer.

    Phase 1: generate candidates (exact OBB validation)
    Phase 1.5: prune (farthest-point, generous limit)
    Phase 2: OR-Tools CP-SAT selects best non-conflicting set

    All placements guaranteed collision-free.
    """
    buffer_m = buffer_ft * FT_TO_M
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

    parked_obbs = _build_parked_obbs(parked_aircraft, ref_lat, ref_lng, buffer_m)

    # Phase 1: generate candidates
    candidates = _generate_candidates(
        zone_m, zone_bounds, to_place, parked_obbs,
        buffer_m, headings, ref_lat, ref_lng)

    # Phase 1.5: prune (generous — OR-Tools has no limits)
    candidates = _prune_candidates(candidates, zone_m, to_place, max_total=200)

    # Phase 2: conflict graph + OR-Tools selection
    conflicts = _build_conflict_graph(candidates, to_place, buffer_m)

    result = _ortools_select(
        candidates, to_place, conflicts, zone_area,
        adg_weights=adg_weights, total_units=total_units)
    result["heading_strategy"] = strategy_label
    return result


# ═══════════════════════════════════════════════════════════════════════
# MODE 1B: RAMP — Row-based layout
# ═══════════════════════════════════════════════════════════════════════

def _find_primary_axis(zone_m):
    """Find the zone's primary axis angle from its longest edge.

    Returns angle in degrees (0° = north/+y, clockwise).
    """
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
            # atan2(dx, dy) gives angle from +y axis clockwise
            best_angle = math.degrees(math.atan2(dx, dy)) % 360

    return best_angle


def _generate_ramp_grid_candidates(zone_m, zone_bounds, aircraft_list, parked_obbs,
                                   buffer_m, primary_axis_angle, ref_lat, ref_lng):
    """Generate row-column grid candidates with per-aircraft heading choice.

    Uses finer grid (0.5× footprint) for denser candidate coverage.
    Rows run ALONG the primary axis, aircraft face PERPENDICULAR.
    """
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

    for ac_idx, ac in enumerate(aircraft_list):
        ws = ac["wingspan_m"]
        ln = ac["length_m"]
        ac_candidates = []

        row_step = (ws + buffer_m) * 0.5
        col_step = (ln + buffer_m) * 0.5

        # Scan from zone bounds edge, not center
        for row_i in range(-20, 21):
            for col_i in range(-20, 21):
                px = min_x + (max_x - min_x) / 2 + row_i * row_step * row_dx + col_i * col_step * col_dx
                py = min_y + (max_y - min_y) / 2 + row_i * row_step * row_dy + col_i * col_step * col_dy

                for heading in [heading_in, heading_out]:
                    buffered = _compute_obb_corners(px, py, heading, ws, ln, buffer_m)
                    if not _obb_inside_polygon(buffered, zone_m):
                        continue

                    fuselage = _compute_obb_corners(
                        px, py, heading,
                        ws * FUSELAGE_WIDTH_RATIO, ln * FUSELAGE_LENGTH_RATIO, 0)
                    wings = _compute_obb_corners(
                        px, py, heading,
                        ws * WING_SPAN_RATIO, ln * WING_CHORD_RATIO, 0)

                    collision = False
                    for parked in parked_obbs:
                        if (_obb_overlap(fuselage, parked["buffered"]) or
                            _obb_overlap(wings, parked["buffered"]) or
                            _obb_overlap(parked["fuselage"], buffered) or
                            _obb_overlap(parked["wings"], buffered)):
                            collision = True
                            break

                    if not collision:
                        lat, lng = _meters_to_latlng(px, py, ref_lat, ref_lng)
                        ac_candidates.append({
                            "x": px, "y": py,
                            "heading": heading,
                            "lat": lat, "lng": lng,
                        })

        candidates[ac_idx] = ac_candidates

    return candidates


def optimize_placement_ramp(zone_coords, aircraft_to_place, parked_aircraft=None,
                            buffer_ft=SAFETY_BUFFER_FT,
                            strategy_label="optimal",
                            adg_weights=None, total_units=0):
    """RAMP MODE: Row-column grid with per-aircraft heading optimization.

    Two-phase: generate grid candidates (nose-in/out per slot) → OR-Tools
    selects best non-conflicting set. Same architecture as hangar mode,
    different candidate generation strategy.
    """
    buffer_m = buffer_ft * FT_TO_M
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

    parked_obbs = _build_parked_obbs(parked_aircraft, ref_lat, ref_lng, buffer_m)

    # Phase 1: Generate row-column grid candidates (nose-in + nose-out per slot)
    candidates = _generate_ramp_grid_candidates(
        zone_m, zone_bounds, to_place, parked_obbs,
        buffer_m, primary_axis, ref_lat, ref_lng)

    # Phase 1.5: Prune (same farthest-point as hangar)
    candidates = _prune_candidates(candidates, zone_m, to_place, max_total=200)

    # Phase 2: Conflict graph + OR-Tools selection (same as hangar)
    conflicts = _build_conflict_graph(candidates, to_place, buffer_m)

    result = _ortools_select(
        candidates, to_place, conflicts, zone_area,
        adg_weights=adg_weights, total_units=total_units)
    result["heading_strategy"] = strategy_label
    return result


RAMP_STRATEGIES = [
    ("Grid-optimized", None),
]


# ═══════════════════════════════════════════════════════════════════════
# MODE 2: CAPACITY — Greedy sequential fill
# ═══════════════════════════════════════════════════════════════════════

def _greedy_fill(zone_m, zone_bounds, existing_items, wingspan, length,
                 headings, buffer_m, max_count=50):
    """Greedily place as many aircraft of given size as possible.

    Uses _try_place_single() which checks exact OBB collision —
    same geometry engine as autostack mode.

    Args:
        zone_m: zone polygon in meter coords
        zone_bounds: (min_x, min_y, max_x, max_y)
        existing_items: list of { fuselage, wings, buffered } OBBs already placed
        wingspan, length: aircraft dimensions in meters
        headings: list of heading angles to try
        buffer_m: safety buffer in meters
        max_count: safety cap

    Returns:
        int — number of aircraft placed
    """
    placed = list(existing_items)
    count = 0

    while count < max_count:
        result = _try_place_single(
            zone_m, zone_bounds, placed,
            wingspan, length, headings, buffer_m)
        if result is None:
            break
        x, y, heading = result
        placed.append({
            "fuselage": _compute_obb_corners(
                x, y, heading,
                wingspan * FUSELAGE_WIDTH_RATIO,
                length * FUSELAGE_LENGTH_RATIO, 0),
            "wings": _compute_obb_corners(
                x, y, heading,
                wingspan * WING_SPAN_RATIO,
                length * WING_CHORD_RATIO, 0),
            "buffered": _compute_obb_corners(
                x, y, heading, wingspan, length, buffer_m),
        })
        count += 1

    return count


def _greedy_fill_with_positions(zone_m, zone_bounds, existing_items, wingspan, length,
                                headings, buffer_m, max_count, ref_lat, ref_lng,
                                adg_class=1):
    """Like _greedy_fill but returns positions for map preview.

    Returns list of { lat, lng, heading, wingspan_m, length_m, adg_class }.
    """
    placed = list(existing_items)
    positions = []
    n_headings = len(headings)

    while len(positions) < max_count:
        # Rotate heading list so each placement tries a different heading first
        rotation = len(positions) % n_headings if n_headings > 0 else 0
        rotated_headings = headings[rotation:] + headings[:rotation]

        result = _try_place_single(
            zone_m, zone_bounds, placed,
            wingspan, length, rotated_headings, buffer_m)
        if result is None:
            break
        x, y, heading = result
        placed.append({
            "fuselage": _compute_obb_corners(
                x, y, heading,
                wingspan * FUSELAGE_WIDTH_RATIO,
                length * FUSELAGE_LENGTH_RATIO, 0),
            "wings": _compute_obb_corners(
                x, y, heading,
                wingspan * WING_SPAN_RATIO,
                length * WING_CHORD_RATIO, 0),
            "buffered": _compute_obb_corners(
                x, y, heading, wingspan, length, buffer_m),
        })
        lat, lng = _meters_to_latlng(x, y, ref_lat, ref_lng)
        positions.append({
            "lat": lat, "lng": lng, "heading": heading,
            "wingspan_m": wingspan, "length_m": length,
            "adg_class": adg_class,
        })

    return positions


def _run_single_ramp_fill(zone_m, zone_bounds, existing_items, wingspan, length,
                          headings, buffer_m, primary_axis_angle,
                          col_edge, col_dir, row_edge, row_dir,
                          col_off, row_off, max_count,
                          return_positions=False, ref_lat=0, ref_lng=0, adg_class=1):
    """Single-strategy ramp fill from a specific edge/corner with optional offset.

    Uses axis-aligned x/y scanning (not direction vectors) for reliability.
    The primary_axis_angle only affects which headings are used, not scan direction.
    """
    min_x, min_y, max_x, max_y = zone_bounds

    # Grid step — 0.5× footprint balances coverage vs greedy quality
    x_step = (wingspan + buffer_m) * 0.5
    y_step = (length + buffer_m) * 0.5

    # Starting position and scan direction based on edge choice
    sx = (min_x + col_off * x_step) if col_edge == "min" else (max_x - col_off * x_step)
    sy = (min_y + row_off * y_step) if row_edge == "min" else (max_y - row_off * y_step)

    placed = list(existing_items)
    positions = []
    count = 0
    n_headings = len(headings)

    for col_i in range(40):
        for row_i in range(50):
            if count >= max_count:
                return positions if return_positions else count

            px = sx + col_i * x_step * col_dir
            py = sy + row_i * y_step * row_dir

            if not (min_x - 5 <= px <= max_x + 5 and min_y - 5 <= py <= max_y + 5):
                continue

            rotation = count % n_headings if n_headings > 0 else 0
            rotated = headings[rotation:] + headings[:rotation]

            for heading in rotated:
                buffered = _compute_obb_corners(px, py, heading, wingspan, length, buffer_m)
                if not _obb_inside_polygon(buffered, zone_m):
                    continue

                fuselage = _compute_obb_corners(
                    px, py, heading,
                    wingspan * FUSELAGE_WIDTH_RATIO, length * FUSELAGE_LENGTH_RATIO, 0)
                wings = _compute_obb_corners(
                    px, py, heading,
                    wingspan * WING_SPAN_RATIO, length * WING_CHORD_RATIO, 0)

                collision = False
                for existing in placed:
                    if (_obb_overlap(fuselage, existing["buffered"]) or
                        _obb_overlap(wings, existing["buffered"]) or
                        _obb_overlap(existing["fuselage"], buffered) or
                        _obb_overlap(existing["wings"], buffered)):
                        collision = True
                        break

                if not collision:
                    placed.append({"fuselage": fuselage, "wings": wings, "buffered": buffered})
                    count += 1
                    if return_positions:
                        lat, lng = _meters_to_latlng(px, py, ref_lat, ref_lng)
                        positions.append({
                            "lat": lat, "lng": lng, "heading": heading,
                            "wingspan_m": wingspan, "length_m": length,
                            "adg_class": adg_class,
                        })
                    break

    return positions if return_positions else count


RAMP_SCAN_STRATEGIES = [
    # 4 corners × 4 offsets (none, quarter, half, three-quarter) = 16 strategies
    ("min", +1, "min", +1, 0.0, 0.0),
    ("min", +1, "min", +1, 0.25, 0.0),
    ("min", +1, "min", +1, 0.0, 0.25),
    ("min", +1, "min", +1, 0.25, 0.25),
    ("max", -1, "min", +1, 0.0, 0.0),
    ("max", -1, "min", +1, 0.25, 0.25),
    ("min", +1, "max", -1, 0.0, 0.0),
    ("min", +1, "max", -1, 0.25, 0.25),
    ("max", -1, "max", -1, 0.0, 0.0),
    ("max", -1, "max", -1, 0.25, 0.25),
    # Half-step offsets
    ("min", +1, "min", +1, 0.5, 0.0),
    ("min", +1, "min", +1, 0.0, 0.5),
    ("min", +1, "min", +1, 0.5, 0.5),
    ("max", -1, "max", -1, 0.5, 0.5),
    ("max", -1, "min", +1, 0.5, 0.0),
    ("min", +1, "max", -1, 0.0, 0.5),
]


def _capacity_fill_ramp_ortools(zone_m, zone_bounds, existing_items, wingspan, length,
                                headings, buffer_m, primary_axis_angle, max_count,
                                ref_lat=0, ref_lng=0, return_positions=False):
    """Dense candidate generation → OR-Tools selection for ramp capacity.

    Unlike greedy fill, this finds globally optimal placement by:
    1. Generating a dense grid of candidate positions (multiple offsets)
    2. Building a conflict graph using exact OBB collision (body vs buffer)
    3. OR-Tools CP-SAT selects the maximum non-conflicting set

    This allows buffer-buffer overlap, finding tighter packings than greedy.
    """
    min_x, min_y, max_x, max_y = zone_bounds

    # Dense candidate generation with multiple offsets
    x_step = (wingspan + buffer_m) * 0.4
    y_step = (length + buffer_m) * 0.4

    all_candidates = []
    seen_positions = set()

    for x_off_frac in [0.0, 0.2, 0.5]:
        for y_off_frac in [0.0, 0.2, 0.5]:
            x = min_x + wingspan / 2 + x_off_frac * x_step
            while x <= max_x - wingspan / 2:
                y = min_y + length / 2 + y_off_frac * y_step
                while y <= max_y - length / 2:
                    # Snap to avoid near-duplicates
                    snap_key = (round(x, 1), round(y, 1))
                    if snap_key in seen_positions:
                        y += y_step
                        continue
                    seen_positions.add(snap_key)

                    for heading in headings:
                        buffered = _compute_obb_corners(x, y, heading, wingspan, length, buffer_m)
                        if not _obb_inside_polygon(buffered, zone_m):
                            continue

                        # Check collision with existing (parked) aircraft
                        fuselage = _compute_obb_corners(
                            x, y, heading,
                            wingspan * FUSELAGE_WIDTH_RATIO, length * FUSELAGE_LENGTH_RATIO, 0)
                        wings = _compute_obb_corners(
                            x, y, heading,
                            wingspan * WING_SPAN_RATIO, length * WING_CHORD_RATIO, 0)

                        collision = False
                        for existing in existing_items:
                            if (_obb_overlap(fuselage, existing["buffered"]) or
                                _obb_overlap(wings, existing["buffered"]) or
                                _obb_overlap(existing["fuselage"], buffered) or
                                _obb_overlap(existing["wings"], buffered)):
                                collision = True
                                break

                        if not collision:
                            all_candidates.append({
                                "x": x, "y": y, "heading": heading,
                                "fuselage": fuselage, "wings": wings, "buffered": buffered,
                            })

                    y += y_step
                x += x_step

    if not all_candidates:
        return [] if return_positions else 0

    # Build conflict graph between candidates
    n = len(all_candidates)
    conflicts = []
    # Spatial bucket for efficiency
    bucket_size = max(wingspan, length) + buffer_m * 2 + 5
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

                a = all_candidates[a_idx]
                b = all_candidates[b_idx]

                if (_obb_overlap(a["fuselage"], b["buffered"]) or
                    _obb_overlap(a["wings"], b["buffered"]) or
                    _obb_overlap(b["fuselage"], a["buffered"]) or
                    _obb_overlap(b["wings"], a["buffered"])):
                    conflicts.append((a_idx, b_idx))

    # OR-Tools CP-SAT: select max non-conflicting set
    model = cp_model.CpModel()
    s = [model.NewBoolVar(f"c_{i}") for i in range(n)]

    for a_idx, b_idx in conflicts:
        model.AddAtMostOne([s[a_idx], s[b_idx]])

    # Cap at max_count
    model.Add(sum(s) <= max_count)
    model.Maximize(sum(s))

    solver = cp_model.CpSolver()
    t0 = time.perf_counter()
    status = solver.Solve(model)
    print(f"[solve] _capacity_fill_ramp_ortools ws={wingspan:.1f}m ln={length:.1f}m n_vars={n} conflicts={len(conflicts)} took={time.perf_counter()-t0:.3f}s status={solver.StatusName(status)}", flush=True)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return [] if return_positions else 0

    count = 0
    positions = []
    for i in range(n):
        if solver.Value(s[i]) == 1:
            count += 1
            if return_positions:
                c = all_candidates[i]
                lat, lng = _meters_to_latlng(c["x"], c["y"], ref_lat, ref_lng)
                positions.append({
                    "lat": lat, "lng": lng, "heading": c["heading"],
                    "wingspan_m": wingspan, "length_m": length,
                    "adg_class": 1,
                })

    return positions if return_positions else count


def _greedy_fill_ramp(zone_m, zone_bounds, existing_items, wingspan, length,
                      headings, buffer_m, primary_axis_angle, max_count=50):
    """OR-Tools based ramp fill for maximum packing. Allows buffer overlap."""
    return _capacity_fill_ramp_ortools(
        zone_m, zone_bounds, existing_items, wingspan, length,
        headings, buffer_m, primary_axis_angle, max_count)


def _greedy_fill_ramp_with_positions(zone_m, zone_bounds, existing_items, wingspan, length,
                                     headings, buffer_m, primary_axis_angle,
                                     max_count, ref_lat, ref_lng, adg_class=1):
    """OR-Tools based ramp fill returning positions for ghost preview."""
    return _capacity_fill_ramp_ortools(
        zone_m, zone_bounds, existing_items, wingspan, length,
        headings, buffer_m, primary_axis_angle, max_count,
        ref_lat=ref_lat, ref_lng=ref_lng, return_positions=True)


def compute_zone_capacity_units(zone_coords, adg_dims, buffer_ft=SAFETY_BUFFER_FT,
                                parking_mode="hangar"):
    """CAPACITY MODE: Compute zone capacity in ADG-I equivalent units.

    For each ADG class, runs greedy fill on an empty zone to find max count.
    Uses ADG-I as the reference unit.

    Does NOT consider currently parked aircraft — this is the theoretical
    empty-zone max, intended to be cached. For remaining-capacity with
    parked obstacles, use optimize_capacity().

    parking_mode: "hangar" uses all 6 headings, "ramp" uses perpendicular to primary axis.
    """
    if not zone_coords or len(zone_coords) < 3:
        return {"total_units": 0, "max_by_adg": {}, "adg_weights": {}}

    buffer_m = buffer_ft * FT_TO_M
    ref_lat, ref_lng = _polygon_centroid(zone_coords)
    zone_m = [_lat_lng_to_meters(c[0], c[1], ref_lat, ref_lng) for c in zone_coords]
    zone_bounds = _polygon_bounds(zone_m)

    # Choose headings based on parking mode
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
                ws, ln, headings, buffer_m, primary_axis,
                max_count=50)
        else:
            max_by_adg[adg_class] = _greedy_fill(
                zone_m, zone_bounds, [],
                ws, ln, headings, buffer_m,
                max_count=50)
    print(f"[capacity] compute_zone_capacity_units mode={parking_mode} buffer_ft={buffer_ft} adg_classes={len(adg_dims)} took={time.perf_counter()-t_loop:.3f}s max_by_adg={max_by_adg}", flush=True)

    # ADG-I is reference unit
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
                      buffer_ft=SAFETY_BUFFER_FT, parking_mode="hangar"):
    """CAPACITY MODE: Estimate remaining capacity per ADG class.

    Greedy fill with existing parked aircraft as obstacles.
    Same OBB collision model as autostack mode.
    Uses mode-aware headings (ramp = perpendicular to primary axis).
    """
    if not zone_coords or len(zone_coords) < 3:
        return {cls: 0 for cls in adg_representative_dims}

    buffer_m = buffer_ft * FT_TO_M
    ref_lat, ref_lng = _polygon_centroid(zone_coords)
    zone_m = [_lat_lng_to_meters(c[0], c[1], ref_lat, ref_lng) for c in zone_coords]
    zone_bounds = _polygon_bounds(zone_m)

    # Mode-aware headings
    if parking_mode == "ramp":
        primary_axis = _find_primary_axis(zone_m)
        headings = [(primary_axis + 90) % 360, (primary_axis + 270) % 360]
    else:
        headings = OPERATIONAL_HEADINGS

    # Build OBBs for parked aircraft
    parked_obbs = _build_parked_obbs(parked_aircraft, ref_lat, ref_lng, buffer_m)

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
                ws, ln, headings, buffer_m, primary_axis,
                max_count=50)
        else:
            result[adg_class] = _greedy_fill(
                zone_m, zone_bounds, parked_obbs,
                ws, ln, headings, buffer_m,
                max_count=50)

    return result
