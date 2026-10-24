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
from src.database import Session

# The one keyword every hint message names (#186 item 1). START_KEYWORDS is a
# set, so `next(iter(...))` -- what the hint used to do -- could tell one
# farmer to type "start" and the next "ทำแบบฟอร์ม" depending on hash order.
# Thai first because that is what the farmer-facing copy and the rich menu
# already say.
PRIMARY_START_KEYWORD = "เริ่ม"

# Case-insensitive; message text is lowercased before matching.
START_KEYWORDS = {PRIMARY_START_KEYWORD, "start", "ทำแบบฟอร์ม"}

_QUICK_REPLY_LIMIT = 13  # LINE's own cap on Quick Reply items per message
_PAGE_SIZE = 50  # how many visible tasks to look at per read while filling the list


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


async def list_pending_tasks(session: Session, user_id: UUID) -> list[PendingTask]:
    """A task is "pending" if it has a form and this user has not answered it
    yet, OR its form is multiple-submit (in which case it stays listed no
    matter how many responses exist) -- mirrors mobile-backend's real GetTasks
    logic (form_handler.go).

    US4-4 (docs-and-plan#236): only the tasks this user can see, by the same
    rule the task list uses (src/db/tasks.py): a task that has assignees is
    listed only to them, and a task with none is listed to everyone, as it
    was before assignments were ever written -- so no existing task
    disappears. Deliberately blind to conversation status for this filter: a
    task with an ACTIVE/PAUSED-but-unconfirmed conversation has no response
    yet, so it already shows up here correctly -- has_conversation only
    changes how it's labeled/handled, not whether it's listed at all.

    handler is included so callers can warn the farmer before they invest
    time answering a form Go can't save yet -- previously this picker had no
    way to know that at all, and the farmer only found out after answering
    every question and tapping confirm.
    """
    pending: list[tuple[db.tasks.TaskRow, db.forms.TaskForm]] = []
    offset = 0
    while len(pending) < _QUICK_REPLY_LIMIT:
        page = await db.tasks.tasks_for_user(
            session, user_id, open_on=None, limit=_PAGE_SIZE, offset=offset
        )
        if not page:
            break
        forms = await db.forms.forms_of_tasks(session, [t.task_id for t in page])
        for task in page:
            form = forms.get(task.task_id)
            # A multiple-submit form is the whole point of this change: it
            # stays listed after the first submission, because the farmer is
            # expected to file several rows against it (three grades for one
            # harvest). Dropping it the moment one response existed made the
            # second one unreachable.
            if form is not None and (form.is_multiple_submit or not task.answered):
                pending.append((task, form))
        if len(page) < _PAGE_SIZE:
            break
        offset += _PAGE_SIZE

    pending = pending[:_QUICK_REPLY_LIMIT]
    open_tasks = await db.chat.open_task_ids(session, user_id, [t.task_id for t, _ in pending])
    return [
        PendingTask(
            task_id=task.task_id,
            task_form_id=form.form_id,
            title=task.title or "",
            handler=form.handler or "",
            has_conversation=task.task_id in open_tasks,
        )
        for task, form in pending
    ]
