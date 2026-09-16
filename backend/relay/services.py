"""Business operations for the parcel lifecycle.

Invariants enforced here:
- Every state change is an atomic, conditional DB update (concurrency-safe):
  two agents acting on the same parcel -> exactly one wins, the other gets 409.
- A state change always carries a coherent custody transfer + audit entry.
- Pickup requires cross-validation: QR(scan) + OTP + same parcel + same point
  + eligible state + authorized partner + current custody.
- OTP is hashed, single-use, expiring, attempt-limited; never stored in clear.
- Notifications go through the outbox (never inline), so provider outages can't
  roll back a committed operation.
"""
import uuid

from fastapi import HTTPException
from pymongo import ReturnDocument

from .audit import enqueue_event, record_custody, write_audit
from .database import (
    DEFAULT_PICKUP_WINDOW_HOURS,
    OTP_MAX_ATTEMPTS,
    OTP_TTL_MINUTES,
    db,
)
from .models import IN_CUSTODY_STATES, CustodianType, IncidentType, ParcelState, TRANSITIONS
from .security import generate_otp_code, hash_otp, verify_otp
from .utils import now_iso, now_utc, parse_dt
from datetime import timedelta


async def point_current_count(relay_point_id: str) -> int:
    return await db.parcels.count_documents(
        {"relay_point_id": relay_point_id, "state": {"$in": list(IN_CUSTODY_STATES)}, "deleted_at": None}
    )


async def _atomic_transition(parcel_id: str, action: str, set_fields: dict) -> dict | None:
    spec = TRANSITIONS[action]
    update = {"state": spec["to"], "updated_at": now_iso(), **set_fields}
    return await db.parcels.find_one_and_update(
        {"_id": parcel_id, "state": {"$in": list(spec["from"])}, "deleted_at": None},
        {"$set": update},
        return_document=ReturnDocument.AFTER,
    )


def _conflict(detail: str):
    return HTTPException(status_code=409, detail=detail)


# --- OTP -----------------------------------------------------------------------

async def issue_otp(parcel_id: str) -> tuple[str, str]:
    """Generate a fresh single-use OTP, replacing any previous one.

    Returns (plaintext_code, expires_at_iso). Plaintext is returned to the caller
    ONLY for the simulated delivery channel and is never persisted in clear.
    """
    code = generate_otp_code()
    expires_at = (now_utc() + timedelta(minutes=OTP_TTL_MINUTES)).isoformat()
    await db.otps.update_one(
        {"parcel_id": parcel_id},
        {
            "$set": {
                "parcel_id": parcel_id,
                "code_hash": hash_otp(code),
                "expires_at": expires_at,
                "used": False,
                "attempts": 0,
                "max_attempts": OTP_MAX_ATTEMPTS,
                "created_at": now_iso(),
            }
        },
        upsert=True,
    )
    return code, expires_at


async def _fail_otp(parcel: dict, actor: dict, reason: str, attempts_left: int):
    await write_audit(
        "otp_failed", actor["user"]["_id"], actor["org"]["_id"], "parcel", parcel["_id"],
        meta={"reason": reason, "attempts_left": attempts_left},
    )


async def validate_otp(parcel: dict, actor: dict, code: str) -> None:
    otp = await db.otps.find_one({"parcel_id": parcel["_id"]})
    if not otp:
        await _fail_otp(parcel, actor, "no_active_otp", 0)
        raise HTTPException(status_code=422, detail="Aucun code actif pour ce colis")
    if otp.get("used"):
        await _fail_otp(parcel, actor, "already_used", 0)
        raise HTTPException(status_code=422, detail="Code déjà utilisé")
    if parse_dt(otp.get("expires_at")) and parse_dt(otp["expires_at"]) < now_utc():
        await _fail_otp(parcel, actor, "expired", 0)
        raise HTTPException(status_code=422, detail="Code expiré")
    if otp.get("attempts", 0) >= otp.get("max_attempts", OTP_MAX_ATTEMPTS):
        await _fail_otp(parcel, actor, "locked", 0)
        raise HTTPException(status_code=429, detail="Trop de tentatives, code bloqué")

    if not verify_otp(code, otp["code_hash"]):
        updated = await db.otps.find_one_and_update(
            {"parcel_id": parcel["_id"]}, {"$inc": {"attempts": 1}}, return_document=ReturnDocument.AFTER
        )
        attempts_left = max(0, updated.get("max_attempts", OTP_MAX_ATTEMPTS) - updated.get("attempts", 0))
        await _fail_otp(parcel, actor, "wrong_code", attempts_left)
        if attempts_left <= 0:
            await db.otps.update_one({"parcel_id": parcel["_id"]}, {"$set": {"used": True}})
            await create_incident(
                actor, IncidentType.OTP_FAILURE.value, parcel,
                "Blocage OTP: trop de tentatives incorrectes.",
            )
            raise HTTPException(status_code=429, detail="Code incorrect. Code bloqué après trop de tentatives.")
        raise HTTPException(status_code=422, detail=f"Code incorrect. {attempts_left} tentative(s) restante(s).")

    await write_audit(
        "otp_verified", actor["user"]["_id"], actor["org"]["_id"], "parcel", parcel["_id"],
    )


# --- Parcel operations ---------------------------------------------------------

async def do_receive(actor: dict, parcel: dict) -> dict:
    point = actor["point"]
    if point.get("status") not in ("ACTIVE",):
        raise HTTPException(status_code=409, detail="Point Relay indisponible (fermé/plein/suspendu)")
    count = await point_current_count(point["_id"])
    if count >= point.get("capacity", 0):
        raise HTTPException(status_code=409, detail="Point Relay plein: capacité atteinte")

    available_from = now_utc()
    deadline = available_from + timedelta(hours=DEFAULT_PICKUP_WINDOW_HOURS)
    custodian = {"type": CustodianType.RELAY_PARTNER.value, "id": point["_id"]}
    updated = await _atomic_transition(
        parcel["_id"], "RECEIVE",
        {
            "custodian": custodian,
            "available_from": available_from.isoformat(),
            "pickup_deadline": deadline.isoformat(),
            "received_at": available_from.isoformat(),
        },
    )
    if not updated:
        raise _conflict("Colis déjà reçu ou état non éligible à la réception")

    code, _ = await issue_otp(parcel["_id"])
    await record_custody(
        parcel["_id"], parcel.get("custodian"), custodian, actor["user"]["_id"], "RECEIVE"
    )
    await write_audit(
        "parcel_received", actor["user"]["_id"], actor["org"]["_id"], "parcel", parcel["_id"],
        before={"state": parcel["state"]}, after={"state": updated["state"]},
    )
    await enqueue_event("parcel.available", {"parcel_id": parcel["_id"], "channel": "SMS"})
    updated["_simulated_otp"] = code
    return updated


async def authorize_pickup(actor: dict, parcel: dict, otp_code: str, scan_code: str) -> dict:
    point = actor["point"]
    # Cross-validation: QR(scan) must match the very parcel being authorized.
    if (scan_code or "").strip().upper() != parcel["tracking_code"].upper():
        await write_audit(
            "pickup_denied", actor["user"]["_id"], actor["org"]["_id"], "parcel", parcel["_id"],
            meta={"reason": "scan_parcel_mismatch"},
        )
        raise HTTPException(status_code=422, detail="Le colis scanné ne correspond pas")
    # Custody must currently be this relay point.
    custodian = parcel.get("custodian") or {}
    if custodian.get("type") != CustodianType.RELAY_PARTNER.value or custodian.get("id") != point["_id"]:
        raise HTTPException(status_code=409, detail="Le point Relay n'a pas la garde de ce colis")

    await validate_otp(parcel, actor, otp_code)

    updated = await _atomic_transition(parcel["_id"], "AUTHORIZE", {"pickup_authorized_at": now_iso()})
    if not updated:
        raise _conflict("Colis non disponible pour autorisation (déjà remis ?)")
    await write_audit(
        "pickup_authorized", actor["user"]["_id"], actor["org"]["_id"], "parcel", parcel["_id"],
        before={"state": parcel["state"]}, after={"state": updated["state"]},
    )
    return updated


async def complete_pickup(actor: dict, parcel: dict) -> dict:
    point = actor["point"]
    customer_custodian = {"type": CustodianType.CUSTOMER.value, "id": (parcel.get("recipient") or {}).get("phone")}
    updated = await _atomic_transition(
        parcel["_id"], "COMPLETE", {"custodian": customer_custodian, "handed_over_at": now_iso()}
    )
    if not updated:
        raise _conflict("Remise déjà effectuée ou état non éligible")

    # Invalidate the OTP after a successful handover.
    await db.otps.update_one({"parcel_id": parcel["_id"]}, {"$set": {"used": True}})
    await record_custody(
        parcel["_id"], parcel.get("custodian"), customer_custodian, actor["user"]["_id"], "COMPLETE"
    )

    receipt_id = uuid.uuid4().hex
    receipt = {
        "_id": receipt_id,
        "parcel_id": parcel["_id"],
        "parcel_tracking_code": parcel["tracking_code"],
        "relay_point_id": point["_id"],
        "relay_point_name": point["name"],
        "partner_user_id": actor["user"]["_id"],
        "partner_name": actor["user"].get("name"),
        "recipient": parcel.get("recipient"),
        "pickup_auth_method": "OTP",
        "authentication_success": True,
        "parcel_scan": parcel["tracking_code"],
        "final_action": ParcelState.HANDED_OVER.value,
        "incident_flag": False,
        "timestamp": now_iso(),
    }
    await db.receipts.insert_one(receipt)
    await db.parcels.update_one({"_id": parcel["_id"]}, {"$set": {"receipt_id": receipt_id}})
    await write_audit(
        "parcel_handed_over", actor["user"]["_id"], actor["org"]["_id"], "parcel", parcel["_id"],
        before={"state": parcel["state"]}, after={"state": updated["state"]}, meta={"receipt_id": receipt_id},
    )
    await enqueue_event("parcel.picked_up", {"parcel_id": parcel["_id"], "receipt_id": receipt_id})
    updated["receipt_id"] = receipt_id
    return updated


async def _simple_transition(actor: dict, parcel: dict, action: str, audit_action: str, set_fields: dict | None = None):
    updated = await _atomic_transition(parcel["_id"], action, set_fields or {})
    if not updated:
        raise _conflict("Transition non autorisée depuis l'état actuel")
    await write_audit(
        audit_action, actor["user"]["_id"], actor["org"]["_id"], "parcel", parcel["_id"],
        before={"state": parcel["state"]}, after={"state": updated["state"]},
    )
    return updated


async def refuse_parcel(actor: dict, parcel: dict) -> dict:
    updated = await _simple_transition(actor, parcel, "REFUSE", "parcel_refused")
    await enqueue_event("parcel.refused", {"parcel_id": parcel["_id"]})
    return updated


async def expire_parcel(actor: dict, parcel: dict) -> dict:
    deadline = parse_dt(parcel.get("pickup_deadline"))
    if not (deadline and deadline < now_utc()):
        raise HTTPException(status_code=422, detail="Le délai de retrait n'est pas encore dépassé")
    updated = await _simple_transition(actor, parcel, "EXPIRE", "parcel_expired")
    await enqueue_event("parcel.expired", {"parcel_id": parcel["_id"]})
    return updated


async def create_return(actor: dict, parcel: dict, reason: str) -> dict:
    updated = await _simple_transition(actor, parcel, "RETURN_CREATE", "parcel_return_requested",
                                       {"return_reason": reason})
    await db.returns.insert_one(
        {
            "_id": uuid.uuid4().hex,
            "original_parcel_id": parcel["_id"],
            "parcel_tracking_code": parcel["tracking_code"],
            "relay_point_id": actor["point"]["_id"],
            "reason": reason,
            "state": ParcelState.RETURN_PENDING.value,
            "created_at": now_iso(),
        }
    )
    await enqueue_event("parcel.return_requested", {"parcel_id": parcel["_id"], "reason": reason})
    return updated


async def mark_return_ready(actor: dict, parcel: dict) -> dict:
    updated = await _simple_transition(actor, parcel, "RETURN_READY", "parcel_return_ready")
    await db.returns.update_one(
        {"original_parcel_id": parcel["_id"]}, {"$set": {"state": ParcelState.RETURN_READY.value}}
    )
    return updated


async def create_incident(actor: dict, incident_type: str, parcel: dict | None, description: str) -> dict:
    incident = {
        "_id": uuid.uuid4().hex,
        "type": incident_type,
        "actor_user_id": actor["user"]["_id"],
        "org_id": actor["org"]["_id"],
        "relay_point_id": actor["point"]["_id"],
        "parcel_id": parcel["_id"] if parcel else None,
        "parcel_tracking_code": parcel["tracking_code"] if parcel else None,
        "description": description,
        "status": "OPEN",
        "resolution": None,
        "created_at": now_iso(),
    }
    await db.incidents.insert_one(incident)
    await write_audit(
        "incident_created", actor["user"]["_id"], actor["org"]["_id"], "incident", incident["_id"],
        meta={"type": incident_type, "parcel_id": incident["parcel_id"]},
    )
    if incident_type == IncidentType.DAMAGED_PACKAGE.value and parcel:
        await _atomic_transition(parcel["_id"], "DAMAGE", {})
    return incident
