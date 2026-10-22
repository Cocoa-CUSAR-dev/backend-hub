"""Date and time fields written the way mobile-backend (Go) wrote them.

The mobile app was built on Go's JSON, where every timestamp and date went
out as RFC 3339 with a "Z": a DATE as midnight ("2026-01-02T00:00:00Z"), a
zone-less timestamp labelled as-is. The merged backend keeps that output for
the routes the app calls, so the app does not change at the same time.

Only the JSON is affected: the Python values stay what the column holds
(naive Bangkok wall-clock for task times, naive UTC for submitted_at and
created_at -- see clock.py).
"""

from datetime import UTC, date, datetime
from typing import Annotated

from pydantic import PlainSerializer


def _as_go_time(moment: datetime) -> str:
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC).replace(tzinfo=None)
    return moment.isoformat() + "Z"


def _as_go_date(day: date) -> str:
    return f"{day.isoformat()}T00:00:00Z"


GoTime = Annotated[datetime, PlainSerializer(_as_go_time, return_type=str)]
GoDate = Annotated[date, PlainSerializer(_as_go_date, return_type=str)]
