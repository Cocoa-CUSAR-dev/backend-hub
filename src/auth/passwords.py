"""Password hashing. bcrypt, compatible with hashes written by Go
(golang.org/x/crypto/bcrypt) and Spring (BCryptPasswordEncoder), so every
existing account keeps its password.
"""

import bcrypt

from src.exceptions import Invalid

MIN_LENGTH = 6  # same rule mobile-backend enforced
_MAX_BYTES = 72  # bcrypt ignores anything longer


def check_strength(password: str) -> None:
    if len(password) < MIN_LENGTH:
        raise Invalid(
            "รหัสผ่านต้องมีอย่างน้อย 6 ตัวอักษร",
            fields={"password": f"ต้องมีอย่างน้อย {MIN_LENGTH} ตัวอักษร"},
        )
    if len(password.encode()) > _MAX_BYTES:
        raise Invalid("รหัสผ่านยาวเกินไป", fields={"password": "ยาวเกินไป"})


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False  # LINE-only accounts have no password
    try:
        return bcrypt.checkpw(password.encode()[:_MAX_BYTES], password_hash.encode())
    except ValueError:
        return False  # malformed hash in the DB
