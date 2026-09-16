"""HTTP API contracts for the RELAY Partner app (all under /api).

Critical operations are commands (receive / authorize / complete / return),
never direct status edits from the client. Camera scan and manual entry both
funnel through the exact same server-side validation. Mutations accept an
`Idempotency-Key` header so a retried request produces a single business effect.
"""
import uuid
from datetime import timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, EmailStr, Field
from pymongo.errors import DuplicateKeyError

from .audit import write_audit
from .database import db
from .dependencies import build_context, current_user, partner_context
from .models import IncidentType, ParcelState
from .security import create_access_token, verify_password
from .services import (
    authorize_pickup,
    complete_pickup,
    create_incident,
    create_return,
    do_receive,
    expire_parcel,
    issue_otp,
    mark_return_ready,
    point_current_count,
    refuse_parcel,
)
from .shaping import incident_public, parcel_public, point_public, receipt_public
from .utils import now_iso, now_utc

router = APIRouter(prefix="/api")

RETURN_STATES = [
    ParcelState.RETURN_PENDING.value,
    ParcelState.RETURN_READY.value,
    ParcelState.RETURN_COLLECTED.value,
    ParcelState.RETURN_IN_TRANSIT.value,
    ParcelState.RETURN_RECEIVED.value,
    ParcelState.RETURNED.value,
    ParcelState.CUSTOMER_REFUSED.value,
    ParcelState.EXPIRED.value,
    ParcelState.DAMAGED.value,
]

FILTER_MAP = {
    "to_receive": [ParcelState.IN_TRANSIT.value],
    "available": [ParcelState.AVAILABLE_FOR_PICKUP.value, ParcelState.PICKUP_AUTHORIZED.value],
    "handed_over": [ParcelState.HANDED_OVER.value],
    "returns": RETURN_STATES,
}


# --- Schemas ------------------------------------------------------------------

class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=256)


class AuthorizeIn(BaseModel):
    otp: str = Field(min_length=6, max_length=6)
    scan_code: str = Field(min_length=1)


class ReturnIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class IncidentIn(BaseModel):
    type: str
    parcel_id: str | None = None
    description: str = Field(min_length=1, max_length=1000)


class StatusIn(BaseModel):
    status: str


# --- Helpers ------------------------------------------------------------------

def actor_summary(ctx: dict) -> dict:
    point = ctx.get("point")
    return {
        "user": {"id": ctx["user"]["_id"], "email": ctx["user"]["email"], "name": ctx["user"].get("name")},
        "role": ctx["membership"]["role"],
        "org": {"id": ctx["org"]["_id"], "name": ctx["org"].get("name"), "type": ctx["org"].get("type")},
        "relay_point": {"id": point["_id"], "name": point["name"]} if point else None,
    }


async def get_scoped_parcel(parcel_id: str, ctx: dict) -> dict:
    doc = await db.parcels.find_one({"_id": parcel_id, "deleted_at": None})
    # Tenant isolation: a parcel outside the actor's point is treated as absent.
    if not doc or doc.get("relay_point_id") != ctx["point"]["_id"]:
        raise HTTPException(status_code=404, detail="Colis introuvable")
    return doc


async def idempotent_get(key: str | None):
    if not key:
        return None
    row = await db.idempotency.find_one({"_id": key})
    return row["response"] if row else None


async def idempotent_save(key: str | None, actor_id: str, endpoint: str, response: dict):
    if not key:
        return
    try:
        await db.idempotency.insert_one(
            {"_id": key, "actor_id": actor_id, "endpoint": endpoint, "response": response, "created_at": now_iso()}
        )
    except DuplicateKeyError:
        pass


# --- Auth ---------------------------------------------------------------------

@router.post("/auth/login")
async def login(body: LoginIn):
    user = await db.users.find_one({"email": body.email.lower(), "deleted_at": None})
    if not verify_password(body.password, user.get("password_hash") if user else None) or not (
        user and user.get("is_active")
    ):
        raise HTTPException(status_code=401, detail="Identifiants invalides")
    ctx = await build_context(user)
    token = create_access_token(user["_id"])
    return {"access_token": token, "token_type": "bearer", "actor": actor_summary(ctx)}


@router.get("/auth/me")
async def me(user: dict = Depends(current_user)):
    ctx = await build_context(user)
    return {"actor": actor_summary(ctx)}


# --- Relay point --------------------------------------------------------------

@router.get("/relay-points/me")
async def my_point(ctx: dict = Depends(partner_context)):
    count = await point_current_count(ctx["point"]["_id"])
    return point_public(ctx["point"], count)


@router.post("/relay-points/me/status")
async def set_point_status(body: StatusIn, ctx: dict = Depends(partner_context)):
    if ctx["membership"]["role"] != "RELAY_PARTNER_OWNER":
        raise HTTPException(status_code=403, detail="Seul le propriétaire peut changer le statut")
    allowed = {"ACTIVE", "TEMPORARILY_CLOSED"}
    if body.status not in allowed:
        raise HTTPException(status_code=422, detail="Statut non autorisé")
    before = ctx["point"].get("status")
    await db.relay_points.update_one({"_id": ctx["point"]["_id"]}, {"$set": {"status": body.status}})
    await write_audit(
        "point_status_changed", ctx["user"]["_id"], ctx["org"]["_id"], "relay_point", ctx["point"]["_id"],
        before={"status": before}, after={"status": body.status},
    )
    point = await db.relay_points.find_one({"_id": ctx["point"]["_id"]})
    count = await point_current_count(point["_id"])
    return point_public(point, count)


# --- Parcels ------------------------------------------------------------------

@router.get("/parcels")
async def list_parcels(
    filter: str = Query("all"),
    q: str | None = Query(None),
    ctx: dict = Depends(partner_context),
):
    query: dict = {"relay_point_id": ctx["point"]["_id"], "deleted_at": None}
    if filter in FILTER_MAP:
        query["state"] = {"$in": FILTER_MAP[filter]}
    if q:
        query["$or"] = [
            {"tracking_code": {"$regex": q, "$options": "i"}},
            {"recipient.name": {"$regex": q, "$options": "i"}},
        ]
    docs = await db.parcels.find(query).sort("updated_at", -1).to_list(300)
    return [parcel_public(d) for d in docs]


@router.get("/parcels/lookup")
async def lookup_parcel(code: str = Query(..., min_length=1), ctx: dict = Depends(partner_context)):
    """Resolve a scanned QR/barcode or a manually typed code — same code path."""
    normalized = code.strip().upper()
    doc = await db.parcels.find_one(
        {"tracking_code": normalized, "relay_point_id": ctx["point"]["_id"], "deleted_at": None}
    )
    if not doc:
        # Distinguish "exists elsewhere" (wrong point) from "unknown" without leaking data.
        other = await db.parcels.find_one({"tracking_code": normalized, "deleted_at": None})
        if other:
            await create_incident(ctx, IncidentType.WRONG_POINT.value, None,
                                  f"Colis {normalized} scanné hors de son point Relay.")
            raise HTTPException(status_code=409, detail="Ce colis n'appartient pas à ce point Relay")
        raise HTTPException(status_code=404, detail="Code inconnu")
    return parcel_public(doc)


@router.get("/parcels/{parcel_id}")
async def get_parcel(parcel_id: str, ctx: dict = Depends(partner_context)):
    return parcel_public(await get_scoped_parcel(parcel_id, ctx))


@router.get("/parcels/{parcel_id}/custody")
async def parcel_custody(parcel_id: str, ctx: dict = Depends(partner_context)):
    await get_scoped_parcel(parcel_id, ctx)
    events = await db.custody_events.find({"parcel_id": parcel_id}).sort("seq", 1).to_list(100)
    return [
        {
            "seq": e["seq"],
            "from": e.get("from_custodian"),
            "to": e.get("to_custodian"),
            "action": e.get("action"),
            "at": e.get("at"),
        }
        for e in events
    ]


@router.post("/parcels/{parcel_id}/receive")
async def receive(
    parcel_id: str,
    ctx: dict = Depends(partner_context),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    cached = await idempotent_get(idempotency_key)
    if cached:
        return cached
    parcel = await get_scoped_parcel(parcel_id, ctx)
    updated = await do_receive(ctx, parcel)
    resp = parcel_public(updated)
    resp["simulated_otp"] = updated.get("_simulated_otp")
    await idempotent_save(idempotency_key, ctx["user"]["_id"], "receive", resp)
    return resp


@router.post("/parcels/{parcel_id}/otp/resend")
async def resend_otp(parcel_id: str, ctx: dict = Depends(partner_context)):
    """Simulated OTP delivery: returns the code in the response for the demo.

    In production this endpoint would trigger the SMS/WhatsApp provider and NOT
    return the code. The plaintext is never persisted; only the hash is stored.
    """
    parcel = await get_scoped_parcel(parcel_id, ctx)
    if parcel["state"] not in (ParcelState.AVAILABLE_FOR_PICKUP.value, ParcelState.PICKUP_AUTHORIZED.value):
        raise HTTPException(status_code=409, detail="Colis non disponible pour l'envoi d'un code")
    code, expires_at = await issue_otp(parcel_id)
    await write_audit("otp_sent", ctx["user"]["_id"], ctx["org"]["_id"], "parcel", parcel_id, meta={"channel": "SIMULATED"})
    return {"simulated_otp": code, "expires_at": expires_at, "channel": "SIMULATED"}


@router.post("/parcels/{parcel_id}/pickup/authorize")
async def pickup_authorize(
    parcel_id: str,
    body: AuthorizeIn,
    ctx: dict = Depends(partner_context),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    cached = await idempotent_get(idempotency_key)
    if cached:
        return cached
    parcel = await get_scoped_parcel(parcel_id, ctx)
    updated = await authorize_pickup(ctx, parcel, body.otp, body.scan_code)
    resp = {"parcel": parcel_public(updated), "authorized": True}
    await idempotent_save(idempotency_key, ctx["user"]["_id"], "authorize", resp)
    return resp


@router.post("/parcels/{parcel_id}/pickup/complete")
async def pickup_complete(
    parcel_id: str,
    ctx: dict = Depends(partner_context),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    cached = await idempotent_get(idempotency_key)
    if cached:
        return cached
    parcel = await get_scoped_parcel(parcel_id, ctx)
    updated = await complete_pickup(ctx, parcel)
    receipt = await db.receipts.find_one({"_id": updated["receipt_id"]})
    resp = {"parcel": parcel_public(updated), "receipt": receipt_public(receipt)}
    await idempotent_save(idempotency_key, ctx["user"]["_id"], "complete", resp)
    return resp


@router.post("/parcels/{parcel_id}/refuse")
async def refuse(parcel_id: str, ctx: dict = Depends(partner_context)):
    parcel = await get_scoped_parcel(parcel_id, ctx)
    return parcel_public(await refuse_parcel(ctx, parcel))


@router.post("/parcels/{parcel_id}/expire")
async def expire(parcel_id: str, ctx: dict = Depends(partner_context)):
    parcel = await get_scoped_parcel(parcel_id, ctx)
    return parcel_public(await expire_parcel(ctx, parcel))


@router.post("/parcels/{parcel_id}/return")
async def parcel_return(
    parcel_id: str,
    body: ReturnIn,
    ctx: dict = Depends(partner_context),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    cached = await idempotent_get(idempotency_key)
    if cached:
        return cached
    parcel = await get_scoped_parcel(parcel_id, ctx)
    resp = parcel_public(await create_return(ctx, parcel, body.reason))
    await idempotent_save(idempotency_key, ctx["user"]["_id"], "return", resp)
    return resp


@router.post("/parcels/{parcel_id}/return/ready")
async def parcel_return_ready(parcel_id: str, ctx: dict = Depends(partner_context)):
    parcel = await get_scoped_parcel(parcel_id, ctx)
    return parcel_public(await mark_return_ready(ctx, parcel))


@router.get("/parcels/{parcel_id}/receipt")
async def get_receipt(parcel_id: str, ctx: dict = Depends(partner_context)):
    await get_scoped_parcel(parcel_id, ctx)
    receipt = await db.receipts.find_one({"parcel_id": parcel_id})
    if not receipt:
        raise HTTPException(status_code=404, detail="Aucun reçu pour ce colis")
    return receipt_public(receipt)


@router.post("/parcels/simulate-arrival")
async def simulate_arrival(ctx: dict = Depends(partner_context)):
    """Demo helper: simulate a carrier bringing a new IN_TRANSIT parcel to this
    point so the Partner has something to RECEIVE. Real arrivals come from the
    carrier/merchant APIs (out of scope for this first mobile build)."""
    merchant = await db.organizations.find_one({"type": "MERCHANT"})
    carrier = await db.organizations.find_one({"type": "CARRIER"})
    names = ["Hassan Alaoui", "Yasmine Benali", "Omar Idrissi", "Salma Tazi", "Karim Fassi", "Nadia Amrani"]
    import random

    name = random.choice(names)
    code = "RLY" + uuid.uuid4().hex[:8].upper()
    doc = {
        "_id": uuid.uuid4().hex,
        "tracking_code": code,
        "merchant_org_id": merchant["_id"] if merchant else None,
        "merchant_name": merchant.get("name") if merchant else "Marchand",
        "carrier_org_id": carrier["_id"] if carrier else None,
        "carrier_name": carrier.get("name") if carrier else "Transporteur",
        "relay_point_id": ctx["point"]["_id"],
        "state": ParcelState.IN_TRANSIT.value,
        "custodian": {"type": "CARRIER", "id": carrier["_id"] if carrier else None},
        "recipient": {"name": name, "phone": "+2126" + f"{random.randint(10000000, 99999999)}"},
        "service_type": "PICKUP",
        "weight_g": random.choice([250, 500, 900, 1500, 3000]),
        "size": random.choice(["S", "M", "L"]),
        "available_from": None,
        "pickup_deadline": None,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "deleted_at": None,
    }
    await db.parcels.insert_one(doc)
    await write_audit("parcel_created", ctx["user"]["_id"], ctx["org"]["_id"], "parcel", doc["_id"],
                      meta={"simulated": True})
    return parcel_public(doc)


# --- Incidents ----------------------------------------------------------------

@router.post("/incidents")
async def post_incident(body: IncidentIn, ctx: dict = Depends(partner_context)):
    valid_types = {t.value for t in IncidentType}
    if body.type not in valid_types:
        raise HTTPException(status_code=422, detail="Type d'incident invalide")
    parcel = None
    if body.parcel_id:
        parcel = await get_scoped_parcel(body.parcel_id, ctx)
    incident = await create_incident(ctx, body.type, parcel, body.description)
    return incident_public(incident)


@router.get("/incidents")
async def list_incidents(ctx: dict = Depends(partner_context)):
    docs = await db.incidents.find({"relay_point_id": ctx["point"]["_id"]}).sort("created_at", -1).to_list(200)
    return [incident_public(d) for d in docs]


# --- Stats --------------------------------------------------------------------

@router.get("/stats/today")
async def stats_today(ctx: dict = Depends(partner_context)):
    point_id = ctx["point"]["_id"]
    start = now_utc().astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    to_receive = await db.parcels.count_documents(
        {"relay_point_id": point_id, "state": ParcelState.IN_TRANSIT.value, "deleted_at": None}
    )
    available = await db.parcels.count_documents(
        {"relay_point_id": point_id, "state": ParcelState.AVAILABLE_FOR_PICKUP.value, "deleted_at": None}
    )
    received_today = await db.parcels.count_documents(
        {"relay_point_id": point_id, "received_at": {"$gte": start}, "deleted_at": None}
    )
    handed_today = await db.parcels.count_documents(
        {"relay_point_id": point_id, "handed_over_at": {"$gte": start}, "deleted_at": None}
    )
    open_incidents = await db.incidents.count_documents({"relay_point_id": point_id, "status": "OPEN"})
    return {
        "to_receive": to_receive,
        "available": available,
        "received_today": received_today,
        "handed_over_today": handed_today,
        "open_incidents": open_incidents,
    }
