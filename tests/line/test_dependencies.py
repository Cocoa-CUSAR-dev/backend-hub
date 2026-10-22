import base64
import hashlib
import hmac
import json

import pytest
from starlette.requests import Request

from src.config import settings
from src.exceptions import InvalidLineSignature
from src.line.dependencies import parse_line_events


def _sign(body: bytes) -> str:
    digest = hmac.new(settings.LINE_CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def _request(body: bytes) -> Request:
    async def receive() -> dict:
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope={"type": "http", "headers": []}, receive=receive)


async def test_parse_line_events_accepts_a_correctly_signed_body() -> None:
    body = json.dumps({"destination": "Udestination0000000000000000000", "events": []}).encode()

    events = await parse_line_events(_request(body), _sign(body))

    assert events == []


async def test_parse_line_events_rejects_a_bad_signature() -> None:
    body = json.dumps({"destination": "Udestination0000000000000000000", "events": []}).encode()

    with pytest.raises(InvalidLineSignature):
        await parse_line_events(_request(body), "not-a-real-signature")
