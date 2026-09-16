"""Idempotent demo seeding: organizations, relay point, staff accounts, parcels.

Safe to run on every startup — everything is upserted by a stable id, so no
duplicates and no destructive writes.
"""
from datetime import timedelta

from .database import DEFAULT_PICKUP_WINDOW_HOURS, db
from .models import CustodianType, OrgType, ParcelState, Role
from .security import hash_password
from .services import issue_otp
from .utils import now_iso, now_utc

DEMO_PASSWORD = "RelayDemo2026!"

ORGS = [
    {"_id": "org_platform", "type": OrgType.PLATFORM.value, "name": "ORALINK"},
    {"_id": "org_merchant", "type": OrgType.MERCHANT.value, "name": "Souk Digital"},
    {"_id": "org_carrier", "type": OrgType.CARRIER.value, "name": "Atlas Express"},
    {"_id": "org_partner", "type": OrgType.RELAY_PARTNER.value, "name": "Épicerie Al Baraka"},
]

POINT = {
    "_id": "point_albaraka",
    "org_id": "org_partner",
    "name": "Relais Al Baraka",
    "address": "12 Rue Ibn Batouta, Maârif",
    "city": "Casablanca",
    "postal_code": "20250",
    "latitude": 33.5883,
    "longitude": -7.6114,
    "opening_hours": "Lun–Sam 08:00–21:00",
    "capacity": 50,
    "services": ["PICKUP", "DROP_OFF", "RETURN"],
    "status": "ACTIVE",
}

USERS = [
    {"_id": "usr_agent", "email": "agent@relay.ma", "name": "Yassine (Agent)",
     "role": Role.RELAY_PARTNER_AGENT.value, "org_id": "org_partner", "relay_point_id": "point_albaraka"},
    {"_id": "usr_owner", "email": "owner@relay.ma", "name": "Fatima (Propriétaire)",
     "role": Role.RELAY_PARTNER_OWNER.value, "org_id": "org_partner", "relay_point_id": "point_albaraka"},
    {"_id": "usr_ops", "email": "ops@oralink.ma", "name": "Ops ORALINK",
     "role": Role.OPS_ADMIN.value, "org_id": "org_platform", "relay_point_id": None},
    {"_id": "usr_super", "email": "admin@oralink.ma", "name": "Super Admin",
     "role": Role.SUPER_ADMIN.value, "org_id": "org_platform", "relay_point_id": None},
]


async def _upsert_parcel(doc: dict):
    existing = await db.parcels.find_one({"_id": doc["_id"]})
    if existing:
        return existing
    await db.parcels.insert_one(doc)
    return doc


def _base_parcel(pid: str, code: str, name: str, phone: str) -> dict:
    return {
        "_id": pid,
        "tracking_code": code,
        "merchant_org_id": "org_merchant",
        "merchant_name": "Souk Digital",
        "carrier_org_id": "org_carrier",
        "carrier_name": "Atlas Express",
        "relay_point_id": "point_albaraka",
        "recipient": {"name": name, "phone": phone},
        "service_type": "PICKUP",
        "weight_g": 750,
        "size": "M",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "deleted_at": None,
    }


async def seed() -> None:
    for org in ORGS:
        await db.organizations.update_one({"_id": org["_id"]}, {"$setOnInsert": org}, upsert=True)

    await db.relay_points.update_one({"_id": POINT["_id"]}, {"$setOnInsert": POINT}, upsert=True)

    for u in USERS:
        await db.users.update_one(
            {"_id": u["_id"]},
            {"$setOnInsert": {
                "_id": u["_id"], "email": u["email"], "name": u["name"],
                "password_hash": hash_password(DEMO_PASSWORD), "is_active": True,
                "created_at": now_iso(), "deleted_at": None,
            }},
            upsert=True,
        )
        await db.memberships.update_one(
            {"user_id": u["_id"], "org_id": u["org_id"]},
            {"$set": {"role": u["role"], "relay_point_id": u["relay_point_id"], "active": True}},
            upsert=True,
        )

    if await db.parcels.count_documents({"relay_point_id": "point_albaraka"}) > 0:
        return  # parcels already seeded

    now = now_utc()
    deadline = (now + timedelta(hours=DEFAULT_PICKUP_WINDOW_HOURS)).isoformat()
    partner_custodian = {"type": CustodianType.RELAY_PARTNER.value, "id": "point_albaraka"}
    carrier_custodian = {"type": CustodianType.CARRIER.value, "id": "org_carrier"}

    # IN_TRANSIT — ready to RECEIVE
    in_transit = [
        ("p_it1", "RLYA1B2C3D4", "Hassan Alaoui", "+212612345642"),
        ("p_it2", "RLYE5F6G7H8", "Yasmine Benali", "+212661223399"),
        ("p_it3", "RLYI9J0K1L2", "Omar Idrissi", "+212677889911"),
    ]
    for pid, code, name, phone in in_transit:
        doc = _base_parcel(pid, code, name, phone)
        doc.update({"state": ParcelState.IN_TRANSIT.value, "custodian": carrier_custodian,
                    "available_from": None, "pickup_deadline": None})
        await _upsert_parcel(doc)

    # AVAILABLE_FOR_PICKUP — ready to HAND OVER (OTP issued, plaintext not stored)
    available = [
        ("p_av1", "RLYM3N4O5P6", "Salma Tazi", "+212612000042"),
        ("p_av2", "RLYQ7R8S9T0", "Karim Fassi", "+212655112288"),
        ("p_av3", "RLYU1V2W3X4", "Nadia Amrani", "+212699334455"),
    ]
    for pid, code, name, phone in available:
        doc = _base_parcel(pid, code, name, phone)
        doc.update({"state": ParcelState.AVAILABLE_FOR_PICKUP.value, "custodian": partner_custodian,
                    "available_from": now.isoformat(), "pickup_deadline": deadline,
                    "received_at": now.isoformat()})
        await _upsert_parcel(doc)
        await issue_otp(pid)

    # AVAILABLE but past deadline — demo EXPIRE -> RETURN
    past = _base_parcel("p_exp", "RLYZ9Y8X7W6", "Rachid Berrada", "+212611778800")
    past.update({"state": ParcelState.AVAILABLE_FOR_PICKUP.value, "custodian": partner_custodian,
                 "available_from": (now - timedelta(hours=100)).isoformat(),
                 "pickup_deadline": (now - timedelta(hours=4)).isoformat(),
                 "received_at": (now - timedelta(hours=100)).isoformat()})
    await _upsert_parcel(past)
    await issue_otp("p_exp")
