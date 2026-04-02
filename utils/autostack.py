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
    SAFETY_BUFFER_M,
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


def autostack(zone_coords, aircraft_list, buffer_m=SAFETY_BUFFER_M,
              headings_to_try=None, num_options=3):
    """
    Compute optimal placement for aircraft within a zone.

    Args:
        zone_coords: [[lat, lng], ...] zone polygon
        aircraft_list: [{ wingspan_m, length_m, tail_number, adg_class }, ...]
            Sorted by priority (largest first recommended)
        buffer_m: safety distance in meters
        headings_to_try: list of headings to attempt (degrees), default [0, 90, 180, 270]
        num_options: number of layout options to generate

    Returns:
        list of layout options, each:
        {
            "utilization": float (0-100),
            "placements": [{ tail_number, lat, lng, heading, wingspan_m, length_m }, ...],
            "unplaced": [tail_numbers that didn't fit],
        }
    """
    if not zone_coords or len(zone_coords) < 3:
        return []
    if not aircraft_list:
        return [{"utilization": 0, "placements": [], "unplaced": []}]

    if headings_to_try is None:
        headings_to_try = [0, 90, 180, 270]

    ref_lat, ref_lng = _polygon_centroid(zone_coords)

    # Convert zone to meters
    zone_m = [_lat_lng_to_meters(c[0], c[1], ref_lat, ref_lng) for c in zone_coords]
    min_x, min_y, max_x, max_y = _polygon_bounds(zone_m)

    # Zone area for utilization calculation
    zone_area = _shoelace_area(zone_m)

    # Filter out aircraft without real dimensions
    valid_aircraft = [
        a for a in aircraft_list
        if a.get("wingspan_m") and a.get("length_m")
    ]

    # Sort aircraft largest first (by area = wingspan * length)
    sorted_aircraft = sorted(
        valid_aircraft,
        key=lambda a: a["wingspan_m"] * a["length_m"],
        reverse=True,
    )

    options = []

    # Generate multiple options with different heading strategies
    heading_strategies = []
    for primary_heading in headings_to_try[:num_options]:
        heading_strategies.append([primary_heading])
    # Also try mixed headings
    if num_options > len(headings_to_try):
        heading_strategies.append(headings_to_try)

    for strategy in heading_strategies[:num_options]:
        placements = []
        placed_items = []  # list of { body, buffered } OBBs
        unplaced = []
        total_aircraft_area = 0

        for ac in sorted_aircraft:
            ws, ln = ac["wingspan_m"], ac["length_m"]
            placed = False

            # Try each heading in this strategy
            for heading in (strategy if len(strategy) > 1 else strategy * 4):
                if placed:
                    break

                # Grid step: use smaller of wingspan/length for finer resolution
                step = min(ws, ln) * 0.6

                # Scan grid within zone bounds
                y = min_y + ln / 2 + buffer_m
                while y <= max_y - ln / 2 - buffer_m:
                    if placed:
                        break
                    x = min_x + ws / 2 + buffer_m
                    while x <= max_x - ws / 2 - buffer_m:
                        candidate_fuselage = _compute_obb_corners(x, y, heading, ws * FUSELAGE_WIDTH_RATIO, ln * FUSELAGE_LENGTH_RATIO, 0)
                        candidate_wings = _compute_obb_corners(x, y, heading, ws * WING_SPAN_RATIO, ln * WING_CHORD_RATIO, 0)
                        candidate_buffered = _compute_obb_corners(x, y, heading, ws, ln, buffer_m)

                        # Check inside zone (buffered OBB must fit)
                        if not _obb_inside_polygon(candidate_buffered, zone_m):
                            x += step
                            continue

                        # Check collision: fuselage or wings enters other's buffer or vice versa
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
                            lat, lng = _meters_to_latlng(x, y, ref_lat, ref_lng)
                            placements.append({
                                "tail_number": ac.get("tail_number", ""),
                                "lat": lat,
                                "lng": lng,
                                "heading": heading,
                                "wingspan_m": ws,
                                "length_m": ln,
                                "adg_class": ac.get("adg_class", 2),
                            })
                            placed_items.append({
                                "fuselage": candidate_fuselage,
                                "wings": candidate_wings,
                                "buffered": candidate_buffered,
                            })
                            total_aircraft_area += ws * ln
                            placed = True
                            break

                        x += step
                    y += step

            if not placed:
                unplaced.append(ac.get("tail_number", "unknown"))

        utilization = (total_aircraft_area / zone_area * 100) if zone_area > 0 else 0

        options.append({
            "utilization": round(utilization, 1),
            "placements": placements,
            "unplaced": unplaced,
            "heading_strategy": strategy[0] if len(strategy) == 1 else "mixed",
        })

    # Sort by utilization (best first), then by fewest unplaced
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
