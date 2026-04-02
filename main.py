from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import ramps, zones, aircraft, adg_classes, aircraft_types, tail_lookup, autostack


app = FastAPI(title="Ramp Management API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(adg_classes.router)
app.include_router(aircraft_types.router)
app.include_router(ramps.router)
app.include_router(zones.router)
app.include_router(aircraft.router)
app.include_router(tail_lookup.router)
app.include_router(autostack.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
