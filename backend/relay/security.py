"""Authentication primitives only (who is this?). Authorization lives elsewhere.

- Passwords: bcrypt with a constant dummy-hash path to blunt user enumeration.
- JWT: short-ish signed access token carrying only an immutable user id (sub).
  Roles / org / point are NEVER trusted from the token; they are resolved from
  the DB on every request.
- OTP: 6 digits, generated server-side, bcrypt-hashed before storage, never
  persisted in plaintext.
"""
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import HTTPException, status

from .database import (
    ACCESS_TOKEN_MINUTES,
    JWT_ALGORITHM,
    JWT_AUDIENCE,
    JWT_ISSUER,
    JWT_SECRET,
)

_DUMMY_HASH = bcrypt.hashpw(b"relay-dummy-never-matches", bcrypt.gensalt(rounds=12)).decode()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, encoded_hash: str | None) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), (encoded_hash or _DUMMY_HASH).encode())
    except (ValueError, TypeError):
        return False


def create_access_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    claims = {
        "sub": str(user_id),
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "iat": now,
        "exp": now + timedelta(minutes=ACCESS_TOKEN_MINUTES),
        "typ": "access",
    }
    return jwt.encode(claims, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> str:
    try:
        payload = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM],
            issuer=JWT_ISSUER,
            audience=JWT_AUDIENCE,
            options={"require": ["sub", "exp", "iat", "iss", "aud"]},
        )
        if payload.get("typ") != "access":
            raise jwt.InvalidTokenError("wrong token type")
        return str(payload["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalide ou expiré",
            headers={"WWW-Authenticate": "Bearer"},
        )


# --- OTP ----------------------------------------------------------------------

def generate_otp_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp(code: str) -> str:
    return bcrypt.hashpw(code.encode(), bcrypt.gensalt(rounds=10)).decode()


def verify_otp(code: str, encoded_hash: str) -> bool:
    try:
        return bcrypt.checkpw(code.encode(), encoded_hash.encode())
    except (ValueError, TypeError):
        return False
