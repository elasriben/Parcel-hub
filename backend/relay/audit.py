"""Append-only audit log, custody ledger and notification outbox.

These helpers are called from within business operations so that every
security- and business-critical event leaves a trace. The audit log is written
insert-only; nothing here ever updates or deletes an audit row.
"""
import uuid
from datetime import datetime, timezone

from .database import db


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def write_audit(
    action: str,
    actor_user_id: str | None,
    org_id: str | None,
    resource_type: str,
    resource_id: str | None,
    *,
    before: dict | None = None,
    after: dict | None = None,
    meta: dict | None = None,
) -> None:
    await db.audit_log.insert_one(
        {
            "_id": uuid.uuid4().hex,
            "action": action,
            "actor_user_id": actor_user_id,
            "org_id": org_id,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "before": before,
            "after": after,
            "meta": meta or {},
            "at": _now().isoformat(),
        }
    )


async def record_custody(
    parcel_id: str,
    from_custodian: dict | None,
    to_custodian: dict,
    actor_user_id: str | None,
    action: str,
) -> None:
    seq = await db.custody_events.count_documents({"parcel_id": parcel_id})
    await db.custody_events.insert_one(
        {
            "_id": uuid.uuid4().hex,
            "parcel_id": parcel_id,
            "seq": seq,
            "from_custodian": from_custodian,
            "to_custodian": to_custodian,
            "actor_user_id": actor_user_id,
            "action": action,
            "at": _now().isoformat(),
        }
    )


async def enqueue_event(event_type: str, payload: dict) -> None:
    """Outbox pattern: business truth is committed first; delivery is async.

    The notification provider is intentionally not called inline, so a provider
    outage never rolls back a completed parcel operation. A real SMS/WhatsApp
    worker would drain PENDING rows. OTP codes are NEVER written here.
    """
    await db.outbox.insert_one(
        {
            "_id": uuid.uuid4().hex,
            "event_type": event_type,
            "payload": payload,
            "status": "PENDING",
            "created_at": _now().isoformat(),
            "sent_at": None,
        }
    )
