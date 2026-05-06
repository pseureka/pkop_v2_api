import os

from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from routers import (
    adg_classes,
    aircraft,
    aircraft_types,
    autostack,
    auth as auth_router,
    clearance,
    ramps,
    tail_lookup,
    utilization,
    zones,
)
from auth.dependencies import get_current_user


app = FastAPI(title="Ramp Management API")

_origins = [o.strip() for o in os.getenv("CORS_ALLOW_ORIGINS", "http://localhost:5173").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth router is unprotected (you can't log in with a token you don't have yet).
app.include_router(auth_router.router)

# Everything else requires a valid bearer token.
_protected = [Depends(get_current_user)]
app.include_router(adg_classes.router, dependencies=_protected)
app.include_router(aircraft_types.router, dependencies=_protected)
app.include_router(ramps.router, dependencies=_protected)
app.include_router(zones.router, dependencies=_protected)
app.include_router(aircraft.router, dependencies=_protected)
app.include_router(tail_lookup.router, dependencies=_protected)
app.include_router(autostack.router, dependencies=_protected)
app.include_router(utilization.router, dependencies=_protected)
app.include_router(clearance.router, dependencies=_protected)


@app.get("/health")
async def health():
    return {"status": "ok"}
