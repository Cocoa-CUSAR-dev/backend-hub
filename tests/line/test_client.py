import base64
import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from linebot.v3.messaging import MessageAction, RichMenuBounds

from src.config import settings
from src.exceptions import InvalidLineSignature
from src.line import client
from src.line.client import TextMessage


def _patched_api_client():
    api = MagicMock()
    api.__aenter__ = AsyncMock(return_value=api)
    api.__aexit__ = AsyncMock(return_value=False)
    return patch("src.line.client.AsyncApiClient", return_value=api)


async def test_create_rich_menu_returns_the_new_menu_id() -> None:
    messaging_api = MagicMock()
    messaging_api.create_rich_menu = AsyncMock(
        return_value=MagicMock(rich_menu_id="richmenu-abc123")
    )
    bounds = RichMenuBounds(x=0, y=0, width=2500, height=843)
    action = MessageAction(label="เมนู", text="เมนู")

    with (
        _patched_api_client(),
        patch("src.line.client.AsyncMessagingApi", return_value=messaging_api),
    ):
        menu_id = await client.create_rich_menu("main", "เมนูหลัก", [(bounds, action)])

    assert menu_id == "richmenu-abc123"
    messaging_api.create_rich_menu.assert_awaited_once()


async def test_upload_rich_menu_image_calls_set_rich_menu_image() -> None:
    blob_api = MagicMock()
    blob_api.set_rich_menu_image = AsyncMock()

    with (
        _patched_api_client(),
        patch("src.line.client.AsyncMessagingApiBlob", return_value=blob_api),
    ):
        await client.upload_rich_menu_image("richmenu-abc123", b"\x89PNG", "image/png")

    blob_api.set_rich_menu_image.assert_awaited_once_with(
        "richmenu-abc123", b"\x89PNG", _content_type="image/png"
    )


async def test_set_default_rich_menu_calls_the_right_endpoint() -> None:
    messaging_api = MagicMock()
    messaging_api.set_default_rich_menu = AsyncMock()

    with (
        _patched_api_client(),
        patch("src.line.client.AsyncMessagingApi", return_value=messaging_api),
    ):
        await client.set_default_rich_menu("richmenu-abc123")

    messaging_api.set_default_rich_menu.assert_awaited_once_with("richmenu-abc123")


async def test_link_rich_menu_to_user_calls_the_right_endpoint() -> None:
    messaging_api = MagicMock()
    messaging_api.link_rich_menu_id_to_user = AsyncMock()

    with (
        _patched_api_client(),
        patch("src.line.client.AsyncMessagingApi", return_value=messaging_api),
    ):
        await client.link_rich_menu_to_user("Uabc123", "richmenu-abc123")

    messaging_api.link_rich_menu_id_to_user.assert_awaited_once_with("Uabc123", "richmenu-abc123")


# ---- sending ---------------------------------------------------------------


def _messaging_api() -> MagicMock:
    api = MagicMock()
    api.reply_message = AsyncMock()
    api.push_message = AsyncMock()
    api.multicast = AsyncMock()
    return api


async def test_reply_sends_the_messages_under_the_reply_token() -> None:
    messaging_api = _messaging_api()
    message = TextMessage(text="สวัสดี")

    with (
        _patched_api_client(),
        patch("src.line.client.AsyncMessagingApi", return_value=messaging_api),
    ):
        await client.reply("reply-token", [message])

    request = messaging_api.reply_message.await_args.args[0]
    assert request.reply_token == "reply-token"
    assert request.messages == [message]


async def test_push_sends_the_messages_to_one_user() -> None:
    messaging_api = _messaging_api()
    message = TextMessage(text="เตือน")

    with (
        _patched_api_client(),
        patch("src.line.client.AsyncMessagingApi", return_value=messaging_api),
    ):
        await client.push("Uabc123", [message])

    request = messaging_api.push_message.await_args.args[0]
    assert request.to == "Uabc123"
    assert request.messages == [message]


async def test_multicast_sends_one_call_to_every_recipient() -> None:
    messaging_api = _messaging_api()
    message = TextMessage(text="เตือน")

    with (
        _patched_api_client(),
        patch("src.line.client.AsyncMessagingApi", return_value=messaging_api),
    ):
        await client.multicast(["U1", "U2"], [message])

    request = messaging_api.multicast.await_args.args[0]
    assert request.to == ["U1", "U2"]
    assert request.messages == [message]


# ---- the webhook -----------------------------------------------------------


def _sign(body: str) -> str:
    digest = hmac.new(
        settings.LINE_CHANNEL_SECRET.encode(), body.encode("utf-8"), hashlib.sha256
    ).digest()
    return base64.b64encode(digest).decode()


def test_parse_events_returns_the_events_of_a_correctly_signed_body() -> None:
    body = json.dumps({"destination": "Udestination0000000000000000000", "events": []})

    assert client.parse_events(body, _sign(body)) == []


def test_parse_events_rejects_a_wrong_signature() -> None:
    body = json.dumps({"destination": "Udestination0000000000000000000", "events": []})

    with pytest.raises(InvalidLineSignature):
        client.parse_events(body, "not-the-signature")
