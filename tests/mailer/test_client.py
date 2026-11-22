import smtplib
from email.message import EmailMessage
from typing import Any

import pytest

from src.config import settings
from src.exceptions import Unavailable
from src.mailer import client


class FakeSMTP:
    """Stands in for smtplib.SMTP / SMTP_SSL and records what was done to it."""

    instances: list["FakeSMTP"] = []
    offers_starttls = True
    fail_on_send: Exception | None = None

    def __init__(self, host: str, port: int, **kwargs: Any) -> None:
        self.host, self.port, self.kwargs = host, port, kwargs
        self.calls: list[str] = []
        self.sent: EmailMessage | None = None
        FakeSMTP.instances.append(self)

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def ehlo(self) -> None:
        self.calls.append("ehlo")

    def has_extn(self, name: str) -> bool:
        return FakeSMTP.offers_starttls

    def starttls(self, context: object = None) -> None:
        self.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        self.calls.append(f"login:{user}")

    def send_message(self, message: EmailMessage) -> None:
        if FakeSMTP.fail_on_send is not None:
            raise FakeSMTP.fail_on_send
        self.sent = message


@pytest.fixture(autouse=True)
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSMTP.instances = []
    FakeSMTP.offers_starttls = True
    FakeSMTP.fail_on_send = None
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "SMTP_PORT", 587)
    monkeypatch.setattr(settings, "SMTP_USER", "mailer")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "pw")
    monkeypatch.setattr(settings, "MAIL_FROM", "no-reply@example.com")
    monkeypatch.setattr(client.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(client.smtplib, "SMTP_SSL", FakeSMTP)


async def test_a_message_goes_out_over_starttls_with_the_login() -> None:
    await client.send_email("farmer@example.com", "Hello", "Body text")

    (smtp,) = FakeSMTP.instances
    assert (smtp.host, smtp.port) == ("smtp.example.com", 587)
    assert smtp.calls == ["ehlo", "starttls", "ehlo", "login:mailer"]
    assert smtp.sent is not None
    assert smtp.sent["From"] == "no-reply@example.com"
    assert smtp.sent["To"] == "farmer@example.com"
    assert smtp.sent["Subject"] == "Hello"
    assert smtp.sent.get_content().strip() == "Body text"


async def test_port_465_is_tls_from_the_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "SMTP_PORT", 465)

    await client.send_email("a@example.com", "s", "b")

    (smtp,) = FakeSMTP.instances
    assert smtp.port == 465
    assert "starttls" not in smtp.calls


async def test_no_login_is_sent_when_none_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "SMTP_USER", "")

    await client.send_email("a@example.com", "s", "b")

    assert not any(call.startswith("login") for call in FakeSMTP.instances[0].calls)


async def test_only_a_local_relay_may_skip_tls(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSMTP.offers_starttls = False
    monkeypatch.setattr(settings, "SMTP_HOST", "localhost")
    await client.send_email("a@example.com", "s", "b")
    assert "starttls" not in FakeSMTP.instances[-1].calls

    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    await client.send_email("a@example.com", "s", "b")
    assert "starttls" in FakeSMTP.instances[-1].calls


@pytest.mark.parametrize("field", ["SMTP_HOST", "MAIL_FROM"])
async def test_nothing_is_sent_when_email_is_not_set_up(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    monkeypatch.setattr(settings, field, "")

    with pytest.raises(Unavailable):
        await client.send_email("a@example.com", "s", "b")

    assert FakeSMTP.instances == []


@pytest.mark.parametrize(
    "failure", [smtplib.SMTPRecipientsRefused({}), ConnectionRefusedError("down"), TimeoutError()]
)
async def test_a_server_that_fails_is_reported_as_unavailable(failure: Exception) -> None:
    FakeSMTP.fail_on_send = failure

    with pytest.raises(Unavailable):
        await client.send_email("a@example.com", "s", "b")
