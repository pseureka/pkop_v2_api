"""
Clearance policy endpoint — exposes the canonical defaults so the frontend
can mirror them without drift.
"""

from fastapi import APIRouter

from utils.clearance import ClearancePolicy, policy_to_dict


router = APIRouter(prefix="/api/clearance", tags=["clearance"])


@router.get("/policy")
async def get_clearance_policy():
    """Return the default ClearancePolicy as JSON.

    The UI fetches this on app load so its in-browser collision math uses the
    exact same per-component, per-axis margins as the backend optimizer.
    """
    return policy_to_dict(ClearancePolicy.DEFAULT)
