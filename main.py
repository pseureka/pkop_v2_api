from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import AsyncSessionLocal
from seed import seed_ramps
from routers import ramps, zones, parking_spots, aircraft, adg_classes, aircraft_types


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        async with AsyncSessionLocal() as db:
            await seed_ramps(db)
    except Exception as e:
        print(f"[startup] Seed failed (DB may not be configured): {e}")
    yield


app = FastAPI(title="Ramp Management API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:3000",
        "https://pkop-hjhub9dzdeccgthp.canadacentral-01.azurewebsites.net",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(adg_classes.router)
app.include_router(aircraft_types.router)
app.include_router(ramps.router)
app.include_router(zones.router)
app.include_router(parking_spots.router)
app.include_router(aircraft.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
