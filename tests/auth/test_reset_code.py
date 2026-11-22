import uuid
from unittest.mock import patch

from src.auth import tokens
from src.config import settings

USER = uuid.uuid4()


def test_a_code_is_six_digits_and_keeps_its_leading_zeros() -> None:
    with patch("src.auth.tokens.secrets.randbelow", return_value=7):
        assert tokens.new_reset_code() == "000007"
    for _ in range(50):
        code = tokens.new_reset_code()
        assert len(code) == 6 and code.isdigit()


def test_what_the_person_types_is_read_as_digits() -> None:
    assert tokens.normalize_reset_code(" 123 456\n") == "123456"
    assert tokens.normalize_reset_code("๑๒๓๔๕๖") == "123456"


def test_the_stored_hash_matches_only_the_right_code_for_the_right_user() -> None:
    stored = tokens.hash_reset_code(USER, "123456")

    assert stored != "123456"
    assert tokens.reset_code_matches(stored, USER, "123456")
    assert tokens.reset_code_matches(stored, USER, " 123 456 ")
    assert tokens.reset_code_matches(stored, USER, "๑๒๓๔๕๖")
    assert not tokens.reset_code_matches(stored, USER, "123457")
    assert not tokens.reset_code_matches(stored, uuid.uuid4(), "123456")


def test_the_hash_depends_on_the_secret_key(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    before = tokens.hash_reset_code(USER, "123456")
    monkeypatch.setattr(settings, "JWT_KEY", settings.JWT_KEY + "-rotated")

    assert tokens.hash_reset_code(USER, "123456") != before
    assert not tokens.reset_code_matches(before, USER, "123456")
