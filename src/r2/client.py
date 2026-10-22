"""Cloudflare R2 (S3-compatible object storage) -- the only file that imports boto3.

Farm photos live here. Objects are private: callers store the key and hand
out short-lived presigned GET links, never a public URL.

boto3 is synchronous, so each call runs in a worker thread to keep the event
loop free. One client is created on first use and shared.
"""

import asyncio
from functools import cache
from typing import Any

import boto3
from botocore.config import Config

from src.config import settings
from src.exceptions import Unavailable


def configured() -> bool:
    """Whether R2 is set up at all (all four R2_* settings present)."""
    return settings.r2_configured


@cache
def _client() -> Any:
    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        region_name="auto",
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=5,
            read_timeout=30,
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


def _require_configured() -> None:
    if not configured():
        raise Unavailable("Photo storage is not configured")


async def put(key: str, body: bytes, content_type: str) -> None:
    """Upload one object. Raises Unavailable if R2 is off or the upload fails."""
    _require_configured()
    try:
        await asyncio.to_thread(
            _client().put_object,
            Bucket=settings.R2_BUCKET,
            Key=key,
            Body=body,
            ContentType=content_type,
        )
    except Exception as exc:  # noqa: BLE001 -- any storage failure is "unavailable"
        raise Unavailable("Photo upload failed, please try again") from exc


async def presigned_get(key: str, expires_seconds: int) -> str:
    """A link that lets anyone holding it GET the object until it expires.

    Signing happens locally (no network call), but it is still done off the
    event loop, like every boto3 call here.
    """
    _require_configured()
    url: str = await asyncio.to_thread(
        _client().generate_presigned_url,
        "get_object",
        Params={"Bucket": settings.R2_BUCKET, "Key": key},
        ExpiresIn=expires_seconds,
    )
    return url
