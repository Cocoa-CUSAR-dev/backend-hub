import asyncio

import bcrypt
import pytest

from src.auth import passwords
from src.exceptions import Invalid


async def test_hash_and_verify() -> None:
    hashed = await passwords.hash_password("secret1")
    assert await passwords.verify_password("secret1", hashed)
    assert not await passwords.verify_password("secret2", hashed)


async def test_hashes_written_by_go_and_spring_still_verify() -> None:
    # Go's x/crypto/bcrypt and Spring's BCryptPasswordEncoder write "$2a$".
    legacy = bcrypt.hashpw(b"farmer-pass", bcrypt.gensalt(prefix=b"2a")).decode()
    assert legacy.startswith("$2a$")
    assert await passwords.verify_password("farmer-pass", legacy)


async def test_accounts_without_a_password_never_match() -> None:
    assert not await passwords.verify_password("anything", None)
    assert not await passwords.verify_password("anything", "not-a-bcrypt-hash")


async def test_bcrypt_does_not_block_the_event_loop() -> None:
    # A bcrypt call takes ~250 ms of CPU. Run on the event loop, it stalled
    # every other request while anyone signed in (docs-and-plan#322). Here
    # another coroutine must keep running while a password is checked.
    hashed = await passwords.hash_password("secret1")
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.005)
            ticks += 1

    task = asyncio.create_task(ticker())
    try:
        assert await passwords.verify_password("secret1", hashed)
        assert await passwords.hash_password("secret2")
    finally:
        task.cancel()
    assert ticks >= 5, f"the event loop only ran {ticks} times during two bcrypt calls"


def test_minimum_length_is_six() -> None:
    passwords.check_strength("123456")
    with pytest.raises(Invalid) as err:
        passwords.check_strength("12345")
    assert "password" in err.value.fields
