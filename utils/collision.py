"""
OBB-based collision detection for aircraft placement using Separating Axis Theorem.

Each aircraft is modeled as a composite of two body OBBs (fuselage + wings)
plus two buffered OBBs (component-aware safety envelopes). Clearance is
governed by `ClearancePolicy` — see utils/CLEARANCE.md.
"""

import math

from .clearance import ClearancePolicy, FT_TO_M

# Legacy single-buffer constant — preserved for any code that still imports it
# (e.g. UI default sliders, test fixtures). New code should use ClearancePolicy.
SAFETY_BUFFER_FT = 5.0
SAFETY_BUFFER_M = SAFETY_BUFFER_FT * FT_TO_M

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


def _compute_obb_corners(cx, cy, heading_deg, width, length,
                         lateral_margin=0.0, longitudinal_margin=0.0):
    """Compute 4 corners of an oriented bounding box in local meter coords.

    heading=0 means nose points north (+y direction).
    `width` is perpendicular to heading (lateral); `length` is along heading.
    Margins are PER-SIDE inflations: `width + 2*lateral_margin` total.
    """
    rad = math.radians(heading_deg)
    hw = width / 2.0 + lateral_margin       # half-width perpendicular to heading
    hl = length / 2.0 + longitudinal_margin  # half-length along heading

    cos_h = math.cos(rad)
    sin_h = math.sin(rad)

    # Along-heading direction: (sin_h, cos_h), perpendicular: (cos_h, -sin_h)
    return [
        (cx + hl * sin_h + hw * cos_h, cy + hl * cos_h - hw * sin_h),
        (cx + hl * sin_h - hw * cos_h, cy + hl * cos_h + hw * sin_h),
        (cx - hl * sin_h - hw * cos_h, cy - hl * cos_h + hw * sin_h),
        (cx - hl * sin_h + hw * cos_h, cy - hl * cos_h - hw * sin_h),
    ]


def build_aircraft_obbs(cx, cy, heading_deg, wingspan, length,
                        policy=None):
    """Build the 4 OBBs for an aircraft at (cx, cy) with the given policy.

    Returns dict with keys: fuselage, wings, fuselage_buffered, wings_buffered.
    fuselage/wings are unbuffered body shapes; the *_buffered variants apply
    per-component, per-axis margins from the policy.
    """
    if policy is None:
        policy = ClearancePolicy.DEFAULT

    fuselage_w = wingspan * FUSELAGE_WIDTH_RATIO
    fuselage_l = length * FUSELAGE_LENGTH_RATIO
    wings_w = wingspan * WING_SPAN_RATIO
    wings_l = length * WING_CHORD_RATIO

    return {
        "fuselage": _compute_obb_corners(cx, cy, heading_deg, fuselage_w, fuselage_l),
        "wings": _compute_obb_corners(cx, cy, heading_deg, wings_w, wings_l),
        "fuselage_buffered": _compute_obb_corners(
            cx, cy, heading_deg, fuselage_w, fuselage_l,
            lateral_margin=policy.fuselage_lateral_m,
            longitudinal_margin=policy.fuselage_longitudinal_m,
        ),
        "wings_buffered": _compute_obb_corners(
            cx, cy, heading_deg, wings_w, wings_l,
            lateral_margin=policy.wing_lateral_m,
            longitudinal_margin=policy.wing_longitudinal_m,
        ),
    }


def aircraft_obbs_collide(a, b):
    """Symmetric four-shape collision predicate.

    A collision occurs when ANY body OBB of one aircraft intrudes into ANY
    buffered OBB of the other. Buffer-buffer overlap is allowed.
    """
    return (
        _obb_overlap(a["fuselage"], b["fuselage_buffered"]) or
        _obb_overlap(a["fuselage"], b["wings_buffered"]) or
        _obb_overlap(a["wings"], b["fuselage_buffered"]) or
        _obb_overlap(a["wings"], b["wings_buffered"]) or
        _obb_overlap(b["fuselage"], a["fuselage_buffered"]) or
        _obb_overlap(b["fuselage"], a["wings_buffered"]) or
        _obb_overlap(b["wings"], a["fuselage_buffered"]) or
        _obb_overlap(b["wings"], a["wings_buffered"])
    )


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


def check_placement(moving, others, zone_coords, policy=None):
    """Full placement validation.

    Collision rule: any body OBB of one aircraft intrudes any buffered OBB of
    the other. Buffer-buffer overlap is NOT a collision. Symmetric.

    Args:
        moving: dict with lat, lng, heading, wingspan_m, length_m
        others: list of dicts with lat, lng, heading, wingspan_m, length_m, tail_number
        zone_coords: [[lat, lng], ...] polygon of the zone (currently unused)
        policy: ClearancePolicy (defaults to ClearancePolicy.DEFAULT)

    Returns:
        (valid: bool, reason: str|None, conflict_tail: str|None)
    """
    if not others:
        return True, None, None

    if policy is None:
        policy = ClearancePolicy.DEFAULT

    ref_lat = moving["lat"]
    ref_lng = moving["lng"]

    mx, my = _lat_lng_to_meters(moving["lat"], moving["lng"], ref_lat, ref_lng)
    m_ws, m_ln = get_effective_dimensions(
        moving.get("wingspan_m"), moving.get("length_m"),
    )
    if m_ws is None:
        return True, None, None

    moving_obbs = build_aircraft_obbs(mx, my, moving["heading"], m_ws, m_ln, policy)

    for other in others:
        o_ws, o_ln = get_effective_dimensions(
            other.get("wingspan_m"), other.get("length_m"),
        )
        if o_ws is None:
            continue

        ox, oy = _lat_lng_to_meters(other["lat"], other["lng"], ref_lat, ref_lng)
        other_obbs = build_aircraft_obbs(
            ox, oy, other.get("heading", 0.0), o_ws, o_ln, policy,
        )

        if aircraft_obbs_collide(moving_obbs, other_obbs):
            return False, "collision", other.get("tail_number", "unknown")

    return True, None, None
