from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from src import db
from src.db.models import Conversation, ConversationAnswer, ConversationStatus
from src.exceptions import AnswerInFlight
from tests.db.conftest import insert


async def _user(session: AsyncSession) -> UUID:
    user_id = await insert(session, "auth.user_account", username=f"user-{uuid4()}")
    assert user_id is not None
    return user_id


async def _task(session: AsyncSession) -> tuple[UUID, UUID]:
    """A task and its form (a conversation points at both)."""
    task_id = await insert(session, "form.task", title="harvest")
    assert task_id is not None
    form_id = await insert(session, "form.task_form", task_id=task_id, title="harvest")
    assert form_id is not None
    return task_id, form_id


async def _conversation(
    session: AsyncSession,
    user_id: UUID,
    task: tuple[UUID, UUID] | None = None,
    status: ConversationStatus = ConversationStatus.ACTIVE,
) -> Conversation:
    task_id, form_id = task or await _task(session)
    conversation = Conversation(
        user_id=user_id, task_id=task_id, task_form_id=form_id, status=status.value
    )
    session.add(conversation)
    await session.flush()
    return conversation


async def _question(session: AsyncSession) -> UUID:
    question_id = await insert(session, "form.question", section_id=uuid4(), label="how much")
    assert question_id is not None
    return question_id


async def test_answers_of_returns_only_that_conversations_rows(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    mine, other = await _conversation(db_session, user), await _conversation(db_session, user)
    question = await _question(db_session)
    for conversation in (mine, other):
        db_session.add(
            ConversationAnswer(
                conversation_id=conversation.conversation_id,
                question_id=question,
                answer={"value": "5"},
                source="guided_flow",
            )
        )
    await db_session.flush()

    rows = await db.chat.answers_of(db_session, mine.conversation_id)

    assert [r.conversation_id for r in rows] == [mine.conversation_id]


async def test_answers_of_with_refresh_rereads_a_row_this_session_already_holds(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session)
    conversation = await _conversation(db_session, user)
    question = await _question(db_session)
    row = ConversationAnswer(
        conversation_id=conversation.conversation_id,
        question_id=question,
        answer={"value": "5"},
        source="guided_flow",
    )
    db_session.add(row)
    await db_session.flush()
    # what another request saved while this one waited on a lock
    await db_session.execute(
        text("UPDATE chat.conversation_answer SET answer = CAST(:a AS jsonb)"),
        {"a": '{"value": "saved elsewhere"}'},
    )

    [stale] = await db.chat.answers_of(db_session, conversation.conversation_id)
    before = dict(stale.answer)
    [reread] = await db.chat.answers_of(db_session, conversation.conversation_id, refresh=True)

    assert before == {"value": "5"}  # without refresh the held copy wins
    assert reread is row
    assert reread.answer == {"value": "saved elsewhere"}


async def test_active_conversation_ignores_other_users_and_finished_ones(
    db_session: AsyncSession,
) -> None:
    me, someone = await _user(db_session), await _user(db_session)
    await _conversation(db_session, me, status=ConversationStatus.COMPLETED)
    await _conversation(db_session, someone)
    assert await db.chat.active_conversation(db_session, me) is None

    active = await _conversation(db_session, me)

    found = await db.chat.active_conversation(db_session, me)
    assert found is not None and found.conversation_id == active.conversation_id


async def test_resumable_conversation_is_active_or_paused_for_that_task_only(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session)
    task = await _task(db_session)
    other_task = await _task(db_session)
    await _conversation(db_session, user, task, ConversationStatus.COMPLETED)
    await _conversation(db_session, user, task, ConversationStatus.CANCELLED)
    await _conversation(db_session, user, other_task, ConversationStatus.PAUSED)
    assert await db.chat.resumable_conversation(db_session, user, task[0]) is None

    paused = await _conversation(db_session, user, task, ConversationStatus.PAUSED)

    found = await db.chat.resumable_conversation(db_session, user, task[0])
    assert found is not None and found.conversation_id == paused.conversation_id


async def test_open_task_ids_are_the_users_active_or_paused_tasks(
    db_session: AsyncSession,
) -> None:
    me, other = await _user(db_session), await _user(db_session)
    active, paused, done, theirs = [await _task(db_session) for _ in range(4)]
    await _conversation(db_session, me, active, ConversationStatus.ACTIVE)
    await _conversation(db_session, me, paused, ConversationStatus.PAUSED)
    await _conversation(db_session, me, done, ConversationStatus.COMPLETED)
    await _conversation(db_session, other, theirs, ConversationStatus.ACTIVE)

    found = await db.chat.open_task_ids(
        db_session, me, [active[0], paused[0], done[0], theirs[0], uuid4()]
    )

    assert found == {active[0], paused[0]}
    assert await db.chat.open_task_ids(db_session, me, []) == set()


async def test_pause_all_active_pauses_only_active_ones_and_counts_them(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session)
    active_a = await _conversation(db_session, user)
    active_b = await _conversation(db_session, await _user(db_session))
    done = await _conversation(db_session, user, status=ConversationStatus.COMPLETED)

    paused = await db.chat.pause_all_active(db_session)
    for conversation in (active_a, active_b, done):
        await db_session.refresh(conversation)

    assert paused == 2
    assert [c.status for c in (active_a, active_b, done)] == [
        ConversationStatus.PAUSED,
        ConversationStatus.PAUSED,
        ConversationStatus.COMPLETED,
    ]


async def test_lock_conversation_returns_the_row_or_none(db_session: AsyncSession) -> None:
    conversation = await _conversation(db_session, await _user(db_session))

    waiting = await db.chat.lock_conversation(db_session, conversation.conversation_id, wait=True)
    nowait = await db.chat.lock_conversation(db_session, conversation.conversation_id, wait=False)
    missing = await db.chat.lock_conversation(db_session, uuid4(), wait=True)

    assert waiting is conversation and nowait is conversation
    assert missing is None


def _db_error(sqlstate: str) -> DBAPIError:
    return DBAPIError("SELECT", {}, SimpleNamespace(sqlstate=sqlstate))  # type: ignore[arg-type]


async def test_a_lock_held_by_someone_else_means_the_answer_is_in_flight() -> None:
    session = MagicMock(execute=AsyncMock(side_effect=_db_error("55P03")))

    with pytest.raises(AnswerInFlight):
        await db.chat.lock_conversation(session, uuid4(), wait=False)


async def test_any_other_database_error_is_not_swallowed() -> None:
    session = MagicMock(execute=AsyncMock(side_effect=_db_error("23503")))

    with pytest.raises(DBAPIError):
        await db.chat.lock_conversation(session, uuid4(), wait=False)
