"""Saving a confirmed chat answer.

The same function the mobile app's POST /tasks calls (`tasks.answers.submit`),
called in-process instead of through Go's POST /service/tasks. The caller is
the person the conversation belongs to, loaded as an Actor, so the service's
permission check (`create:response:own`, database V35) and its rules apply
exactly as they do in the app: the whole answer is validated before anything
is written, then the answer and its handler-table row are saved together.

It runs in its OWN short session, never the caller's.
`service.confirm_conversation` holds a FOR UPDATE lock on the conversation for
the whole fan-out of a multi-choice answer, and the service commits when it
finishes: committing the caller's session would release that lock and let a
second confirm send the same rows again (the reason the list of rows already
sent is also written through its own session: `record_submitted`, in the
chat module of src/db). The conversation row is not touched by the answer's tables, so
this never waits on the lock.

A failure surfaces as the service's own error (Forbidden, NotFound, Invalid,
HandlerNotSupported). The callers already treat HandlerNotSupported as
permanent and any other error as "nothing was saved, tapping confirm again
retries".
"""

from uuid import UUID

from src.auth.service import load_actor
from src.database import async_session_maker
from src.tasks import answers
from src.tasks.schemas import TaskSubmission


async def submit_task(submission: TaskSubmission) -> None:
    async with async_session_maker() as session:
        actor = await load_actor(session, UUID(submission.user_id), "line")
        await answers.submit(session, actor, UUID(submission.task_id), submission.answer)
