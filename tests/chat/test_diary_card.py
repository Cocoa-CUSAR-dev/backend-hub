from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from src.chat import router
from src.config import settings
from src.exceptions import DiaryNotAvailable


@asynccontextmanager
async def _no_session() -> AsyncIterator[Any]:
    yield object()


def _diary(**generate: Any) -> Any:
    """The diary is written in-process now: no database, a fixed actor."""
    return (
        patch("src.chat.router.async_session_maker", _no_session),
        patch("src.chat.router.load_actor", AsyncMock(return_value="actor")),
        patch("src.chat.router.diary_service.generate_for", AsyncMock(**generate)),
    )


# US3-2 / #125 (F2): the diary card's "view full history" deep link must carry
# the SSO token in the URL *fragment* (`#token=`), never the query string
# (`?token=`) -- the fragment is never sent to the server, so the token can't
# land in access logs or a Referer header. Red before the fix (was `?token=`),
# green after.
async def test_diary_card_link_puts_token_in_fragment_not_query() -> None:
    captured: dict[str, str] = {}

    def fake_build_diary_flex(diary_text: str, history_url: str) -> dict:
        captured["history_url"] = history_url
        captured["diary_text"] = diary_text
        return {"type": "flex"}

    session, actor, generate = _diary(return_value="diary text")
    with (
        session,
        actor,
        generate as generated,
        patch("src.chat.router.mint_sso_token", AsyncMock(return_value="TOK123")),
        patch("src.chat.router.build_diary_flex", side_effect=fake_build_diary_flex),
        patch("src.chat.router.push_flex", AsyncMock()),
    ):
        await router._generate_and_push_diary(str(uuid4()), "Uabc")

    url = captured["history_url"]
    assert url == f"{settings.WEB_APP_URL}/sso#token=TOK123"
    assert "?token=" not in url
    assert captured["diary_text"] == "diary text"
    assert generated.await_args.args[1] == "actor"  # as the farmer, not a service key


async def test_no_diary_means_no_card_and_no_error() -> None:
    session, actor, generate = _diary(side_effect=DiaryNotAvailable("nothing today"))
    with session, actor, generate, patch("src.chat.router.push_flex", AsyncMock()) as push:
        await router._generate_and_push_diary(str(uuid4()), "Uabc")

    push.assert_not_awaited()
