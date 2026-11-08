from unittest.mock import AsyncMock

import pytest

from src.database import transaction


async def test_transaction_commits_when_the_block_finishes() -> None:
    session = AsyncMock()
    async with transaction(session):
        pass
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()


async def test_transaction_rolls_back_and_reraises_on_error() -> None:
    session = AsyncMock()
    with pytest.raises(ValueError):
        async with transaction(session):
            raise ValueError("second write failed")
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
