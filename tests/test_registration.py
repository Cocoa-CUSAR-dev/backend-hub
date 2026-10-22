from datetime import date

import pytest

from src import clock, pagination, registration
from src.exceptions import Invalid
from src.registration import GisPoint

A = GisPoint(lat=13.7, lng=100.5)
B = GisPoint(lat=13.8, lng=100.6)
C = GisPoint(lat=13.9, lng=100.4)


def test_wkt_is_lon_lat_with_six_decimals() -> None:
    assert registration.wkt_point(A) == "POINT(100.500000 13.700000)"
    assert registration.wkt_polygon([A, B, C]) == (
        "POLYGON((100.500000 13.700000,100.600000 13.800000,"
        "100.400000 13.900000,100.500000 13.700000))"
    )


def test_one_point_is_a_location_and_more_an_outline() -> None:
    assert registration.wkt_shape([A]).startswith("POINT(")
    assert registration.wkt_shape([A, B]).startswith("POLYGON((")


def test_found_date(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clock, "bangkok_today", lambda: date(2026, 10, 23))

    assert registration.found_date("") == date(2026, 10, 23)
    assert registration.found_date(" 2026-01-02 ") == date(2026, 1, 2)
    with pytest.raises(Invalid) as caught:
        registration.found_date("02/01/2026")
    assert caught.value.fields == {"found_date": "Use YYYY-MM-DD"}


@pytest.mark.parametrize(
    ("page", "page_size", "expected"),
    [
        (2, 20, (20, 40)),
        (-1, 20, (20, 0)),
        (0, 0, (50, 0)),
        (0, -5, (50, 0)),
        (1, 10_000, (500, 500)),
    ],
)
def test_pagination_window_falls_back_like_go(
    page: int, page_size: int, expected: tuple[int, int]
) -> None:
    assert pagination.window(page, page_size) == expected
