"""src/r2/client.py without the network: botocore's Stubber answers in place
of R2, and presigning is local anyway."""

from collections.abc import Iterator
from urllib.parse import parse_qs, urlparse

import pytest
from botocore.stub import Stubber

from src.config import settings
from src.exceptions import Unavailable
from src.r2 import client


@pytest.fixture
def r2_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(settings, "R2_ACCOUNT_ID", "acct")
    monkeypatch.setattr(settings, "R2_ACCESS_KEY_ID", "key-id")
    monkeypatch.setattr(settings, "R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setattr(settings, "R2_BUCKET", "farm-images")
    client._client.cache_clear()
    yield
    client._client.cache_clear()


async def test_put_uploads_to_the_bucket(r2_settings: None) -> None:
    with Stubber(client._client()) as stub:
        stub.add_response(
            "put_object",
            {},
            {
                "Bucket": "farm-images",
                "Key": "farms/f1/a.jpg",
                "Body": b"\xff\xd8\xff",
                "ContentType": "image/jpeg",
            },
        )
        await client.put("farms/f1/a.jpg", b"\xff\xd8\xff", "image/jpeg")
        stub.assert_no_pending_responses()


async def test_put_failure_is_unavailable(r2_settings: None) -> None:
    with Stubber(client._client()) as stub:
        stub.add_client_error("put_object", service_error_code="InternalError")
        with pytest.raises(Unavailable, match="upload failed"):
            await client.put("farms/f1/a.jpg", b"x", "image/jpeg")


async def test_presigned_get_signs_a_path_style_url(r2_settings: None) -> None:
    url = await client.presigned_get("farms/f1/a.jpg", 3600)

    parsed = urlparse(url)
    assert parsed.netloc == "acct.r2.cloudflarestorage.com"
    assert parsed.path == "/farm-images/farms/f1/a.jpg"
    assert parse_qs(parsed.query)["X-Amz-Expires"] == ["3600"]


async def test_everything_is_unavailable_when_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "R2_BUCKET", "")

    assert client.configured() is False
    with pytest.raises(Unavailable, match="not configured"):
        await client.put("k", b"x", "image/png")
    with pytest.raises(Unavailable, match="not configured"):
        await client.presigned_get("k", 60)
