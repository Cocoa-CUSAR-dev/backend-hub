"""TEMPORARY stand-in for the real farmer-facing task picker. Boom's
"FE - Create: pending to-do list UI" (Sprint 5, EPIC 4 "Reminders & Notif.")
is meant to replace this with a real LIFF screen -- until that ships, a
farmer types a keyword and gets their pending tasks back as LINE Quick Reply
buttons instead.

Temporary: this is production code reachable from real LINE messages, not
test scaffolding. Delete this file (and the keyword-handling branch in
src/chat/router.py that calls it) once Sprint 5's real picker ships.
"""

from dataclasses import dataclass
from uuid import UUID

from src import db
from src.actor import Actor
from src.database import Session
from src.exceptions import Forbidden
from src.tasks import service as tasks_service
from src.tasks.schemas import TaskOut

# The one keyword every hint message names (#186 item 1). START_KEYWORDS is a
# set, so `next(iter(...))` -- what the hint used to do -- could tell one
# farmer to type "start" and the next "ทำแบบฟอร์ม" depending on hash order.
# Thai first because that is what the farmer-facing copy and the rich menu
# already say.
PRIMARY_START_KEYWORD = "เริ่ม"

# Case-insensitive; message text is lowercased before matching.
START_KEYWORDS = {PRIMARY_START_KEYWORD, "start", "ทำแบบฟอร์ม"}

_QUICK_REPLY_LIMIT = 13  # LINE's own cap on Quick Reply items per message
_PAGE_SIZE = 50  # how many tasks to read at a time while filling the list


@dataclass(frozen=True)
class PendingTask:
    task_id: UUID
    task_form_id: UUID
    title: str
    handler: str
    # US2-3: true when this task already has a conversation that isn't done
    # (ACTIVE or PAUSED) -- lets reply_task_choices label it "ทำต่อ" instead
    # of "เริ่มทำ", and lets router.py's "start" postback resume it instead
    # of starting a duplicate one (see service.find_resumable_conversation).
    has_conversation: bool


async def list_pending_tasks(session: Session, actor: Actor) -> list[PendingTask]:
    """A task is "pending" while it is not COMPLETED: not answered yet, or a
    multiple-submit form (which never reaches COMPLETED, because the farmer is
    expected to file several rows against it). Overdue tasks stay, they are
    still owed. Only tasks with a form can be started, so the rest are left out.

    This is tasks.service.list_for, the same function the mobile app's task
    list uses: same visibility (a task with assignees is listed only to them),
    same order (newest first), same status. Which tasks are in the list is that
    function's job; what this adds is the Quick Reply cap and which of them the
    farmer already has a conversation open for.

    `actor` must be allowed to read forms (read:form:assigned); anyone else
    gets Forbidden, which the caller turns into a reply.

    handler is included so callers can warn the farmer before they invest
    time answering a form Go can't save yet -- previously this picker had no
    way to know that at all, and the farmer only found out after answering
    every question and tapping confirm.
    """
    if actor.user_id is None:
        raise Forbidden("A user is required")

    pending: list[TaskOut] = []
    page = 0
    while len(pending) < _QUICK_REPLY_LIMIT:
        rows = await tasks_service.list_for(session, actor, page=page, page_size=_PAGE_SIZE)
        pending += [t for t in rows if t.task_form_id is not None and t.status != "COMPLETED"]
        if len(rows) < _PAGE_SIZE:
            break
        page += 1

    pending = pending[:_QUICK_REPLY_LIMIT]
    open_tasks = await db.chat.open_task_ids(session, actor.user_id, [t.task_id for t in pending])
    return [
        PendingTask(
            task_id=task.task_id,
            task_form_id=task.task_form_id,
            title=task.title or "",
            handler=task.handler or "",
            has_conversation=task.task_id in open_tasks,
        )
        for task in pending
        if task.task_form_id is not None
    ]
