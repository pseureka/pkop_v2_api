"""
AutoStack — automated aircraft placement within zones.

Given a zone polygon and a list of aircraft to place, computes optimal
positions and headings to maximize space utilization while maintaining
safety clearances.

Algorithm: grid-based placement with oriented bounding box collision
detection. Tries multiple headings and positions, ranks by utilization.
"""

import math
from .collision import (
    _lat_lng_to_meters,
    _compute_obb_corners,
    _obb_overlap,
    _point_in_polygon,
    SAFETY_BUFFER_FT,
    SAFETY_BUFFER_M,
    FT_TO_M,
    WING_SPAN_RATIO,
    WING_CHORD_RATIO,
    FUSELAGE_WIDTH_RATIO,
    FUSELAGE_LENGTH_RATIO,
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
                      headings, buffer_m):
    """Try to place a single aircraft in the zone using grid scan.

    Args:
        zone_m: zone polygon in meter coords
        zone_bounds: (min_x, min_y, max_x, max_y)
        placed_items: list of existing { fuselage, wings, buffered } OBBs
        wingspan: aircraft wingspan in meters
        length: aircraft length in meters
        headings: list of heading angles to try (degrees)
        buffer_m: safety buffer in meters

    Returns:
        (x, y, heading) if placed, or None
    """
    min_x, min_y, max_x, max_y = zone_bounds
    step = min(wingspan, length) * 0.6

    # Position-first, heading-second: ensures varied headings across placements
    y = min_y + length / 2 + buffer_m
    while y <= max_y - length / 2 - buffer_m:
        x = min_x + wingspan / 2 + buffer_m
        while x <= max_x - wingspan / 2 - buffer_m:
            for heading in headings:
                candidate_fuselage = _compute_obb_corners(
                    x, y, heading,
                    wingspan * FUSELAGE_WIDTH_RATIO,
                    length * FUSELAGE_LENGTH_RATIO, 0)
                candidate_wings = _compute_obb_corners(
                    x, y, heading,
                    wingspan * WING_SPAN_RATIO,
                    length * WING_CHORD_RATIO, 0)
                candidate_buffered = _compute_obb_corners(
                    x, y, heading, wingspan, length, buffer_m)

                if not _obb_inside_polygon(candidate_buffered, zone_m):
                    continue

                collision = False
                for existing in placed_items:
                    if (
                        _obb_overlap(candidate_fuselage, existing["buffered"]) or
                        _obb_overlap(candidate_wings, existing["buffered"]) or
                        _obb_overlap(existing["fuselage"], candidate_buffered) or
                        _obb_overlap(existing["wings"], candidate_buffered)
                    ):
                        collision = True
                        break

                if not collision:
                    return x, y, heading

            x += step
        y += step

    return None


def autostack(zone_coords, aircraft_list, buffer_ft=SAFETY_BUFFER_FT,
              headings_to_try=None, num_options=3, adg_dims=None,
              parking_mode="hangar"):
    """
    Compute optimal placement for aircraft within a zone.

    Two modes:
      - "hangar": OR-Tools CP-SAT optimization (tight packing)
      - "ramp": Row-based layout along primary axis (organized rows)

    Args:
        zone_coords: [[lat, lng], ...] zone polygon
        aircraft_list: [{ wingspan_m, length_m, tail_number, adg_class }, ...]
        buffer_ft: safety distance in feet
        headings_to_try: list of headings to attempt (degrees)
        num_options: number of layout options to generate
        parking_mode: "hangar" or "ramp"

    Returns:
        list of layout options
    """
    from .optimizer import optimize_placement, optimize_placement_ramp

    if not zone_coords or len(zone_coords) < 3:
        return []
    if not aircraft_list:
        return [{"utilization": 0, "placements": [], "unplaced": []}]

    from .optimizer import HEADING_STRATEGIES, RAMP_STRATEGIES, compute_zone_capacity_units

    # Pre-compute capacity units once (shared across all strategy solves)
    # Use ADG dims from DB if provided, otherwise build from aircraft list
    if adg_dims is None:
        adg_dims = {}
        for ac in aircraft_list:
            cls = ac.get("adg_class", 2)
            ws = ac.get("wingspan_m", 0)
            ln = ac.get("length_m", 0)
            if ws > 0 and ln > 0 and cls not in adg_dims:
                adg_dims[cls] = {"wingspan_m": ws, "length_m": ln}

    cap = compute_zone_capacity_units(zone_coords, adg_dims, buffer_ft)

    options = []

    if parking_mode == "ramp":
        # Ramp mode: row-based layout strategies
        for label, _ in RAMP_STRATEGIES[:num_options]:
            result = optimize_placement_ramp(
                zone_coords, aircraft_list, parked_aircraft=None,
                buffer_ft=buffer_ft, strategy_label=label,
                adg_weights=cap["adg_weights"], total_units=cap["total_units"],
            )
            options.append(result)
    else:
        # Hangar mode: OR-Tools CP-SAT optimization
        for label, headings in HEADING_STRATEGIES[:num_options]:
            result = optimize_placement(
                zone_coords, aircraft_list, parked_aircraft=None,
                buffer_ft=buffer_ft, headings=headings,
                time_limit=5, strategy_label=label,
                adg_weights=cap["adg_weights"], total_units=cap["total_units"],
            )
            options.append(result)

    # Sort by most placed, then highest utilization
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
