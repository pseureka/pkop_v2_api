"""
Intelligent capacity estimation — estimate how many more aircraft
of each ADG class can fit in a zone given current occupancy.
"""

from .clearance import ClearancePolicy
from .collision import SAFETY_BUFFER_FT
from .optimizer import optimize_capacity


def estimate_remaining_capacity(zone_coords, parked_aircraft,
                                adg_representative_dims, buffer_ft=SAFETY_BUFFER_FT,
                                parking_mode="hangar", policy=None):
    """Estimate how many more aircraft of each ADG class can fit.

    Pass `policy` for the new component-aware model, or `buffer_ft` for the
    legacy uniform-scale shim.
    """
    return optimize_capacity(
        zone_coords, parked_aircraft, adg_representative_dims,
        buffer_ft=buffer_ft, parking_mode=parking_mode, policy=policy,
    )
