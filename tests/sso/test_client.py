"""src/sso/client.py: the diary card's deep-link token is minted in-process
and is exactly what POST /api/v1/auth/sso/exchange redeems."""

import uuid

import pytest

from src.auth import tokens
from src.exceptions import Unauthorized
from src.sso.client import mint_sso_token


async def test_mints_an_sso_token_for_that_user() -> None:
    user_id = uuid.uuid4()

    token = await mint_sso_token(str(user_id))

    minted_for, _, _ = tokens.read_sso_token(token)
    assert minted_for == user_id


async def test_the_link_token_is_not_an_api_token() -> None:
    token = await mint_sso_token(uuid.uuid4())

    with pytest.raises(Unauthorized):
        tokens.read_access_token(token)
