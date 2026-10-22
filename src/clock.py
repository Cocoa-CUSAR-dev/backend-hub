"""The only place that reads the clock or knows about timezones (CONVENTIONS.md §9).

The database stores UTC. Bangkok time is only for what a person sees and for
"today" (a farmer's day, a reminder's time of day). Reading the clock through
these functions also gives tests one thing to patch.
"""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

BANGKOK = ZoneInfo("Asia/Bangkok")


def utc_now() -> datetime:
    """Timezone-aware now in UTC -- use for anything written to the DB."""
    return datetime.now(UTC)


def bangkok_now() -> datetime:
    """Timezone-aware now in Bangkok -- use for "what time is it for the user"."""
    return datetime.now(BANGKOK)


def bangkok_today() -> date:
    """The user's calendar day (Thai farmers), not the server's."""
    return bangkok_now().date()


def to_bangkok(moment: datetime) -> datetime:
    """Convert a stored time for display. Naive values are treated as UTC,
    since that is what the DB columns hold.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(BANGKOK)
