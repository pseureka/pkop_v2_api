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
                    adg_weights=None, total_units=0, time_limit=10):
    """Select best non-conflicting candidate set using OR-Tools CP-SAT.

    All binary, all linear. No variable limits.
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
    solver.parameters.max_time_in_seconds = time_limit

    status = solver.Solve(model)

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
                       buffer_ft=SAFETY_BUFFER_FT, headings=None, time_limit=10,
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
        adg_weights=adg_weights, total_units=total_units,
        time_limit=time_limit)
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


def _generate_row_layout(zone_m, zone_bounds, aircraft_list, buffer_m,
                         primary_axis_angle, ref_lat, ref_lng):
    """Generate row-based placements for ramp mode.

    Aircraft are placed in parallel rows perpendicular to the primary axis.
    Each row contains aircraft of similar size, all facing the same heading.

    Args:
        zone_m: zone polygon in meter coords
        zone_bounds: (min_x, min_y, max_x, max_y)
        aircraft_list: sorted by size (largest first)
        buffer_m: safety buffer in meters
        primary_axis_angle: degrees, direction of rows
        ref_lat, ref_lng: reference for lat/lng conversion

    Returns:
        list of { tail_number, lat, lng, heading, wingspan_m, length_m, adg_class }
    """
    min_x, min_y, max_x, max_y = zone_bounds

    # Heading for aircraft: face along the primary axis
    heading = primary_axis_angle
    rad = math.radians(heading)
    cos_h = math.cos(rad)
    sin_h = math.sin(rad)

    # Row direction: perpendicular to primary axis
    # Row axis = primary_axis + 90°
    row_dx = cos_h   # perpendicular direction x
    row_dy = -sin_h  # perpendicular direction y

    # Along-row direction (same as primary axis)
    along_dx = sin_h
    along_dy = cos_h

    # Sort aircraft largest first by wingspan
    sorted_ac = sorted(aircraft_list,
                       key=lambda a: a["wingspan_m"] * a["length_m"],
                       reverse=True)

    # Compute zone center as starting reference
    cx = sum(p[0] for p in zone_m) / len(zone_m)
    cy = sum(p[1] for p in zone_m) / len(zone_m)

    placements = []
    placed_obbs = []

    # Place aircraft in rows, offsetting perpendicular to the primary axis
    row_offset = 0  # offset from center in perpendicular direction
    row_sign = 1    # alternate sides: +1, -1, +2, -2, ...
    row_step_count = 0

    ac_remaining = list(sorted_ac)

    while ac_remaining:
        # Current row position (perpendicular offset from center)
        row_center_x = cx + row_offset * row_sign * row_dx
        row_center_y = cy + row_offset * row_sign * row_dy

        # Try to place each remaining aircraft along this row
        placed_in_row = []
        still_remaining = []

        # Scan along the row direction
        for ac in ac_remaining:
            ws = ac["wingspan_m"]
            ln = ac["length_m"]

            # Try positions along the row
            placed = False
            for along_step in range(-15, 16):
                step_size = (ln + buffer_m) * 0.8
                px = row_center_x + along_step * step_size * along_dx
                py = row_center_y + along_step * step_size * along_dy

                # Check inside zone
                buffered = _compute_obb_corners(px, py, heading, ws, ln, buffer_m)
                if not _obb_inside_polygon(buffered, zone_m):
                    continue

                # Check collision with already placed
                fuselage = _compute_obb_corners(
                    px, py, heading,
                    ws * FUSELAGE_WIDTH_RATIO, ln * FUSELAGE_LENGTH_RATIO, 0)
                wings = _compute_obb_corners(
                    px, py, heading,
                    ws * WING_SPAN_RATIO, ln * WING_CHORD_RATIO, 0)

                collision = False
                for existing in placed_obbs:
                    if (_obb_overlap(fuselage, existing["buffered"]) or
                        _obb_overlap(wings, existing["buffered"]) or
                        _obb_overlap(existing["fuselage"], buffered) or
                        _obb_overlap(existing["wings"], buffered)):
                        collision = True
                        break

                if not collision:
                    lat, lng = _meters_to_latlng(px, py, ref_lat, ref_lng)
                    placements.append({
                        "tail_number": ac.get("tail_number", ""),
                        "lat": lat, "lng": lng,
                        "heading": heading,
                        "wingspan_m": ws, "length_m": ln,
                        "adg_class": ac.get("adg_class", 2),
                    })
                    placed_obbs.append({
                        "fuselage": fuselage,
                        "wings": wings,
                        "buffered": buffered,
                    })
                    placed_in_row.append(ac)
                    placed = True
                    break

            if not placed:
                still_remaining.append(ac)

        ac_remaining = still_remaining

        # Move to next row
        row_step_count += 1
        if row_step_count % 2 == 0:
            row_offset += max(
                max((a["wingspan_m"] for a in sorted_ac), default=20) + buffer_m,
                20
            )
        row_sign *= -1

        # Safety: don't loop forever
        if row_offset > max(max_x - min_x, max_y - min_y):
            break

    return placements


def optimize_placement_ramp(zone_coords, aircraft_to_place, parked_aircraft=None,
                            buffer_ft=SAFETY_BUFFER_FT, time_limit=10,
                            strategy_label="optimal",
                            adg_weights=None, total_units=0):
    """RAMP MODE: Row-based layout along zone's primary axis.

    Places aircraft in organized rows matching real ramp operations.
    Uses same OBB collision engine as hangar mode.
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

    # Determine heading based on strategy
    if strategy_label == "Nose-in":
        heading_angle = primary_axis
    elif strategy_label == "Angled":
        heading_angle = (primary_axis + 60) % 360
    else:
        heading_angle = primary_axis

    placements = _generate_row_layout(
        zone_m, zone_bounds, to_place, buffer_m,
        heading_angle, ref_lat, ref_lng)

    placed_tails = {p["tail_number"] for p in placements}
    unplaced = [a.get("tail_number", "unknown") for a in to_place
                if a.get("tail_number", "") not in placed_tails]

    consumed_units = sum(
        (adg_weights or {}).get(p.get("adg_class", 2), 1)
        for p in placements
    )
    util = (consumed_units / total_units * 100) if total_units > 0 else 0

    return {
        "placements": placements,
        "unplaced": unplaced,
        "utilization": round(util, 1),
        "heading_strategy": strategy_label,
    }


RAMP_STRATEGIES = [
    ("Nose-in", None),
    ("Angled", None),
    ("Mixed", None),
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


def compute_zone_capacity_units(zone_coords, adg_dims, buffer_ft=SAFETY_BUFFER_FT):
    """CAPACITY MODE: Compute zone capacity in ADG-I equivalent units.

    For each ADG class, runs greedy fill on an empty zone to find max count.
    Uses ADG-I as the reference unit.

    Same OBB collision model as autostack mode — same geometry truth.
    """
    if not zone_coords or len(zone_coords) < 3:
        return {"total_units": 0, "max_by_adg": {}, "adg_weights": {}}

    buffer_m = buffer_ft * FT_TO_M
    ref_lat, ref_lng = _polygon_centroid(zone_coords)
    zone_m = [_lat_lng_to_meters(c[0], c[1], ref_lat, ref_lng) for c in zone_coords]
    zone_bounds = _polygon_bounds(zone_m)

    max_by_adg = {}
    for adg_class, dims in adg_dims.items():
        ws = dims["wingspan_m"]
        ln = dims["length_m"]
        if ws <= 0 or ln <= 0:
            max_by_adg[adg_class] = 0
            continue

        max_by_adg[adg_class] = _greedy_fill(
            zone_m, zone_bounds, [],  # empty zone
            ws, ln, OPERATIONAL_HEADINGS, buffer_m,
            max_count=50)

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
                      buffer_ft=SAFETY_BUFFER_FT):
    """CAPACITY MODE: Estimate remaining capacity per ADG class.

    Greedy fill with existing parked aircraft as obstacles.
    Same OBB collision model as autostack mode.
    """
    if not zone_coords or len(zone_coords) < 3:
        return {cls: 0 for cls in adg_representative_dims}

    buffer_m = buffer_ft * FT_TO_M
    ref_lat, ref_lng = _polygon_centroid(zone_coords)
    zone_m = [_lat_lng_to_meters(c[0], c[1], ref_lat, ref_lng) for c in zone_coords]
    zone_bounds = _polygon_bounds(zone_m)

    # Build OBBs for parked aircraft
    parked_obbs = _build_parked_obbs(parked_aircraft, ref_lat, ref_lng, buffer_m)

    result = {}
    for adg_class, dims in adg_representative_dims.items():
        ws = dims["wingspan_m"]
        ln = dims["length_m"]
        if ws <= 0 or ln <= 0:
            result[adg_class] = 0
            continue

        result[adg_class] = _greedy_fill(
            zone_m, zone_bounds, parked_obbs,
            ws, ln, OPERATIONAL_HEADINGS, buffer_m,
            max_count=50)

    return result
