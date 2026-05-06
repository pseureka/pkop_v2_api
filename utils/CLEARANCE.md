# Clearance Policy

The parking optimizer used to apply a single symmetric buffer (`SAFETY_BUFFER_FT
= 5.0` ft) equally to wingspan and length. That made nose-tail rows as wide
as wingtips, and aircraft could not interleave the way they do on a real ramp.

Today's model uses **per-component, per-axis margins** centralized in
`ClearancePolicy` (see `clearance.py`). Each aircraft yields four oriented
bounding boxes:

| OBB | Body? | Lateral inflate (per side) | Longitudinal inflate (per side) |
|---|---|---|---|
| `fuselage` | yes (no margin) | 0 | 0 |
| `wings` | yes (no margin) | 0 | 0 |
| `fuselage_buffered` | no | `fuselage_lateral_m` | `fuselage_longitudinal_m` |
| `wings_buffered` | no | `wing_lateral_m` | `wing_longitudinal_m` |

A collision = any body OBB of one aircraft intrudes any buffered OBB of the
other. Buffer-buffer overlap is still allowed (preserves tight packing).

## Default values

```
wing_lateral_m          = 3.048  m   (10 ft per side  — wingtip protection)
wing_longitudinal_m     = 1.524  m   ( 5 ft per side  — wing chord direction)
fuselage_lateral_m      = 0.914  m   ( 3 ft per side  — fuselage side clearance)
fuselage_longitudinal_m = 0.762  m   (2.5 ft per side — nose-to-tail)
boundary_lateral_m      = 1.524  m   ( 5 ft per side  — to zone edge)
boundary_longitudinal_m = 1.524  m   ( 5 ft per side  — to zone edge)
```

### Rationale

- **Wing lateral 10 ft/side**: FAA AC 150/5300-13B Table 3-1 lists 25 ft total
  wingtip-to-wingtip clearance for ADG-I/II aircraft on the apron. Split per
  side that is ~12.5 ft; we use 10 ft because parked spacing (vs taxi spacing)
  is conventionally a bit tighter.
- **Wing longitudinal 5 ft/side**: chord-direction clearance is rarely binding
  on a ramp; matches the old uniform buffer.
- **Fuselage lateral 3 ft/side**: a narrow side envelope so a wingtip can sit
  beside a fuselage when the geometry allows.
- **Fuselage longitudinal 2.5 ft/side**: the nose-tail axis is where realistic
  ramps pack tight; this lets rows interleave.
- **Boundary 5 ft/side**: keeps planes off the painted edge of the zone.

These are engineering defaults — change them in `clearance.py` if SMEs disagree.
The dataclass is the single source of truth.

## Legacy `buffer_ft` parameter

API endpoints still accept `buffer_ft` (default 5.0) for backwards compatibility.
It is now interpreted as a **uniform scale on the policy**: `buffer_ft / 5.0`
multiplies every margin. So `buffer_ft=5.0` → `DEFAULT`, `buffer_ft=10.0` →
2× every margin, etc. The UI's safety-buffer slider continues to work as before
without code changes.

For full control, send a `clearance_policy` body field (Pydantic model, all
fields optional) on the autostack/utilization endpoints — provided fields are
merged onto `DEFAULT`.

## Frontend mirror

`ui/src/utils/geometry.js` mirrors these values as a fallback. The frontend
fetches `GET /api/clearance/policy` on app load and uses the live values; the
hardcoded fallback is only used if the fetch fails. **If you change the
defaults here, update the fallback in `geometry.js` to match.**
