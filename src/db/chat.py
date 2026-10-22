"""Conversations and the answers collected in them.

Owns:
    chat.conversation
    chat.conversation_answer

All SQL for these tables lives in this file and nowhere else (CONVENTIONS.md §2).
Other code calls the functions here.

Transitional: these return the ORM objects from src/db/models.py, because
src/chat/service.py still changes a loaded Conversation in place and commits.
Plain rows with explicit update functions replace that when the chatbot's
service is rewritten; until then the queries, the row locks and the one write
through a side session are here, and the service holds no SQL.
"""

import logging
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from src.database import async_session_maker
from src.db.models import Conversation, ConversationAnswer, ConversationStatus
from src.exceptions import AnswerInFlight

logger = logging.getLogger(__name__)

# Postgres SQLSTATE for "could not obtain lock on row" (FOR UPDATE NOWAIT).
_LOCK_NOT_AVAILABLE = "55P03"


async def answers_of(
    session: AsyncSession, conversation_id: UUID, *, refresh: bool = False
) -> list[ConversationAnswer]:
    """Every answer row of a conversation. refresh=True re-reads rows this
    session may already hold: after waiting on a lock, a stale in-memory copy
    would re-send what another request already saved.
    """
    statement = select(ConversationAnswer).where(
        ConversationAnswer.conversation_id == conversation_id
    )
    if refresh:
        statement = statement.execution_options(populate_existing=True)
    result = await session.execute(statement)
    return list(result.scalars().all())


async def lock_conversation(
    session: AsyncSession,
    conversation_id: UUID,
    *,
    wait: bool,
    refresh: bool = False,
) -> Conversation | None:
    """Loads the conversation FOR UPDATE and holds the row lock until the
    caller's transaction ends. None means the row is gone.

    wait=True blocks until the lock is free, so two quick taps both land, one
    after the other. wait=False is NOWAIT: a second message for a conversation
    that is mid-answer is dropped instead of queued, and this raises
    AnswerInFlight so the caller can say nothing.

    refresh=True re-reads a row this session already loaded, which is needed
    after waiting on the lock: without it the attributes read before the wait
    are returned and the waiter acts on stale state.
    """
    statement = (
        select(Conversation)
        .where(Conversation.conversation_id == conversation_id)
        .with_for_update(nowait=not wait)
    )
    if refresh:
        statement = statement.execution_options(populate_existing=True)
    try:
        return (await session.execute(statement)).scalar_one_or_none()
    except DBAPIError as exc:
        # Checked on the SQLSTATE rather than the wrapped exception type: the
        # asyncpg dialect re-wraps the driver error but still proxies .sqlstate.
        if getattr(exc.orig, "sqlstate", None) != _LOCK_NOT_AVAILABLE:
            raise
        logger.info(
            "conversation_id=%s already has an answer in flight -- dropping this message",
            conversation_id,
        )
        raise AnswerInFlight() from exc


async def active_conversation(session: AsyncSession, user_id: UUID) -> Conversation | None:
    """The conversation the user is in the middle of, if any."""
    result = await session.execute(
        select(Conversation).where(
            Conversation.user_id == user_id,
            Conversation.status == ConversationStatus.ACTIVE,
        )
    )
    return result.scalars().first()


async def resumable_conversation(
    session: AsyncSession, user_id: UUID, task_id: UUID
) -> Conversation | None:
    """The user's not-yet-done conversation for this task (ACTIVE or PAUSED).
    COMPLETED and CANCELLED rows are excluded, so they can always start again.
    """
    result = await session.execute(
        select(Conversation).where(
            Conversation.user_id == user_id,
            Conversation.task_id == task_id,
            Conversation.status.in_([ConversationStatus.ACTIVE, ConversationStatus.PAUSED]),
        )
    )
    return result.scalars().first()


async def open_task_ids(session: AsyncSession, user_id: UUID, task_ids: list[UUID]) -> set[UUID]:
    """Which of these tasks the user has a not-yet-done conversation for
    (ACTIVE or PAUSED)."""
    if not task_ids:
        return set()
    result = await session.execute(
        select(Conversation.task_id).where(
            Conversation.user_id == user_id,
            Conversation.task_id.in_(task_ids),
            Conversation.status.in_([ConversationStatus.ACTIVE, ConversationStatus.PAUSED]),
        )
    )
    return set(result.scalars())


async def pause_all_active(session: AsyncSession) -> int:
    """Pauses every ACTIVE conversation. Returns how many were paused."""
    raw_result = await session.execute(
        update(Conversation)
        .where(Conversation.status == ConversationStatus.ACTIVE)
        .values(status=ConversationStatus.PAUSED)
    )
    # session.execute()'s declared return type is the general Result[Any]; an
    # UPDATE always returns the CursorResult subtype that carries rowcount.
    return cast(CursorResult[Any], raw_result).rowcount


async def record_submitted(row: ConversationAnswer, submitted: list[str]) -> None:
    """Durably records which choices already reached Go, in its own short
    transaction rather than the caller's.

    confirm_conversation holds a FOR UPDATE lock on the conversation for its
    whole fan-out, and a COMMIT on that session would release it, letting a
    second confirm send the remaining rows again. This is a different table
    from the locked row, so it never waits on it.

    The in-memory copy is updated with set_committed_value, which does NOT
    mark the row dirty. If the calling session flushed its own UPDATE of this
    row, its transaction would hold the row lock while this one waited on it:
    a deadlock Postgres cannot detect, since both connections belong to the
    same request.
    """
    answer = {**row.answer, "submitted": submitted}
    async with async_session_maker() as side_session:
        await side_session.execute(
            update(ConversationAnswer)
            .where(ConversationAnswer.conversation_answer_id == row.conversation_answer_id)
            .values(answer=answer)
        )
        await side_session.commit()
    set_committed_value(row, "answer", answer)
