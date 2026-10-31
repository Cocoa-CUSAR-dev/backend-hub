"""ref.service pieces that need no database."""

from datetime import datetime

from src.ref.service import Filters, buddhist_label


def test_buddhist_label_matches_gos_to_char() -> None:
    assert buddhist_label(datetime(2026, 4, 30, 10)) == "30 Apr 2569"
    assert buddhist_label(datetime(2026, 1, 5)) == "05 Jan 2569"
    assert buddhist_label(None) is None


def test_29_february_becomes_the_28th_as_in_postgres() -> None:
    # 2024 + 543 = 2567, not a leap year: Postgres' + interval '543 years' gives 28 Feb.
    assert buddhist_label(datetime(2024, 2, 29)) == "28 Feb 2567"


def test_an_empty_filter_counts_as_not_given() -> None:
    assert Filters(province_id="").get("province_id") is None
    assert Filters(province_id="p").get("province_id") == "p"
