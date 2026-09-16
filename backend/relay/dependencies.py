"""Request-scoped identity & authorization.

A valid JWT only proves *who* the caller is. Every access decision below is
re-derived from the database: active user, membership, role, org and the relay
point the actor is bound to. Nothing about roles/org/point is trusted from the
client or the token.
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from .database import db
from .models import PARTNER_ROLES
from .security import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=True)


async def current_user(token: str = Depends(oauth2_scheme)) -> dict:
    user_id = decode_access_token(token)
    user = await db.users.find_one({"_id": user_id, "is_active": True, "deleted_at": None})
    if not user:
        raise HTTPException(status_code=401, detail="Acteur inconnu ou désactivé")
    return user


async def build_context(user: dict) -> dict:
    """Resolve the authoritative actor context from the DB."""
    membership = await db.memberships.find_one({"user_id": user["_id"], "active": True})
    if not membership:
        raise HTTPException(status_code=403, detail="Aucune organisation associée")
    org = await db.organizations.find_one({"_id": membership["org_id"]})
    point = None
    if membership.get("relay_point_id"):
        point = await db.relay_points.find_one({"_id": membership["relay_point_id"]})
    return {"user": user, "membership": membership, "org": org, "point": point}


async def partner_context(user: dict = Depends(current_user)) -> dict:
    """Authorize a Relay Partner actor bound to exactly one active relay point.

    Enforces: partner role + resolved point + point not decommissioned/suspended
    for read access. Write-time point-status gates (closed/full) live in services.
    """
    ctx = await build_context(user)
    if ctx["membership"]["role"] not in PARTNER_ROLES:
        raise HTTPException(status_code=403, detail="Accès réservé aux partenaires Relay")
    if not ctx["point"]:
        raise HTTPException(status_code=403, detail="Aucun point Relay rattaché à ce compte")
    if ctx["point"].get("status") == "DECOMMISSIONED":
        raise HTTPException(status_code=403, detail="Point Relay décommissionné")
    if ctx["point"].get("status") == "SUSPENDED":
        raise HTTPException(status_code=403, detail="Point Relay suspendu")
    return ctx


def require_status(*allowed: int):  # noqa: D401 - simple factory, currently unused hook
    return allowed
