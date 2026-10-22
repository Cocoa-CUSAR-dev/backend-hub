"""ORM table classes (CONVENTIONS.md §2).

Only declares tables -- no queries. The schemas are owned by the `database`
repo's Flyway migrations (ADR 0005), never created here. Queries that use
these classes live in the `src/db/` file that owns the table (db/chat.py,
db/notify.py).

chat.conversation / chat.conversation_answer and notify.reminder_schedule /
reminder_recipient / reminder_log are mapped. The FKs of those tables point at
auth.user_account / form.task / form.question, which this service deliberately
has no mapped classes for (those are other services' domain). SQLAlchemy still
needs a Table registered in THIS Base's metadata to resolve a ForeignKey
string at flush time -- without one, session.commit() fails with
NoReferencedTableError -- so the bare Core Tables below exist purely for that.
They are not mapped classes.
"""

import uuid
from datetime import datetime, time
from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Table, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


def uuid_pk() -> Mapped[uuid.UUID]:
    """Matches this project's existing convention: uuid PK via gen_random_uuid(),
    named `<entity>_id` (never a generic `id`) -- e.g. `conversation_id`,
    `line_identity_id`. Assign the return value to the correctly-named
    attribute in each model rather than using a mixin, since the column name
    itself varies per table.
    """
    return mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---- stub tables, for FK resolution only ----------------------------------

Table(
    "user_account",
    Base.metadata,
    Column("user_id", PGUUID(as_uuid=True), primary_key=True),
    schema="auth",
)
Table(
    "task",
    Base.metadata,
    Column("task_id", PGUUID(as_uuid=True), primary_key=True),
    schema="form",
)
Table(
    "question",
    Base.metadata,
    Column("question_id", PGUUID(as_uuid=True), primary_key=True),
    schema="form",
)


# ---- chat ----------------------------------------------------------------


class ConversationStatus(StrEnum):
    """Outer state machine -- persisted as chat.conversation.status. Kept in
    sync with target-architecture.md #4. Lives here, beside the column, so
    src/db/chat.py can use it without importing the chatbot package.
    """

    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Conversation(Base):
    __tablename__ = "conversation"
    __table_args__ = {"schema": "chat"}

    conversation_id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("auth.user_account.user_id"))
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("form.task.task_id"))
    task_form_id: Mapped[uuid.UUID] = mapped_column()
    status: Mapped[str] = mapped_column(String)
    current_question_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    # Set once, by the parent-picker step for the 5 previously-blocked
    # handlers (src/line/parent_picker.py), as {"field_name": ..., "value":
    # ...}. Not a form.question answer -- that picker step has no real
    # question_id to satisfy ConversationAnswer's FK below, so it's merged
    # into the submission payload directly at confirm time instead. See
    # database/migrations/V13__conversation_parent_answer.sql.
    parent_answer: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Which page of the CURRENTLY open question's real choices the farmer is
    # viewing -- only meaningful for an OPTION question with more real
    # choices than fit in one Quick Reply message (see service.py's
    # _paginate). Reset to 0 whenever current_question_id changes to a
    # genuinely different question (service._advance_to). See
    # database/migrations/V17__conversation_current_page.sql.
    current_page: Mapped[int] = mapped_column(default=0, server_default="0")


class ConversationAnswer(Base):
    __tablename__ = "conversation_answer"
    __table_args__ = {"schema": "chat"}

    conversation_answer_id: Mapped[uuid.UUID] = uuid_pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat.conversation.conversation_id")
    )
    question_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("form.question.question_id"))
    answer: Mapped[dict[str, Any]] = mapped_column(JSONB)
    source: Mapped[str] = mapped_column(String)


# ---- notify --------------------------------------------------------------


class ReminderSchedule(Base):
    __tablename__ = "reminder_schedule"
    __table_args__ = {"schema": "notify"}

    schedule_id: Mapped[uuid.UUID] = uuid_pk()
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("form.task.task_id"))
    cadence: Mapped[str] = mapped_column(String)
    time_of_day: Mapped[time] = mapped_column()
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("auth.user_account.user_id"))


class ReminderRecipient(Base):
    """One targeting rule for a schedule: a whole role (recipient_type
    'ROLE') or one person ('USER'). A schedule with no rows here reminds
    everyone who still owes the task -- see users_owing_task in queries.py.
    """

    __tablename__ = "reminder_recipient"
    __table_args__ = {"schema": "notify"}

    recipient_id: Mapped[uuid.UUID] = uuid_pk()
    schedule_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("notify.reminder_schedule.schedule_id")
    )
    recipient_type: Mapped[str] = mapped_column(String)
    role_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("auth.user_account.user_id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column()


class ReminderLog(Base):
    """The idempotency source of truth for the periodic reminder check --
    NOT APScheduler's in-memory job state, which doesn't survive a restart
    (ADR 0006).
    """

    __tablename__ = "reminder_log"
    __table_args__ = {"schema": "notify"}

    log_id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("auth.user_account.user_id"))
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("form.task.task_id"))
    sent_at: Mapped[datetime] = mapped_column()
    channel: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
