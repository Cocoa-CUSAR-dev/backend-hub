import bcrypt
import pytest

from src.auth import passwords
from src.exceptions import Invalid


def test_hash_and_verify() -> None:
    hashed = passwords.hash_password("secret1")
    assert passwords.verify_password("secret1", hashed)
    assert not passwords.verify_password("secret2", hashed)


def test_hashes_written_by_go_and_spring_still_verify() -> None:
    # Go's x/crypto/bcrypt and Spring's BCryptPasswordEncoder write "$2a$".
    legacy = bcrypt.hashpw(b"farmer-pass", bcrypt.gensalt(prefix=b"2a")).decode()
    assert legacy.startswith("$2a$")
    assert passwords.verify_password("farmer-pass", legacy)


def test_accounts_without_a_password_never_match() -> None:
    assert not passwords.verify_password("anything", None)
    assert not passwords.verify_password("anything", "not-a-bcrypt-hash")


def test_minimum_length_is_six() -> None:
    passwords.check_strength("123456")
    with pytest.raises(Invalid) as err:
        passwords.check_strength("12345")
    assert "password" in err.value.fields
