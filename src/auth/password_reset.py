"""Forgot password: a short code, sent through LINE or email (docs-and-plan#321).

Asking for a code always gets the same answer, whether or not the account
exists, and the work happens after that answer has gone out, so the time it
takes says nothing either. The person types the code with a new password; the
code works once, for a few minutes, and is guessable only a few times.

Whoever has neither a LINE link nor an email gets nothing: an admin resets
their password by hand (src/admin/service.py).
"""

import logging
from dataclasses import dataclass
from datetime import timedelta

from src import db
from src.auth import passwords, tokens
from src.clock import utc_now_naive
from src.config import Environment, settings
from src.database import Session, async_session_maker, transaction
from src.exceptions import InvalidResetCode
from src.line import service as line_service
from src.mailer import client as mailer

logger = logging.getLogger(__name__)

# A code can be tried this many times wrong; after that it is dead, even if the
# right number comes next.
MAX_WRONG_TRIES = 5
# Codes one account can ask for in an hour; more are quietly ignored.
MAX_CODES_PER_HOUR = 3
_HOUR = timedelta(hours=1)

NEUTRAL_MESSAGE = (
    "ถ้ามีบัญชีนี้ เราจะส่งรหัสให้ทาง LINE หรืออีเมลที่ผูกไว้ หากไม่ได้รับรหัสภายในไม่กี่นาที ให้ติดต่อผู้ดูแลเพื่อขอรีเซ็ตรหัสผ่าน"
)
_EMAIL_SUBJECT = "รหัสรีเซ็ตรหัสผ่าน / Password reset code"


@dataclass(frozen=True, slots=True)
class Delivery:
    """A fresh code and where it can go."""

    code: str
    line_user_id: str | None
    email: str | None


def _line_text(code: str, minutes: int) -> str:
    return (
        f"รหัสรีเซ็ตรหัสผ่านของคุณคือ {code}\n"
        f"ใช้ได้ภายใน {minutes} นาที และใช้ได้ครั้งเดียว\n"
        "ถ้าคุณไม่ได้เป็นคนขอ ให้ละเว้นข้อความนี้"
    )


def _email_text(code: str, minutes: int) -> str:
    return (
        f"รหัสรีเซ็ตรหัสผ่านของคุณคือ {code}\n"
        f"ใช้ได้ภายใน {minutes} นาที และใช้ได้ครั้งเดียว\n"
        "ถ้าคุณไม่ได้เป็นคนขอ ให้ละเว้นอีเมลนี้\n\n"
        f"Your password reset code is {code}\n"
        f"It works once, for {minutes} minutes. If you did not ask for it, ignore this email.\n"
    )


async def request_code(session: Session, login: str) -> Delivery | None:
    """Make a new code for this account and keep its hash. None when there is
    no such account, or it already asked for too many codes this hour.
    """
    user = await db.auth.user_by_login(session, login.strip())
    if user is None:
        return None
    now = utc_now_naive()
    async with transaction(session):
        if await db.auth.count_reset_codes_since(session, user.user_id, now - _HOUR) >= (
            MAX_CODES_PER_HOUR
        ):
            return None
        code = tokens.new_reset_code()
        await db.auth.insert_reset_code(
            session,
            user.user_id,
            tokens.hash_reset_code(user.user_id, code),
            now + timedelta(seconds=settings.RESET_CODE_TTL_SECONDS),
            now,
        )
    line_ids = await db.auth.line_user_ids_for(session, [user.user_id])
    return Delivery(code=code, line_user_id=line_ids.get(user.user_id), email=user.email)


async def deliver(delivery: Delivery) -> None:
    """Send the code through every channel the account has. A channel that
    fails is logged and skipped: the answer has already gone out, and the
    person can ask again.
    """
    minutes = settings.RESET_CODE_TTL_SECONDS // 60
    sent = False
    if delivery.line_user_id:
        try:
            await line_service.push_text(delivery.line_user_id, _line_text(delivery.code, minutes))
            sent = True
        except Exception:
            logger.exception("password reset: could not push the code through LINE")
    if delivery.email and settings.email_configured:
        try:
            await mailer.send_email(
                delivery.email, _EMAIL_SUBJECT, _email_text(delivery.code, minutes)
            )
            sent = True
        except Exception:
            logger.exception("password reset: could not email the code")
    if not sent and settings.ENVIRONMENT == Environment.LOCAL:
        # Only on a developer's machine, so the flow can be tried with no mail
        # server and no LINE account.
        logger.info("password reset code (local only): %s", delivery.code)


async def send_code(login: str) -> None:
    """The whole "forgot password" request, run after the answer was sent, with
    its own session. Never raises: nobody is left to hear about it.
    """
    try:
        async with async_session_maker() as session:
            delivery = await request_code(session, login)
        if delivery is not None:
            await deliver(delivery)
    except Exception:
        logger.exception("password reset: could not send a code")


async def confirm(session: Session, login: str, code: str, new_password: str) -> None:
    """Set the new password if the code is right, and sign the person out
    everywhere. Wrong, expired, used and unknown all answer the same way.
    """
    passwords.check_strength(new_password)
    # Hashed first, for every login, so the time says nothing about the account.
    new_hash = await passwords.hash_password(new_password)
    user = await db.auth.user_by_login(session, login.strip())
    accepted = False
    if user is not None:
        async with transaction(session):
            now = utc_now_naive()
            row = await db.auth.live_reset_code(session, user.user_id, now)
            if row is not None and row.attempts < MAX_WRONG_TRIES:
                if tokens.reset_code_matches(row.code_hash, user.user_id, code):
                    await db.auth.set_password(session, user.user_id, new_hash)
                    await db.auth.use_up_reset_codes(session, user.user_id, now)
                    await db.auth.revoke_all_refresh_tokens(session, user.user_id)
                    accepted = True
                else:
                    await db.auth.count_reset_attempt(session, row.password_reset_code_id)
    # Raised after the transaction closed, so a wrong try is still counted.
    if not accepted:
        raise InvalidResetCode()
