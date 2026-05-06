"""
AutoStack — automated aircraft placement within zones.

Given a zone polygon and a list of aircraft to place, computes optimal
positions and headings to maximize space utilization while respecting the
component-aware ClearancePolicy.
"""

import math
from .clearance import ClearancePolicy
from .collision import (
    _lat_lng_to_meters,
    _obb_overlap,
    _point_in_polygon,
    aircraft_obbs_collide,
    build_aircraft_obbs,
    SAFETY_BUFFER_FT,
    SAFETY_BUFFER_M,
    FT_TO_M,
    get_effective_dimensions,
)


def _polygon_bounds(coords_m):
    """Get axis-aligned bounding box of polygon in meters."""
    xs = [c[0] for c in coords_m]
    ys = [c[1] for c in coords_m]
    return min(xs), min(ys), max(xs), max(ys)


def _polygon_centroid(coords):
    """Centroid of lat/lng polygon."""
    n = len(coords)
    lat = sum(c[0] for c in coords) / n
    lng = sum(c[1] for c in coords) / n
    return lat, lng


def _meters_to_latlng(x, y, ref_lat, ref_lng):
    """Convert meter offsets back to lat/lng."""
    lat = ref_lat + y / 111320.0
    lng = ref_lng + x / (111320.0 * math.cos(math.radians(ref_lat)))
    return lat, lng


def _obb_inside_polygon(corners, polygon):
    """Check all OBB corners inside polygon."""
    return all(_point_in_polygon(c[0], c[1], polygon) for c in corners)


def _try_place_single(zone_m, zone_bounds, placed_items, wingspan, length,
                      headings, policy):
    """Try to place a single aircraft in the zone using grid scan.

    `placed_items` are 4-shape OBB dicts (built via `build_aircraft_obbs`).
    Returns (x, y, heading) if a non-colliding position fits, else None.
    """
    min_x, min_y, max_x, max_y = zone_bounds
    step = min(wingspan, length) * 0.6
    boundary_m = max(policy.boundary_lateral_m, policy.boundary_longitudinal_m)

    y = min_y + length / 2 + boundary_m
    while y <= max_y - length / 2 - boundary_m:
        x = min_x + wingspan / 2 + boundary_m
        while x <= max_x - wingspan / 2 - boundary_m:
            for heading in headings:
                cand_obbs = build_aircraft_obbs(x, y, heading, wingspan, length, policy)

                if not (
                    _obb_inside_polygon(cand_obbs["fuselage_buffered"], zone_m) and
                    _obb_inside_polygon(cand_obbs["wings_buffered"], zone_m)
                ):
                    continue

                collision = False
                for existing in placed_items:
                    if aircraft_obbs_collide(cand_obbs, existing):
                        collision = True
                        break

                if not collision:
                    return x, y, heading

            x += step
        y += step

    return None


def autostack(zone_coords, aircraft_list, buffer_ft=SAFETY_BUFFER_FT,
              headings_to_try=None, num_options=3, adg_dims=None,
              parking_mode="hangar", cap=None, policy=None):
    """
    Compute optimal placement for aircraft within a zone.

    Two modes:
      - "hangar": OR-Tools CP-SAT optimization (tight packing)
      - "ramp": Row-based layout along primary axis (organized rows)

    Pass `policy` directly, or `buffer_ft` for the legacy uniform-scale shim.
    """
    from .optimizer import optimize_placement, optimize_placement_ramp

    if policy is None:
        policy = ClearancePolicy.from_buffer_ft(buffer_ft)

    if not zone_coords or len(zone_coords) < 3:
        return []
    if not aircraft_list:
        return [{"utilization": 0, "placements": [], "unplaced": []}]

    from .optimizer import HEADING_STRATEGIES, RAMP_STRATEGIES, compute_zone_capacity_units

    if adg_dims is None:
        adg_dims = {}
        for ac in aircraft_list:
            cls = ac.get("adg_class", 2)
            ws = ac.get("wingspan_m", 0)
            ln = ac.get("length_m", 0)
            if ws > 0 and ln > 0 and cls not in adg_dims:
                adg_dims[cls] = {"wingspan_m": ws, "length_m": ln}

    if cap is None:
        cap = compute_zone_capacity_units(zone_coords, adg_dims, parking_mode=parking_mode, policy=policy)
    else:
        print(f"[memo] autostack reused pre-computed cap (skipped compute_zone_capacity_units)", flush=True)

    options = []

    if parking_mode == "ramp":
        for label, _ in RAMP_STRATEGIES[:num_options]:
            result = optimize_placement_ramp(
                zone_coords, aircraft_list, parked_aircraft=None,
                strategy_label=label, policy=policy,
                adg_weights=cap["adg_weights"], total_units=cap["total_units"],
            )
            options.append(result)
    else:
        for label, headings in HEADING_STRATEGIES[:num_options]:
            result = optimize_placement(
                zone_coords, aircraft_list, parked_aircraft=None,
                headings=headings, strategy_label=label, policy=policy,
                adg_weights=cap["adg_weights"], total_units=cap["total_units"],
            )
            options.append(result)

    options.sort(key=lambda o: (-len(o["placements"]), -o["utilization"]))

    return options[:num_options]


def _shoelace_area(polygon):
    """Compute area of polygon using shoelace formula (in sq meters)."""
    n = len(polygon)
    area = 0
    for i in range(n):
        j = (i + 1) % n
        area += polygon[i][0] * polygon[j][1]
        area -= polygon[j][0] * polygon[i][1]
    return abs(area) / 2
