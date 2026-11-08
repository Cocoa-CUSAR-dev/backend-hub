"""Webhook signature verification -- see ADR 0003 (line-bot-sdk-python chosen
specifically so this doesn't need hand-rolled HMAC verification). The check
itself lives in src/line/client.py; this is only the FastAPI dependency.
"""

from typing import Annotated

from fastapi import Header, Request

from src.line import client
from src.line.client import Event


async def parse_line_events(
    request: Request,
    x_line_signature: Annotated[str, Header()],
) -> list[Event]:
    body = (await request.body()).decode("utf-8")
    return client.parse_events(body, x_line_signature)
