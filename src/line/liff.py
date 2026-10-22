"""LIFF ID-token verification.

Not part of `line-bot-sdk` -- `linebot.v3.liff` is for managing LIFF apps
(add/update/list), not for verifying a logged-in LIFF session's ID token.
That's a plain REST call to LINE's own OAuth2 verify endpoint, so this uses
httpx directly, same as src/forms and src/tasks.
"""

from typing import Any, cast

import httpx

from src.config import settings
from src.exceptions import InvalidLiffToken, UpstreamServiceError

_VERIFY_URL = "https://api.line.me/oauth2/v2.1/verify"


def channel_id_from_liff_id(liff_id: str) -> str:
    """LINE documents a LIFF ID as "{channelId}-{randomId}" -- the same
    channel ID the verify endpoint's client_id expects.
    """
    return liff_id.split("-", 1)[0]


def login_channel_ids() -> list[str]:
    """Every LINE Login channel whose ID tokens this service accepts."""
    channels = list(settings.LINE_LOGIN_CHANNEL_IDS)
    if settings.LIFF_ID:
        channels.insert(0, channel_id_from_liff_id(settings.LIFF_ID))
    return list(dict.fromkeys(c for c in channels if c))


async def verify_for_any_channel(id_token: str) -> dict[str, Any]:
    """Verify against each configured channel; the first that accepts wins.
    Fails closed when nothing is configured.
    """
    channels = login_channel_ids()
    if not channels:
        raise InvalidLiffToken("No LINE Login channel is configured")
    for channel_id in channels:
        try:
            return await verify_id_token(id_token, channel_id)
        except InvalidLiffToken:
            continue
    raise InvalidLiffToken


async def verify_id_token(id_token: str, channel_id: str) -> dict[str, Any]:
    """Returns the decoded claims (includes `sub` -- the LINE user ID) if
    valid. Raises InvalidLiffToken on anything else.
    """
    async with httpx.AsyncClient() as client:
        response = await client.post(
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
