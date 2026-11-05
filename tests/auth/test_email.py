"""An email as it is stored: no database needed."""

import pytest

from src.auth.service import clean_email
from src.exceptions import Invalid


def test_lower_case_and_trimmed() -> None:
    assert clean_email("  Somchai@Example.COM ") == "somchai@example.com"


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_blank_means_none(blank: str | None) -> None:
    assert clean_email(blank) is None


@pytest.mark.parametrize("bad", ["no-at", "a@b", "a b@c.com", "a@@b.com", "@b.com"])
def test_a_bad_one_is_refused(bad: str) -> None:
    with pytest.raises(Invalid):
        clean_email(bad)
