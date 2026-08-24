# pkop_v2_api

Backend API for **aircraft parking collision avoidance** — models an airport's
ramps and zones as geospatial polygons, tracks parked aircraft with real
dimensions and headings, and computes conflict-free placements using
oriented-bounding-box (OBB) collision detection plus an OR-Tools CP-SAT
optimizer.

Built with FastAPI + async SQLAlchemy over PostgreSQL/PostGIS. Deployed to
Azure App Service (`pkop-api`, slot `v2`) via GitHub Actions.

---

## What it does

- **Ramp / zone hierarchy** — ramps are top-level polygons (tenant, FBO,
  terminal flags); zones are child polygons inside a ramp where aircraft park.
- **Aircraft tracking** — each aircraft has a tail number, an aircraft type
  (wingspan / length / tail height), an ADG class, a lat/lng, and a heading.
- **Component-aware collision model** — every aircraft is decomposed into four
  OBBs (fuselage, wings, and a buffered envelope for each). A collision is any
  *body* OBB intruding another aircraft's *buffered* OBB. Buffer-on-buffer
  overlap is permitted, which keeps packing tight and realistic.
- **AutoStack** — given a zone and a set of aircraft, generates candidate
  placements and uses CP-SAT to select the best non-conflicting arrangement
  (with heading-consistency and row-alignment objectives).
- **Capacity & utilization** — computes max ADG-I-equivalent units per zone,
  ADG weights, remaining capacity per class, and a before/after comparison
  showing what AutoStack would gain.
- **Tail-number lookup** — resolves a registration to its country/registry and
  to a known aircraft type; full-text search across the type catalog.

---

## Architecture

```
main.py                 FastAPI app, CORS, router wiring, /health
database.py             async engine + session factory (asyncpg)
models.py               SQLAlchemy ORM: adg_classes, aircraft_types, ramps,
                        zones, aircraft, users
schemas.py              Pydantic request/response models
seed.py                 Seeds ramps + aircraft from provideddata/*.json

auth/
  security.py           bcrypt hashing, JWT encode/decode
  dependencies.py       get_current_user (HTTP bearer), require_role helper

routers/
  auth.py               /api/auth        — login, me
  adg_classes.py        /api/adg-classes — ADG class dimension bands
  aircraft_types.py     /api/aircraft-types
  ramps.py              /api/ramps
  zones.py              /api/zones       — CRUD + capacity recalculation
  aircraft.py           /api/aircraft    — CRUD + move
  autostack.py          /api/autostack   — optimal placement
  utilization.py        /api/utilization — utilization, capacity, analysis
  tail_lookup.py        /api/lookup      — tail lookup, type search, countries
  clearance.py          /api/clearance   — canonical clearance policy
  geofences.py          legacy — defined but NOT registered in main.py

utils/
  clearance.py          ClearancePolicy dataclass — single source of truth
  collision.py          OBB construction + Separating Axis Theorem
  geometry.py           GeoJSON ↔ PostGIS WKT ↔ Leaflet coordinate conversion
  autostack.py          candidate generation + greedy single-type fill
  optimizer.py          CP-SAT placement + capacity solvers (hangar / ramp)
  capacity.py           thin wrapper over optimize_capacity
  CLEARANCE.md          rationale for every default margin — read this first

alembic/versions/       0001 → 0009 migrations
scripts/create_user.py  create/update a login user
scripts/clean_tiles.py  YOLOv8 + OpenCV — strip parked aircraft from satellite
                        tiles (offline tooling, deps not in requirements.txt)
```

### Two placement modes

| Mode | Used for | Strategy |
|---|---|---|
| `hangar` | free-form parking | greedy sequential fill, any heading |
| `ramp` | ramp/apron parking | row-column grid aligned to the zone's primary axis |

`parking_mode` is accepted by the zone-capacity, optimize-analysis, and
autostack endpoints, and cached per-mode in `zones.capacity_data`.

---

## Clearance policy

Margins are **per-component and per-axis**, in meters *per side*:

| Margin | Default | Meaning |
|---|---|---|
| `wing_lateral_m` | 3.048 (10 ft) | wingtip protection — the binding constraint |
| `wing_longitudinal_m` | 1.524 (5 ft) | wing chord direction |
| `fuselage_lateral_m` | 0.914 (3 ft) | fuselage side clearance |
| `fuselage_longitudinal_m` | 0.762 (2.5 ft) | nose-to-tail |
| `boundary_lateral_m` | 1.524 (5 ft) | to zone edge |
| `boundary_longitudinal_m` | 1.524 (5 ft) | to zone edge |

Two ways to control it on request:

- `buffer_ft` (legacy, default `5.0`) — a **uniform scale**: every margin is
  multiplied by `buffer_ft / 5.0`. Existing safety-buffer sliders keep working.
- `clearance_policy` — a body field with any subset of the fields above,
  merged onto the defaults. Full per-axis control.

`GET /api/clearance/policy` returns the canonical defaults so a frontend can
run identical collision math without hardcoding drifted values. See
[`utils/CLEARANCE.md`](utils/CLEARANCE.md) for the FAA references behind each
number.

---

## Getting started

### Prerequisites

- Python 3.14 (the CI workflow pins this)
- PostgreSQL with the **PostGIS** extension

### Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # then fill it in
```

### Environment

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://user:pass@host:5432/db?ssl=require` |
| `JWT_SECRET` | 32+ random bytes — required, login fails without it |
| `JWT_ALGORITHM` | default `HS256` |
| `JWT_EXPIRE_MINUTES` | default `480` (8 hours) |
| `CORS_ALLOW_ORIGINS` | comma-separated; default `http://localhost:5173` |

Generate a secret with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Alembic reuses `DATABASE_URL` and swaps `asyncpg` → `psycopg2` itself, so you
only configure it once (`alembic.ini` leaves `sqlalchemy.url` empty on purpose).

### Migrate, seed, create a user

```bash
alembic upgrade head

python seed.py                                    # optional demo ramps + aircraft
python scripts/create_user.py --username alice \
       --password 'at-least-8-chars' --role Admin # roles: Admin | Reader
```

### Run

```bash
uvicorn main:app --reload --port 8000
```

- Interactive docs: <http://localhost:8000/docs>
- Health check: <http://localhost:8000/health>

Production runs under gunicorn with uvicorn workers, e.g.:

```bash
gunicorn main:app -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000
```

---

## Authentication

Everything except `/health` and `/api/auth/login` requires a bearer token.

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"alice","password":"at-least-8-chars"}' | jq -r .access_token)

curl http://localhost:8000/api/zones -H "Authorization: Bearer $TOKEN"
```

Tokens are HS256 JWTs carrying `sub` (user id) and `role`, valid for
`JWT_EXPIRE_MINUTES`. Roles are `Admin` and `Reader`; `require_role()` exists
in `auth/dependencies.py` but is not yet applied to any endpoint — today all
authenticated users have the same access.

---

## API reference

All routes are prefixed with `/api` and require `Authorization: Bearer <token>`
unless noted.

### Auth
| Method | Path | Notes |
|---|---|---|
| `POST` | `/api/auth/login` | public — returns token + user |
| `GET` | `/api/auth/me` | current user |

### Reference data
| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/adg-classes` | ADG class wingspan/tail-height bands |
| `PATCH` | `/api/adg-classes/{class}` | |
| `GET` | `/api/aircraft-types` | `?adg_class=` filter |
| `POST` `PATCH` `DELETE` | `/api/aircraft-types[/{id}]` | |

### Geometry
| Method | Path | Notes |
|---|---|---|
| `GET` `POST` `PATCH` `DELETE` | `/api/ramps[/{id}]` | polygon + tenant metadata |
| `GET` | `/api/zones` | `?ramp_id=` filter |
| `GET` `POST` `PATCH` `DELETE` | `/api/zones[/{id}]` | capacity computed on create |
| `POST` | `/api/zones/{id}/recalculate-capacity` | `{buffer_ft, parking_mode}` |

Polygons go **in** as `coordinates: [[lng, lat], ...]` (GeoJSON order) and come
**out** as `[[lat, lng], ...]` (Leaflet order). `utils/geometry.py` owns the
conversion; the ring is closed automatically.

### Aircraft
| Method | Path | Notes |
|---|---|---|
| `GET` `POST` `DELETE` | `/api/aircraft[/{id}]` | |
| `PATCH` | `/api/aircraft/{id}/move` | `{lat, lng, heading, zone_id}` |

### Placement & analysis
| Method | Path | Notes |
|---|---|---|
| `POST` | `/api/autostack/compute` | place a supplied aircraft list in a zone |
| `POST` | `/api/autostack/zone/{zone_id}` | re-arrange the zone's current aircraft |
| `GET` | `/api/utilization` | `?buffer_ft=` — all ramps and zones |
| `GET` | `/api/utilization/zones/{id}/capacity` | `?buffer_ft=&parking_mode=` |
| `POST` | `/api/utilization/zones/{id}/optimize-analysis` | current vs optimized |
| `GET` | `/api/clearance/policy` | canonical clearance defaults |

### Lookup
| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/lookup/tail/{tail_number}` | registry + matched type |
| `GET` | `/api/lookup/search?q=` | search make / model / designator |
| `GET` | `/api/lookup/countries` | known registration prefixes |

#### AutoStack example

```bash
curl -X POST http://localhost:8000/api/autostack/zone/$ZONE_ID \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{
        "zone_id": "'"$ZONE_ID"'",
        "parking_mode": "ramp",
        "num_options": 3,
        "clearance_policy": {"wing_lateral_m": 4.0}
      }'
```

Returns up to `num_options` ranked arrangements, each a list of
`{tail_number, lat, lng, heading}` placements.

---

## Database schema

| Table | Key columns |
|---|---|
| `adg_classes` | `class` (PK), wingspan / tail-height min-max bands |
| `aircraft_types` | make, model, designator, `wingspan_m`, `length_m`, `adg_class` FK |
| `ramps` | `geometry POLYGON(4326)`, tenant, `is_fbo`, `is_terminal`, capacity |
| `zones` | `geometry POLYGON(4326)`, `ramp_id` FK (cascade), `capacity_data` JSON cache |
| `aircraft` | tail number (unique), `type_id` FK, lat/lng, heading, `zone_id` FK (set null) |
| `users` | username (unique), bcrypt hash, role `Admin`\|`Reader`, `is_active` |

Primary keys are UUIDv7 (`uuid_utils.uuid7`), so they sort by creation time.
`zones.capacity_data` caches solver output keyed by parking mode and buffer, so
repeated utilization calls skip the CP-SAT run.

---

## Deployment

`.github/workflows/main_pkop-api(v2).yml` builds on every push to `main` (and
on manual dispatch), then deploys to Azure App Service `pkop-api`, slot `v2`,
using OIDC federated credentials. Azure's Oryx build runs `pip install` on the
platform, so the workflow's local venv exists only to catch dependency breakage
early and is excluded from the artifact.

Set `DATABASE_URL`, `JWT_SECRET`, and `CORS_ALLOW_ORIGINS` as App Service
application settings — they are not in the repo.

---

## Notes and known gaps

- `routers/geofences.py` is from the pre-0002 schema and is **not** mounted in
  `main.py`. It targets tables that migration 0002 replaced.
- `seed.py`'s docstring says it runs on app startup; it does not — `main.py`
  has no startup hook. Run `python seed.py` manually.
- The repo root contains `.baseline_*.json` / `.after_*.json` optimizer
  snapshots from capacity tuning runs. They are inputs to manual comparison,
  not fixtures for an automated suite.
- `scripts/clean_tiles.py` needs `opencv-python`, `ultralytics`, `mercantile`,
  and `requests`, none of which are in `requirements.txt` — install them
  separately if you need the tile-cleaning pipeline.
- There is no automated test suite yet.
- `bcrypt` is pinned to `<4.1` for passlib compatibility.
