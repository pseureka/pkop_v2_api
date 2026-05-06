"""
Centralized clearance policy.

Replaces the legacy single symmetric `SAFETY_BUFFER_FT` with per-component,
per-axis margins. Wings get a strict lateral envelope (wingtip protection is
the binding constraint on a ramp); fuselage gets a tighter nose-tail envelope
(rows can pack closer than wings allow).

See utils/CLEARANCE.md for the rationale behind the default values.
"""

from dataclasses import asdict, dataclass, replace
from typing import ClassVar, Optional

from pydantic import BaseModel


FT_TO_M = 0.3048


@dataclass(frozen=True)
class ClearancePolicy:
    """Per-component, per-axis clearance margins (meters per side).

    Each margin is the inflation on ONE side of the OBB along the named axis.
    So `wing_lateral_m=3.05` means the wing-buffered OBB extends 3.05 m past
    each wingtip — total lateral inflation is `2 * wing_lateral_m`.
    """
    # Wing envelope: strict — wingtips are the binding lateral constraint
    wing_lateral_m: float = 10.0 * FT_TO_M       # 3.048 m per side
    wing_longitudinal_m: float = 5.0 * FT_TO_M   # 1.524 m per side
    # Fuselage envelope: tight — nose/tail can pack close
    fuselage_lateral_m: float = 3.0 * FT_TO_M    # 0.914 m per side
    fuselage_longitudinal_m: float = 2.5 * FT_TO_M  # 0.762 m per side
    # Zone-boundary margin (replaces the single old buffer for edge fit checks)
    boundary_lateral_m: float = 5.0 * FT_TO_M    # 1.524 m per side
    boundary_longitudinal_m: float = 5.0 * FT_TO_M  # 1.524 m per side

    DEFAULT: ClassVar["ClearancePolicy"]

    @classmethod
    def from_buffer_ft(cls, buffer_ft: Optional[float]) -> "ClearancePolicy":
        """Legacy shim: scale DEFAULT margins by `buffer_ft / 5.0`.

        Preserves the single-slider UX in the UI. `buffer_ft=5.0` (the historical
        default) maps to `DEFAULT`. `buffer_ft=10.0` doubles every margin, etc.
        Passing None or 0 returns `DEFAULT` unchanged.
        """
        if buffer_ft is None or buffer_ft <= 0:
            return cls.DEFAULT
        scale = buffer_ft / 5.0
        d = cls.DEFAULT
        return cls(
            wing_lateral_m=d.wing_lateral_m * scale,
            wing_longitudinal_m=d.wing_longitudinal_m * scale,
            fuselage_lateral_m=d.fuselage_lateral_m * scale,
            fuselage_longitudinal_m=d.fuselage_longitudinal_m * scale,
            boundary_lateral_m=d.boundary_lateral_m * scale,
            boundary_longitudinal_m=d.boundary_longitudinal_m * scale,
        )

    def with_overrides(self, **overrides) -> "ClearancePolicy":
        """Return a copy with named fields replaced. Used by API overrides."""
        clean = {k: v for k, v in overrides.items() if v is not None}
        return replace(self, **clean) if clean else self

    def max_margin_m(self) -> float:
        """Largest per-side margin across all components/axes — used to size the
        spatial-bucket cell for conflict-graph construction."""
        return max(
            self.wing_lateral_m, self.wing_longitudinal_m,
            self.fuselage_lateral_m, self.fuselage_longitudinal_m,
        )


ClearancePolicy.DEFAULT = ClearancePolicy()


class ClearancePolicyOverride(BaseModel):
    """Optional per-request clearance policy overrides.

    Any field left as None falls through to the policy derived from buffer_ft
    (or `ClearancePolicy.DEFAULT` when buffer_ft is also absent).
    """
    wing_lateral_m: Optional[float] = None
    wing_longitudinal_m: Optional[float] = None
    fuselage_lateral_m: Optional[float] = None
    fuselage_longitudinal_m: Optional[float] = None
    boundary_lateral_m: Optional[float] = None
    boundary_longitudinal_m: Optional[float] = None


def policy_from_request(buffer_ft: Optional[float],
                        override: Optional[ClearancePolicyOverride]) -> ClearancePolicy:
    """Build a ClearancePolicy from an API request.

    Order of precedence: explicit override fields > buffer_ft scale > DEFAULT.
    """
    base = ClearancePolicy.from_buffer_ft(buffer_ft)
    if override is None:
        return base
    return base.with_overrides(**override.model_dump(exclude_none=True))


def policy_to_dict(policy: ClearancePolicy) -> dict:
    """Serialize a policy to a JSON-friendly dict (for the GET endpoint)."""
    return asdict(policy)


def validate_access(candidate, existing_candidates, zone_m, policy):
    """Movement-path validation hook (placeholder).

    Future use: entry corridor reachability, towing-path feasibility, and exit
    clearance. Returns True today so the optimizer behavior is unchanged.

    Receives `existing_candidates` (the list being built) so a future
    implementation can model "candidate X blocks egress of an earlier candidate."

    TODO: implement once movement-path constraints are defined.
    """
    return True
