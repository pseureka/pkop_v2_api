"""
OBB-based collision detection for aircraft placement using Separating Axis Theorem.

Inspired by swarm drone landing algorithms — each aircraft is modeled as an
oriented bounding box (OBB) defined by position, heading, wingspan, and length,
plus a configurable safety buffer.
"""

import math

# Default safety distance in meters between aircraft
SAFETY_BUFFER_M = 5.0

# Cross-shaped collision body ratios — matched to SVG with preserveAspectRatio="none".
WING_SPAN_RATIO = 0.94
WING_CHORD_RATIO = 0.175
FUSELAGE_WIDTH_RATIO = 0.16
FUSELAGE_LENGTH_RATIO = 0.98


def get_effective_dimensions(wingspan_m, length_m, adg_class=None):
    """Get effective dimensions. Both wingspan_m and length_m are required.
    Returns (None, None) if either is missing."""
    if not wingspan_m or not length_m:
        return None, None
    return wingspan_m, length_m


def _lat_lng_to_meters(lat, lng, ref_lat, ref_lng):
    """Convert lat/lng to local meter offsets from a reference point.
    Uses tangent plane approximation — accurate within a few hundred meters."""
    dy = (lat - ref_lat) * 111320.0
    dx = (lng - ref_lng) * 111320.0 * math.cos(math.radians(ref_lat))
    return dx, dy


def _compute_obb_corners(cx, cy, heading_deg, wingspan, length, buffer=SAFETY_BUFFER_M):
    """Compute 4 corners of an oriented bounding box in local meter coords.

    heading=0 means nose points north (+y direction).
    Wingspan is perpendicular to heading, length is along heading.
    Buffer is added to both dimensions (half on each side).
    """
    rad = math.radians(heading_deg)
    hw = (wingspan + buffer) / 2.0  # half-width (perpendicular to heading)
    hl = (length + buffer) / 2.0    # half-length (along heading)

    cos_h = math.cos(rad)
    sin_h = math.sin(rad)

    # Along-heading direction: (sin_h, cos_h), perpendicular: (cos_h, -sin_h)
    return [
        (cx + hl * sin_h + hw * cos_h, cy + hl * cos_h - hw * sin_h),
        (cx + hl * sin_h - hw * cos_h, cy + hl * cos_h + hw * sin_h),
        (cx - hl * sin_h - hw * cos_h, cy - hl * cos_h + hw * sin_h),
        (cx - hl * sin_h + hw * cos_h, cy - hl * cos_h - hw * sin_h),
    ]


def _get_axes(corners):
    """Get 2 unique edge normal axes for SAT from a rectangle's corners."""
    axes = []
    for i in range(2):
        j = (i + 1) % len(corners)
        ex = corners[j][0] - corners[i][0]
        ey = corners[j][1] - corners[i][1]
        length = math.sqrt(ex * ex + ey * ey)
        if length < 1e-10:
            continue
        axes.append((-ey / length, ex / length))
    return axes


def _project(corners, axis):
    """Project corners onto axis, return (min, max)."""
    dots = [c[0] * axis[0] + c[1] * axis[1] for c in corners]
    return min(dots), max(dots)


def _obb_overlap(corners_a, corners_b):
    """SAT-based OBB-OBB intersection test. Returns True if overlapping."""
    axes = _get_axes(corners_a) + _get_axes(corners_b)
    for axis in axes:
        min_a, max_a = _project(corners_a, axis)
        min_b, max_b = _project(corners_b, axis)
        if max_a < min_b or max_b < min_a:
            return False  # Separating axis found
    return True  # No separating axis — overlap


def _point_in_polygon(x, y, polygon):
    """Ray-casting point-in-polygon for (x, y) coords."""
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _obb_inside_polygon(corners, polygon):
    """Check all 4 OBB corners are inside the polygon."""
    return all(_point_in_polygon(c[0], c[1], polygon) for c in corners)


def check_placement(moving, others, zone_coords, buffer_m=SAFETY_BUFFER_M):
    """Full placement validation.

    Collision rule: a plane's body entering another plane's buffer zone is
    a collision. Two buffer zones merely overlapping is NOT a collision.

    Args:
        moving: dict with lat, lng, heading, wingspan_m, length_m
        others: list of dicts with lat, lng, heading, wingspan_m, length_m, tail_number
        zone_coords: [[lat, lng], ...] polygon of the zone
        buffer_m: safety distance in meters

    Returns:
        (valid: bool, reason: str|None, conflict_tail: str|None)
    """
    if not others:
        return True, None, None

    ref_lat = moving["lat"]
    ref_lng = moving["lng"]

    mx, my = _lat_lng_to_meters(moving["lat"], moving["lng"], ref_lat, ref_lng)
    m_ws, m_ln = get_effective_dimensions(
        moving.get("wingspan_m"), moving.get("length_m"),
    )
    if m_ws is None:
        return True, None, None

    m_heading = moving["heading"]
    moving_fuselage = _compute_obb_corners(mx, my, m_heading, m_ws * FUSELAGE_WIDTH_RATIO, m_ln * FUSELAGE_LENGTH_RATIO, 0)
    moving_wings = _compute_obb_corners(mx, my, m_heading, m_ws * WING_SPAN_RATIO, m_ln * WING_CHORD_RATIO, 0)
    moving_buffered = _compute_obb_corners(mx, my, m_heading, m_ws, m_ln, buffer_m)

    for other in others:
        o_ws, o_ln = get_effective_dimensions(
            other.get("wingspan_m"), other.get("length_m"),
        )
        if o_ws is None:
            continue

        ox, oy = _lat_lng_to_meters(other["lat"], other["lng"], ref_lat, ref_lng)
        o_heading = other.get("heading", 0.0)
        other_fuselage = _compute_obb_corners(ox, oy, o_heading, o_ws * FUSELAGE_WIDTH_RATIO, o_ln * FUSELAGE_LENGTH_RATIO, 0)
        other_wings = _compute_obb_corners(ox, oy, o_heading, o_ws * WING_SPAN_RATIO, o_ln * WING_CHORD_RATIO, 0)
        other_buffered = _compute_obb_corners(ox, oy, o_heading, o_ws, o_ln, buffer_m)

        if (
            _obb_overlap(moving_fuselage, other_buffered) or _obb_overlap(moving_wings, other_buffered) or
            _obb_overlap(other_fuselage, moving_buffered) or _obb_overlap(other_wings, moving_buffered)
        ):
            return False, "collision", other.get("tail_number", "unknown")

    return True, None, None
