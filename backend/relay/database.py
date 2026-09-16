import os
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

ROOT_DIR = Path(__file__).parent.parent
load_dotenv(ROOT_DIR / ".env")

_mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(_mongo_url)
db = client[os.environ["DB_NAME"]]

# --- Runtime configuration (never hardcode critical delays) --------------------
JWT_SECRET = os.environ["JWT_SECRET"]
JWT_ALGORITHM = "HS256"
JWT_ISSUER = "relay-api"
JWT_AUDIENCE = "relay-partner-app"
ACCESS_TOKEN_MINUTES = int(os.environ.get("ACCESS_TOKEN_MINUTES", "720"))

DEFAULT_PICKUP_WINDOW_HOURS = int(os.environ.get("DEFAULT_PICKUP_WINDOW_HOURS", "72"))
OTP_TTL_MINUTES = int(os.environ.get("OTP_TTL_MINUTES", "15"))
OTP_MAX_ATTEMPTS = int(os.environ.get("OTP_MAX_ATTEMPTS", "5"))


async def ensure_indexes() -> None:
    await db.users.create_index("email", unique=True)
    await db.memberships.create_index([("user_id", 1), ("org_id", 1)], unique=True)
    await db.parcels.create_index("tracking_code", unique=True)
    await db.parcels.create_index([("relay_point_id", 1), ("state", 1)])
    await db.otps.create_index("parcel_id", unique=True)
    await db.idempotency.create_index("created_at")
    await db.audit_log.create_index([("resource_id", 1), ("at", -1)])
    await db.custody_events.create_index([("parcel_id", 1), ("seq", 1)])
