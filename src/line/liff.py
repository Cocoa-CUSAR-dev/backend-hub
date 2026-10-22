"""Which LINE Login channels' ID tokens this service accepts, and the loop that
tries them. The REST call that verifies one token is `verify_id_token` in
src/line/client.py.
"""

from typing import Any

from src.config import settings
from src.exceptions import InvalidLiffToken
from src.line.client import verify_id_token

__all__ = [
    "channel_id_from_liff_id",
    "login_channel_ids",
    "verify_for_any_channel",
    "verify_id_token",
]


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
