"""Presentation shaping: never return raw Mongo docs; strip/mask sensitive data.

Data minimization for the Partner: the recipient's full phone is stored but the
Partner only ever sees a masked form (e.g. "Hassan • ••••42").
"""
from .models import allowed_actions
from .utils import now_utc, parse_dt


def mask_phone(phone: str | None) -> str:
    if not phone:
        return "••••"
    digits = "".join(c for c in phone if c.isdigit())
    return "••••" + digits[-2:] if len(digits) >= 2 else "••••"


def recipient_public(recipient: dict | None) -> dict:
    recipient = recipient or {}
    full = (recipient.get("name") or "").strip()
    first = full.split(" ")[0] if full else "Client"
    return {"name": first, "phone_masked": mask_phone(recipient.get("phone"))}


def custodian_label(custodian: dict | None) -> str:
    if not custodian:
        return "—"
    return custodian.get("type", "—")


def parcel_public(doc: dict) -> dict:
    deadline = parse_dt(doc.get("pickup_deadline"))
    past_deadline = bool(deadline and deadline < now_utc())
    return {
        "id": doc["_id"],
        "tracking_code": doc["tracking_code"],
        "state": doc["state"],
        "custodian": doc.get("custodian"),
        "custodian_label": custodian_label(doc.get("custodian")),
        "recipient": recipient_public(doc.get("recipient")),
        "merchant_name": doc.get("merchant_name"),
        "carrier_name": doc.get("carrier_name"),
        "relay_point_id": doc.get("relay_point_id"),
        "service_type": doc.get("service_type"),
        "weight_g": doc.get("weight_g"),
        "size": doc.get("size"),
        "available_from": doc.get("available_from"),
        "pickup_deadline": doc.get("pickup_deadline"),
        "past_deadline": past_deadline,
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
        "allowed_actions": allowed_actions(doc["state"], past_deadline),
    }


def point_public(doc: dict, current_count: int) -> dict:
    capacity = doc.get("capacity", 0)
    return {
        "id": doc["_id"],
        "name": doc["name"],
        "address": doc.get("address"),
        "city": doc.get("city"),
        "postal_code": doc.get("postal_code"),
        "latitude": doc.get("latitude"),
        "longitude": doc.get("longitude"),
        "opening_hours": doc.get("opening_hours"),
        "capacity": capacity,
        "current_count": current_count,
        "occupancy_pct": round(100 * current_count / capacity) if capacity else 0,
        "services": doc.get("services", []),
        "status": doc.get("status"),
    }


def incident_public(doc: dict) -> dict:
    return {
        "id": doc["_id"],
        "type": doc["type"],
        "parcel_id": doc.get("parcel_id"),
        "parcel_tracking_code": doc.get("parcel_tracking_code"),
        "description": doc.get("description"),
        "status": doc.get("status"),
        "resolution": doc.get("resolution"),
        "created_at": doc.get("created_at"),
    }


def receipt_public(doc: dict) -> dict:
    return {
        "id": doc["_id"],
        "parcel_id": doc["parcel_id"],
        "parcel_tracking_code": doc.get("parcel_tracking_code"),
        "relay_point_id": doc.get("relay_point_id"),
        "relay_point_name": doc.get("relay_point_name"),
        "partner_user_id": doc.get("partner_user_id"),
        "partner_name": doc.get("partner_name"),
        "recipient": recipient_public(doc.get("recipient")),
        "pickup_auth_method": doc.get("pickup_auth_method"),
        "authentication_success": doc.get("authentication_success"),
        "parcel_scan": doc.get("parcel_scan"),
        "final_action": doc.get("final_action"),
        "incident_flag": doc.get("incident_flag"),
        "timestamp": doc.get("timestamp"),
    }
