"""Access tokens (JWT), refresh tokens (opaque), one-time SSO tokens and
forgot-password codes.

Pure functions: no DB, no HTTP. src/auth/service.py decides when to issue.
"""

import hashlib
import hmac
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


# ---- forgot-password codes ---------------------------------------------------
# A short number the person types, so a link is not needed (the app has no deep
# link). Only an HMAC is stored (auth.password_reset_code), keyed with JWT_KEY
# and tied to the user: a copy of the table cannot be turned back into codes
# without the key, and one account's hash means nothing for another.

RESET_CODE_DIGITS = 6
_THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")


def new_reset_code() -> str:
    return f"{secrets.randbelow(10**RESET_CODE_DIGITS):0{RESET_CODE_DIGITS}d}"


def normalize_reset_code(code: str) -> str:
    """What the person typed, as digits: no spaces, Thai digits as Arabic."""
    return "".join(code.split()).translate(_THAI_DIGITS)


def hash_reset_code(user_id: UUID, code: str) -> str:
    return hmac.new(
        settings.JWT_KEY.encode(),
        f"password-reset:{user_id}:{normalize_reset_code(code)}".encode(),
        hashlib.sha256,
    ).hexdigest()


def reset_code_matches(stored_hash: str, user_id: UUID, code: str) -> bool:
    return hmac.compare_digest(stored_hash, hash_reset_code(user_id, code))
