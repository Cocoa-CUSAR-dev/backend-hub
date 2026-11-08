"""The one place this service talks to LINE (CONVENTIONS.md §3).

Only this file imports `linebot`, and only this file calls LINE's own REST
endpoints with httpx. It wraps four things and makes no business decisions
and reads no database:

- sending: `reply` (free, inside the reply-token window), `push` and
  `multicast` (costed, anytime)
- the webhook: `parse_events` checks the signature and parses the body
- rich menus: create, upload the image, set as default, link to a user
- LIFF: `verify_id_token`, the REST call that checks a logged-in LIFF
  session's ID token (the SDK's `linebot.v3.liff` only manages LIFF apps)

What to say, and to whom, is decided elsewhere (src/line/service.py builds
the messages; src/chat decides when to send them). The SDK's message and
event classes that callers need are re-exported in `__all__`, so a caller
writes `from src.line.client import TextMessage` and never imports linebot.
"""

from collections.abc import Sequence
from typing import Any, cast

import httpx
from linebot.v3 import WebhookParser
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    AsyncApiClient,
    AsyncMessagingApi,
    AsyncMessagingApiBlob,
    Configuration,
    FlexContainer,
    FlexMessage,
    LocationAction,
    Message,
    MessageAction,
    MulticastRequest,
    PostbackAction,
    PushMessageRequest,
    QuickReply,
    QuickReplyItem,
    ReplyMessageRequest,
    RichMenuArea,
    RichMenuBounds,
    RichMenuRequest,
    RichMenuSize,
    TextMessage,
)
from linebot.v3.webhooks import (
    Event,
    FollowEvent,
    ImageMessageContent,
    LocationMessageContent,
    MessageEvent,
    PostbackEvent,
    TextMessageContent,
)

from src.config import settings
from src.exceptions import InvalidLiffToken, InvalidLineSignature, UpstreamServiceError

__all__ = [
    "Event",
    "FlexContainer",
    "FlexMessage",
    "FollowEvent",
    "ImageMessageContent",
    "LocationAction",
    "LocationMessageContent",
    "Message",
    "MessageAction",
    "MessageEvent",
    "PostbackAction",
    "PostbackEvent",
    "QuickReply",
    "QuickReplyItem",
    "TextMessage",
    "TextMessageContent",
    "create_rich_menu",
    "link_rich_menu_to_user",
    "multicast",
    "parse_events",
    "push",
    "reply",
    "set_default_rich_menu",
    "upload_rich_menu_image",
    "verify_id_token",
]

_configuration = Configuration(access_token=settings.LINE_CHANNEL_ACCESS_TOKEN)
_parser = WebhookParser(settings.LINE_CHANNEL_SECRET)

_VERIFY_URL = "https://api.line.me/oauth2/v2.1/verify"


# ---- the webhook -----------------------------------------------------------


def parse_events(body: str, signature: str) -> list[Event]:
    """Checks `signature` against the raw `body` and returns the parsed events.
    Raises InvalidLineSignature if it does not match.

    Signature checking is the SDK's own `WebhookParser`, never hand-rolled.
    """
    try:
        # line-bot-sdk's own stubs don't type WebhookParser.parse()'s return
        # precisely enough for mypy to see past Any here.
        return cast(list[Event], _parser.parse(body, signature))
    except InvalidSignatureError as exc:
        raise InvalidLineSignature from exc


# ---- sending ---------------------------------------------------------------


async def reply(reply_token: str, messages: Sequence[Message]) -> None:
    """Reply within the ~60s reply-token window -- free, not a push."""
    async with AsyncApiClient(_configuration) as api:
        await AsyncMessagingApi(api).reply_message(
            ReplyMessageRequest(replyToken=reply_token, messages=list(messages))
        )


async def push(to: str, messages: Sequence[Message]) -> None:
    """Proactive send outside the reply-token window -- a COSTED send. Never
    use it when a reply token is still valid.
    """
    async with AsyncApiClient(_configuration) as api:
        await AsyncMessagingApi(api).push_message(
            PushMessageRequest(to=to, messages=list(messages))
        )


async def multicast(to: list[str], messages: Sequence[Message]) -> None:
    """One call, many recipients (LINE caps it at 500 per call; chunking is the
    caller's job). Billed differently from looping `push`.
    """
    async with AsyncApiClient(_configuration) as api:
        await AsyncMessagingApi(api).multicast(MulticastRequest(to=to, messages=list(messages)))


# ---- rich menus ------------------------------------------------------------
# E.g. the to-do fallback channel for farmers who don't act on a reminder push
# (ADR 0006's flagged-but-not-yet-built fallback). Primitives only: no route
# uses them yet, and no backlog task fixes the menu's contents or layout.


async def create_rich_menu(
    name: str, chat_bar_text: str, areas: list[tuple[RichMenuBounds, MessageAction]]
) -> str:
    """Returns the new rich_menu_id. Creating the menu and uploading its
    image are two separate LINE API calls -- see upload_rich_menu_image.
    """
    request = RichMenuRequest(
        size=RichMenuSize(width=2500, height=843),
        selected=False,
        name=name,
        chatBarText=chat_bar_text,
        areas=[RichMenuArea(bounds=bounds, action=action) for bounds, action in areas],
    )
    async with AsyncApiClient(_configuration) as api:
        response = await AsyncMessagingApi(api).create_rich_menu(request)
    return str(response.rich_menu_id)


async def upload_rich_menu_image(rich_menu_id: str, image_bytes: bytes, content_type: str) -> None:
    """`content_type` must be image/jpeg or image/png -- LINE's own
    requirement, not this code's.
    """
    async with AsyncApiClient(_configuration) as api:
        await AsyncMessagingApiBlob(api).set_rich_menu_image(
            rich_menu_id, image_bytes, _content_type=content_type
        )


async def set_default_rich_menu(rich_menu_id: str) -> None:
    """Applies to every farmer who doesn't already have a menu explicitly
    linked via link_rich_menu_to_user.
    """
    async with AsyncApiClient(_configuration) as api:
        await AsyncMessagingApi(api).set_default_rich_menu(rich_menu_id)


async def link_rich_menu_to_user(user_id: str, rich_menu_id: str) -> None:
    async with AsyncApiClient(_configuration) as api:
        await AsyncMessagingApi(api).link_rich_menu_id_to_user(user_id, rich_menu_id)


# ---- LIFF ------------------------------------------------------------------


async def verify_id_token(id_token: str, channel_id: str) -> dict[str, Any]:
    """Returns the decoded claims (includes `sub` -- the LINE user ID) if
    valid. Raises InvalidLiffToken on anything else.

    Not part of `line-bot-sdk` (`linebot.v3.liff` manages LIFF apps, it does
    not verify a session's ID token): a plain REST call to LINE's own OAuth2
    verify endpoint.
    """
    async with httpx.AsyncClient() as http:
        response = await http.post(
            _VERIFY_URL,
            data={"id_token": id_token, "client_id": channel_id},
        )

    if response.status_code == 400:
        raise InvalidLiffToken
    if response.status_code >= 400:
        raise UpstreamServiceError(f"LINE verify endpoint returned {response.status_code}")

    # httpx's .json() is typed Any (JSON content can be anything) -- LINE's
    # verify endpoint contract guarantees an object on 200, per its own docs.
    return cast(dict[str, Any], response.json())
