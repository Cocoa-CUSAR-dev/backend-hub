from datetime import date, datetime, timedelta, timezone

from pydantic import BaseModel

from src.go_json import GoDate, GoTime


class _Out(BaseModel):
    at: GoTime
    on: GoDate


def test_naive_times_get_a_z_and_dates_become_midnight() -> None:
    out = _Out(at=datetime(2026, 10, 1, 8, 30), on=date(2026, 1, 2))
    assert out.model_dump(mode="json") == {
        "at": "2026-10-01T08:30:00Z",
        "on": "2026-01-02T00:00:00Z",
    }


def test_aware_times_are_converted_to_utc_first() -> None:
    bangkok = timezone(timedelta(hours=7))
    out = _Out(at=datetime(2026, 10, 1, 9, tzinfo=bangkok), on=date(2026, 1, 2))
    assert out.model_dump(mode="json")["at"] == "2026-10-01T02:00:00Z"
