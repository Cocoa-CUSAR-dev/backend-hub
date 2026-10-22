import uuid
from datetime import timedelta
from unittest.mock import patch

import jwt
import pytest

from src.auth import tokens
from src.clock import utc_now
from src.config import settings
from src.exceptions import Unauthorized

USER = uuid.uuid4()


def _access() -> str:
    return tokens.issue_access_token(
        USER, "0812345678", frozenset({"farmer", "processor"}), frozenset({"read:farm:own"})
    )


def test_access_token_round_trip_keeps_every_role() -> None:
    claims = tokens.read_access_token(_access())
    assert claims.user_id == USER
    assert claims.roles == {"farmer", "processor"}
    assert claims.perms == {"read:farm:own"}


def test_access_token_carries_the_claims_the_old_backends_read() -> None:
    raw = jwt.decode(_access(), settings.JWT_KEY, algorithms=["HS256"])
    # mobile-backend reads user_id + roles; web-backend reads userId + permissions.
    assert raw["user_id"] == raw["userId"] == str(USER)
    assert raw["roles"] == ["farmer", "processor"]
    assert raw["permissions"] == ["read:farm:own"]


def test_expired_access_token_is_refused() -> None:
    with patch("src.auth.tokens.utc_now", return_value=utc_now() - timedelta(days=1)):
        old = _access()
    with pytest.raises(Unauthorized, match="expired"):
        tokens.read_access_token(old)


def test_token_signed_with_another_key_is_refused() -> None:
    forged = jwt.encode(
        {"sub": str(USER), "typ": "access", "exp": utc_now() + timedelta(hours=1)},
        "some-other-key-that-is-also-32-bytes-long!",
        algorithm="HS256",
    )
    with pytest.raises(Unauthorized):
        tokens.read_access_token(forged)


def test_sso_and_access_tokens_cannot_stand_in_for_each_other() -> None:
    sso, jti, _ = tokens.issue_sso_token(USER)
    with pytest.raises(Unauthorized):
        tokens.read_access_token(sso)
    with pytest.raises(Unauthorized):
        tokens.read_sso_token(_access())
    user_id, read_jti, _ = tokens.read_sso_token(sso)
    assert (user_id, read_jti) == (USER, jti)


def test_refresh_tokens_are_random_and_stored_hashed() -> None:
    a, b = tokens.new_refresh_token(), tokens.new_refresh_token()
    assert a != b and len(a) == 64
    assert tokens.hash_refresh_token(a) != a
    assert tokens.hash_refresh_token(a) == tokens.hash_refresh_token(a)
