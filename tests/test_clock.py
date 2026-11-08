from datetime import UTC, datetime, timedelta

from src.clock import BANGKOK, bangkok_now, bangkok_today, to_bangkok, utc_now


def test_now_functions_are_timezone_aware() -> None:
    assert utc_now().utcoffset() == timedelta(0)
    assert bangkok_now().utcoffset() == timedelta(hours=7)


def test_bangkok_today_is_the_bangkok_calendar_day() -> None:
    assert bangkok_today() == datetime.now(BANGKOK).date()


def test_to_bangkok_treats_naive_values_as_utc() -> None:
    # 20:00 UTC is already the next morning in Bangkok -- the case that
    # broke "today" for reminders sent between 00:00 and 07:00.
    shown = to_bangkok(datetime(2026, 9, 7, 20, 0))
    assert (shown.day, shown.hour) == (8, 3)
    assert to_bangkok(datetime(2026, 9, 7, 20, 0, tzinfo=UTC)) == shown
