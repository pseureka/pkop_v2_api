"""
Intelligent capacity estimation — estimate how many more aircraft
of each ADG class can fit in a zone given current occupancy.

Uses Gurobi MIP optimizer for mathematically optimal results.
"""

from .collision import SAFETY_BUFFER_FT
from .optimizer import optimize_capacity


def estimate_remaining_capacity(zone_coords, parked_aircraft,
                                adg_representative_dims, buffer_ft=SAFETY_BUFFER_FT):
    """Estimate how many more aircraft of each ADG class can fit.

    Args:
        zone_coords: [[lat, lng], ...] zone polygon
        parked_aircraft: list of dicts with lat, lng, heading, wingspan_m, length_m
        adg_representative_dims: dict { adg_class: { wingspan_m, length_m } }
        buffer_ft: safety buffer in feet

    Returns:
        dict { adg_class: remaining_count }
    """
    return optimize_capacity(
        zone_coords, parked_aircraft, adg_representative_dims, buffer_ft
    )
