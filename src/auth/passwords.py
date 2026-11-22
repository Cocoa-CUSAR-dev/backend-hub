"""Password hashing. bcrypt, compatible with hashes written by Go
(golang.org/x/crypto/bcrypt) and Spring (BCryptPasswordEncoder), so every
existing account keeps its password.

hash_password and verify_password are async and run bcrypt in a worker
thread. A bcrypt call takes about 250 ms of CPU; on the event loop it
stalled every other request while anyone signed in (the load test in
docs-and-plan#322: p99 3.6 s at 100 users, 250 ms in a thread).
"""

import asyncio
import secrets

import bcrypt

from src.exceptions import Invalid

MIN_LENGTH = 6  # same rule mobile-backend enforced
_MAX_BYTES = 72  # bcrypt ignores anything longer


# No 0/O, 1/l/I: an admin reads this out or types it into a chat.
_TEMPORARY_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
TEMPORARY_LENGTH = 10


def new_temporary_password() -> str:
    return "".join(secrets.choice(_TEMPORARY_ALPHABET) for _ in range(TEMPORARY_LENGTH))


def check_strength(password: str) -> None:
    if len(password) < MIN_LENGTH:
        raise Invalid(
            "รหัสผ่านต้องมีอย่างน้อย 6 ตัวอักษร",
            fields={"password": f"ต้องมีอย่างน้อย {MIN_LENGTH} ตัวอักษร"},
        )
    if len(password.encode()) > _MAX_BYTES:
        raise Invalid("รหัสผ่านยาวเกินไป", fields={"password": "ยาวเกินไป"})


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(_hash, password)


async def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False  # LINE-only accounts have no password
    return await asyncio.to_thread(_check, password, password_hash)


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _check(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode()[:_MAX_BYTES], password_hash.encode())
    except ValueError:
        return False  # malformed hash in the DB
