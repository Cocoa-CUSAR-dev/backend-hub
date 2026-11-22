"""/api/v1/auth/forgot-password end to end against Postgres (RUN_DB_TESTS=1).

LINE and the mail server are replaced by an outbox the tests read the code from.
"""

import logging
import re
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth import password_reset, tokens
from src.clock import utc_now_naive
from src.config import Environment, settings
from tests.integration.auth_helpers import seed_user

AUTH = "/api/v1/auth"
BAD_CODE = {"detail": "รหัสไม่ถูกต้องหรือหมดอายุ กรุณาขอรหัสใหม่"}


@dataclass
class Outbox:
    line: list[tuple[str, str]] = field(default_factory=list)
    emails: list[tuple[str, str, str]] = field(default_factory=list)

    def last_code(self) -> str:
        """The code in the newest message, from whichever channel got it."""
        body = self.line[-1][1] if self.line else self.emails[-1][2]
        match = re.search(r"\b(\d{6})\b", body)
        assert match is not None, body
        return match.group(1)


@pytest.fixture
def outbox(monkeypatch: pytest.MonkeyPatch) -> Outbox:
    box = Outbox()

    async def push_text(to: str, message: str, quick_reply: Any = None) -> None:
        box.line.append((to, message))

    async def send_email(to: str, subject: str, body: str) -> None:
        box.emails.append((to, subject, body))

    monkeypatch.setattr("src.line.service.push_text", push_text)
    monkeypatch.setattr("src.mailer.client.send_email", send_email)
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "MAIL_FROM", "no-reply@example.com")
    return box


@pytest.fixture
def fixed_codes(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Make the next codes known ahead of time."""

    def use(*codes: str) -> None:
        feed: Iterator[str] = iter(codes)
        monkeypatch.setattr(tokens, "new_reset_code", lambda: next(feed))

    return use


async def _seed(
    session: AsyncSession,
    username: str,
    *,
    password: str = "old-pass-1",
    line: str | None = None,
    email: str | None = None,
) -> uuid.UUID:
    user_id = await seed_user(session, username, password)
    if line:
        await session.execute(
            text(
                "INSERT INTO auth.line_identity (user_id, line_user_id, display_name)"
                " VALUES (:u, :l, 'Farmer')"
            ),
            {"u": user_id, "l": line},
        )
    if email:
        await session.execute(
            text("UPDATE auth.user_account SET email = :e WHERE user_id = :u"),
            {"e": email, "u": user_id},
        )
    await session.commit()
    return user_id


async def _ask(client: AsyncClient, login: str) -> Response:
    return await client.post(f"{AUTH}/forgot-password", json={"login": login})


async def _confirm(
    client: AsyncClient, login: str, code: str, password: str = "new-pass-1"
) -> Response:
    return await client.post(
        f"{AUTH}/forgot-password/confirm",
        json={"login": login, "code": code, "new_password": password},
    )


async def _login(client: AsyncClient, login: str, password: str) -> Response:
    return await client.post(f"{AUTH}/login", json={"username": login, "password": password})


async def test_a_known_and_an_unknown_account_get_the_same_answer(
    client: AsyncClient, db_session: AsyncSession, outbox: Outbox
) -> None:
    await _seed(db_session, "0811111111", line="U-farmer-1")

    known = await _ask(client, "0811111111")
    unknown = await _ask(client, "0899999999")

    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json() == {"message": password_reset.NEUTRAL_MESSAGE}
    assert [to for to, _ in outbox.line] == ["U-farmer-1"]


async def test_the_code_comes_through_line_and_resets_the_password_everywhere(
    client: AsyncClient, db_session: AsyncSession, outbox: Outbox
) -> None:
    await _seed(db_session, "0811111111", line="U-farmer-1")
    signed_in = (await _login(client, "0811111111", "old-pass-1")).json()

    await _ask(client, "0811111111")
    reset = await _confirm(client, "0811111111", outbox.last_code())

    assert reset.status_code == 204
    assert (await _login(client, "0811111111", "old-pass-1")).status_code == 401
    assert (await _login(client, "0811111111", "new-pass-1")).status_code == 200
    old_session = await client.post(
        f"{AUTH}/refresh", json={"refresh_token": signed_in["refresh_token"]}
    )
    assert old_session.status_code == 401


async def test_the_code_comes_by_email_when_the_account_has_one(
    client: AsyncClient, db_session: AsyncSession, outbox: Outbox
) -> None:
    await _seed(db_session, "researcher01", email="researcher@example.com")

    await _ask(client, "researcher@example.com")

    assert outbox.line == []
    ((to, subject, body),) = outbox.emails
    assert to == "researcher@example.com"
    assert "Password reset" in subject
    # Asked for by email, reset by username: either name reaches the account.
    assert (await _confirm(client, "researcher01", outbox.last_code())).status_code == 204
    assert body.count(outbox.last_code()) >= 1


async def test_an_account_with_both_gets_the_same_code_on_both(
    client: AsyncClient, db_session: AsyncSession, outbox: Outbox
) -> None:
    await _seed(db_session, "0811111111", line="U-farmer-1", email="a@example.com")

    await _ask(client, "0811111111")

    assert len(outbox.line) == len(outbox.emails) == 1
    assert (
        re.search(r"\d{6}", outbox.line[0][1]).group()
        == re.search(  # type: ignore[union-attr]
            r"\d{6}", outbox.emails[0][2]
        ).group()
    )  # type: ignore[union-attr]


async def test_an_account_with_neither_is_answered_and_sent_nothing(
    client: AsyncClient, db_session: AsyncSession, outbox: Outbox
) -> None:
    await _seed(db_session, "0811111111")

    response = await _ask(client, "0811111111")

    assert response.status_code == 202
    assert outbox.line == [] and outbox.emails == []


async def test_a_code_works_once(
    client: AsyncClient, db_session: AsyncSession, outbox: Outbox
) -> None:
    await _seed(db_session, "0811111111", line="U-farmer-1")
    await _ask(client, "0811111111")
    code = outbox.last_code()

    assert (await _confirm(client, "0811111111", code)).status_code == 204
    again = await _confirm(client, "0811111111", code, "other-pass-2")

    assert again.status_code == 400
    assert again.json() == BAD_CODE
    assert (await _login(client, "0811111111", "other-pass-2")).status_code == 401


async def test_an_expired_code_is_refused(
    client: AsyncClient,
    db_session: AsyncSession,
    outbox: Outbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, "0811111111", line="U-farmer-1")
    await _ask(client, "0811111111")
    late: Callable[[], datetime] = lambda: utc_now_naive() + timedelta(  # noqa: E731
        seconds=settings.RESET_CODE_TTL_SECONDS + 60
    )
    monkeypatch.setattr(password_reset, "utc_now_naive", late)

    response = await _confirm(client, "0811111111", outbox.last_code())

    assert response.status_code == 400
    assert response.json() == BAD_CODE
    assert (await _login(client, "0811111111", "old-pass-1")).status_code == 200


async def test_too_many_wrong_tries_lock_the_code_even_for_the_right_number(
    client: AsyncClient, db_session: AsyncSession, outbox: Outbox, fixed_codes: Callable[..., None]
) -> None:
    fixed_codes("424242")
    user_id = await _seed(db_session, "0811111111", line="U-farmer-1")
    await _ask(client, "0811111111")

    for _ in range(password_reset.MAX_WRONG_TRIES):
        assert (await _confirm(client, "0811111111", "000000")).status_code == 400
    locked = await _confirm(client, "0811111111", "424242")

    assert locked.status_code == 400
    attempts = (
        await db_session.execute(
            text("SELECT attempts FROM auth.password_reset_code WHERE user_id = :u"),
            {"u": user_id},
        )
    ).scalar_one()
    assert attempts == password_reset.MAX_WRONG_TRIES
    assert (await _login(client, "0811111111", "old-pass-1")).status_code == 200


async def test_a_new_request_replaces_the_old_code(
    client: AsyncClient, db_session: AsyncSession, outbox: Outbox, fixed_codes: Callable[..., None]
) -> None:
    fixed_codes("111111", "222222")
    await _seed(db_session, "0811111111", line="U-farmer-1")
    await _ask(client, "0811111111")
    await _ask(client, "0811111111")

    assert (await _confirm(client, "0811111111", "111111")).status_code == 400
    assert (await _confirm(client, "0811111111", "222222")).status_code == 204


async def test_an_account_can_ask_for_only_so_many_codes_an_hour(
    client: AsyncClient, db_session: AsyncSession, outbox: Outbox
) -> None:
    await _seed(db_session, "0811111111", line="U-farmer-1")

    for _ in range(password_reset.MAX_CODES_PER_HOUR + 2):
        response = await _ask(client, "0811111111")
        assert response.status_code == 202

    assert len(outbox.line) == password_reset.MAX_CODES_PER_HOUR


async def test_a_weak_new_password_is_refused_without_using_up_the_code(
    client: AsyncClient, db_session: AsyncSession, outbox: Outbox
) -> None:
    await _seed(db_session, "0811111111", line="U-farmer-1")
    await _ask(client, "0811111111")
    code = outbox.last_code()

    weak = await _confirm(client, "0811111111", code, "12345")

    assert weak.status_code == 422
    assert "password" in weak.json()["fields"]
    assert (await _confirm(client, "0811111111", code)).status_code == 204


async def test_an_unknown_account_is_refused_like_a_wrong_code(
    client: AsyncClient, db_session: AsyncSession, outbox: Outbox
) -> None:
    await _seed(db_session, "0811111111", line="U-farmer-1")
    await _ask(client, "0811111111")

    wrong = await _confirm(client, "0811111111", "000000")
    unknown = await _confirm(client, "0899999999", "000000")

    assert wrong.status_code == unknown.status_code == 400
    assert wrong.json() == unknown.json() == BAD_CODE


async def test_the_code_is_logged_only_on_a_developers_machine(
    client: AsyncClient,
    db_session: AsyncSession,
    outbox: Outbox,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await _seed(db_session, "0811111111")

    monkeypatch.setattr(settings, "ENVIRONMENT", Environment.LOCAL)
    with caplog.at_level(logging.INFO, logger="src.auth.password_reset"):
        await _ask(client, "0811111111")
    match = re.search(r"password reset code \(local only\): (\d{6})", caplog.text)
    assert match is not None
    assert (await _confirm(client, "0811111111", match.group(1))).status_code == 204

    caplog.clear()
    monkeypatch.setattr(settings, "ENVIRONMENT", Environment.PRODUCTION)
    with caplog.at_level(logging.INFO, logger="src.auth.password_reset"):
        await _ask(client, "0811111111")
    assert "local only" not in caplog.text
