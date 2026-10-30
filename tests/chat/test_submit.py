"""src/chat/submit.py: the chat saves a confirmed answer through the same
function the mobile app's POST /tasks calls, in-process, in its own session.

The answer's rules (validation, handler table, permission) are
tests/db/test_answers.py's job; these tests are about the hand-over.
"""

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.actor import Actor
from src.chat.submit import submit_task
from src.exceptions import Forbidden, HandlerNotSupported, Invalid
from src.tasks.schemas import TaskSubmission

USER_ID = uuid.uuid4()
TASK_ID = uuid.uuid4()
ANSWER = {"farm_id": str(uuid.uuid4()), "note": "ใส่ปุ๋ย"}


def _submission() -> TaskSubmission:
    return TaskSubmission(user_id=str(USER_ID), task_id=str(TASK_ID), answer=ANSWER)


class _Sessions:
    """Stands in for async_session_maker: hands out one fake session and
    remembers whether it was opened and closed."""

    def __init__(self) -> None:
        self.session = MagicMock(name="own_session")
        self.opened = 0
        self.closed = 0

    def __call__(self):  # type: ignore[no-untyped-def]
        @asynccontextmanager
        async def open_session():  # type: ignore[no-untyped-def]
            self.opened += 1
            try:
                yield self.session
            finally:
                self.closed += 1

        return open_session()


@pytest.fixture
def sessions():  # type: ignore[no-untyped-def]
    fake = _Sessions()
    actor = Actor(user_id=USER_ID, roles=frozenset({"farmer"}), perms=frozenset(), channel="line")
    with (
        patch("src.chat.submit.async_session_maker", fake),
        patch("src.chat.submit.load_actor", AsyncMock(return_value=actor)) as load_actor,
        patch("src.chat.submit.answers.submit", AsyncMock()) as submit,
    ):
        fake.actor = actor  # type: ignore[attr-defined]
        fake.load_actor = load_actor  # type: ignore[attr-defined]
        fake.submit = submit  # type: ignore[attr-defined]
        yield fake


async def test_saves_the_answer_as_the_person_who_gave_it(sessions) -> None:  # type: ignore[no-untyped-def]
    await submit_task(_submission())

    # The person, loaded with their roles and permissions, on the LINE channel:
    sessions.load_actor.assert_awaited_once_with(sessions.session, USER_ID, "line")
    # ... then the app's own function, with real ids (not the strings the chat holds):
    sessions.submit.assert_awaited_once_with(sessions.session, sessions.actor, TASK_ID, ANSWER)


async def test_runs_in_its_own_session_and_closes_it(sessions) -> None:  # type: ignore[no-untyped-def]
    """confirm_conversation holds a row lock on its own session; the service
    commits when it finishes, so it must not be handed that one."""
    await submit_task(_submission())

    assert (sessions.opened, sessions.closed) == (1, 1)


@pytest.mark.parametrize(
    "refusal",
    [
        HandlerNotSupported("Form type 'x' can't be saved automatically yet"),
        Forbidden("Missing permission create:response:own"),
        Invalid("ข้อมูลไม่ผ่านการตรวจสอบ", fields={"weight": "must be a number"}),
    ],
    ids=lambda e: type(e).__name__,
)
async def test_a_refusal_reaches_the_conversation_unchanged(sessions, refusal) -> None:  # type: ignore[no-untyped-def]
    """The conversation tells HandlerNotSupported (permanent) from anything
    else (retry) by type, so the service's error must not be wrapped."""
    sessions.submit.side_effect = refusal

    with pytest.raises(type(refusal)) as raised:
        await submit_task(_submission())

    assert raised.value is refusal
    assert sessions.closed == 1  # the session is closed on the failure path too
