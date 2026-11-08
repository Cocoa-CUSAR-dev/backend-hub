"""Access tokens (JWT), refresh tokens (opaque) and one-time SSO tokens.

Pure functions: no DB, no HTTP. src/auth/service.py decides when to issue.
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID

import jwt

from src.clock import utc_now
from src.config import settings
from src.exceptions import Unauthorized

_ALGORITHM = "HS256"
TokenType = Literal["access", "sso"]


@dataclass(frozen=True, slots=True)
class AccessClaims:
    user_id: UUID
    username: str
    roles: frozenset[str]
    perms: frozenset[str]


def issue_access_token(
    user_id: UUID, username: str, roles: frozenset[str], perms: frozenset[str]
) -> str:
    now = utc_now()
    claims: dict[str, Any] = {
        "sub": str(user_id),
        "username": username,
        "roles": sorted(roles),
        "perms": sorted(perms),
        "typ": "access",
        "iat": now,
        "exp": now + timedelta(seconds=settings.ACCESS_TOKEN_TTL_SECONDS),
        "jti": str(uuid.uuid4()),
        # ---- read by the old backends while they still run; delete with them.
        # mobile-backend's JwtAuthMiddleware reads user_id + roles.
        "user_id": str(user_id),
        # web-backend's JwtAuthenticationFilter reads userId + permissions
        # (it takes `sub` as the username but only compares it with itself).
        "userId": str(user_id),
        "permissions": sorted(perms),
    }
    return jwt.encode(claims, settings.JWT_KEY, algorithm=_ALGORITHM)


def issue_sso_token(user_id: UUID) -> tuple[str, UUID, datetime]:
    """Short, single-use token for the LINE diary card's web link.
    Returns (token, jti, expires_at) -- the jti is claimed on exchange.
    """
    jti = uuid.uuid4()
    expires_at = utc_now() + timedelta(seconds=settings.SSO_TOKEN_TTL_SECONDS)
    token = jwt.encode(
        {"sub": str(user_id), "typ": "sso", "jti": str(jti), "exp": expires_at},
        settings.JWT_KEY,
        algorithm=_ALGORITHM,
    )
    return token, jti, expires_at


def _decode(token: str, expected: TokenType) -> dict[str, Any]:
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            settings.JWT_KEY,
            algorithms=[_ALGORITHM],
            options={"require": ["sub", "exp", "typ"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("Token expired") from exc
    except jwt.PyJWTError as exc:
        raise Unauthorized("Invalid token") from exc
    if claims.get("typ") != expected:
        # An SSO link token must never work as an API credential, and an
        # access token must never be redeemable as an SSO token.
        raise Unauthorized("Invalid token")
    return claims


def read_access_token(token: str) -> AccessClaims:
    claims = _decode(token, "access")
    try:
        return AccessClaims(
            user_id=UUID(claims["sub"]),
            username=str(claims.get("username", "")),
            roles=frozenset(claims.get("roles", [])),
            perms=frozenset(claims.get("perms", [])),
        )
    except (ValueError, TypeError) as exc:
        raise Unauthorized("Invalid token") from exc


def read_sso_token(token: str) -> tuple[UUID, UUID, datetime]:
    """(user_id, jti, expires_at) of a still-valid SSO token."""
    claims = _decode(token, "sso")
    try:
        expires_at = datetime.fromtimestamp(claims["exp"], tz=utc_now().tzinfo)
        return UUID(claims["sub"]), UUID(claims["jti"]), expires_at
    except (KeyError, ValueError, TypeError) as exc:
        raise Unauthorized("Invalid token") from exc


# ---- refresh tokens --------------------------------------------------------
# Opaque random values; only their sha256 is stored (auth.refresh_token), the
# same scheme mobile-backend uses, so a token from either side can be rotated.


def new_refresh_token() -> str:
    return secrets.token_hex(32)


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def refresh_expires_at() -> datetime:
    return utc_now() + timedelta(days=settings.REFRESH_TOKEN_TTL_DAYS)
