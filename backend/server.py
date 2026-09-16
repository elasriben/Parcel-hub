"""RELAY backend entrypoint — modular monolith (see relay/ package).

Modules: identity/auth, organizations, relay-points, parcels (state machine),
custody, pickup (OTP), returns, incidents, notifications (outbox), audit.
Transactional core on MongoDB with atomic conditional transitions for
concurrency safety and idempotency keys for safe retries.
"""
import logging

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from relay.database import client, ensure_indexes
from relay.routes import router as api_router
from relay.seed import seed

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("relay")

app = FastAPI(title="RELAY API", version="0.1.0")


@app.get("/api/")
async def root():
    return {"service": "RELAY", "operator": "ORALINK", "status": "ok"}


app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup():
    await ensure_indexes()
    await seed()
    logger.info("RELAY backend ready — demo data seeded.")


@app.on_event("shutdown")
async def on_shutdown():
    client.close()
