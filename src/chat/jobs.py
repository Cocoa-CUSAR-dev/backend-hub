"""Scheduled job bodies for conversation lifecycle (US2-3) -- kept separate
from src/reminders/jobs.py since this is conversation-state management, not
reminder delivery, even though both run on the same AsyncIOScheduler
instance (src/jobs/scheduler.py).
"""

import logging

from src import db
from src.database import async_session_maker

logger = logging.getLogger(__name__)


async def pause_idle_conversations() -> None:
    """Runs once daily (see scheduler.py) -- a fixed wall-clock cutoff, not
    a per-conversation idle timer: anything still ACTIVE by the time this
    runs is treated as "the farmer will pick this up another day" and gets
    paused. Doesn't touch PAUSED/COMPLETED/CANCELLED rows.

    This is the backstop for "went quiet and never came back" -- the
    same-turn "farmer is deliberately switching tasks right now" case is
    handled immediately instead, by service.pause_active_conversation
    whenever เริ่ม is typed. Deliberately silent (no proactive LINE push):
    that's the separate Reminders/Notif. epic's job, not this one's.
    """
    async with async_session_maker() as session:
        paused = await db.chat.pause_all_active(session)
        await session.commit()
    logger.info("pause_idle_conversations: paused %d conversation(s)", paused)
