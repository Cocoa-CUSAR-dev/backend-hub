"""Seed users the way Go/Spring wrote them ($2a$ bcrypt), with roles and
each role's permissions. Shared by the auth and /me DB tests.
"""

import uuid

import bcrypt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def seed_role(session: AsyncSession, role: str, perms: list[str]) -> uuid.UUID:
    role_id = (
        await session.execute(
            text("SELECT role_id FROM auth.role WHERE role_name = :n"), {"n": role}
        )
    ).scalar_one_or_none()
    if role_id is None:
        role_id = (
            await session.execute(
                text("INSERT INTO auth.role (role_name) VALUES (:n) RETURNING role_id"),
                {"n": role},
            )
        ).scalar_one()
    for key in perms:
        perm_id = (
            await session.execute(
                text(
                    "INSERT INTO auth.permission (permission_key) VALUES (:k)"
                    " ON CONFLICT (permission_key) DO UPDATE"
                    " SET permission_key = EXCLUDED.permission_key RETURNING permission_id"
                ),
                {"k": key},
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO auth.role_permission (role_id, permission_id) VALUES (:r, :p)"
                " ON CONFLICT DO NOTHING"
            ),
            {"r": role_id, "p": perm_id},
        )
    await session.commit()
    return role_id


async def seed_user(
    session: AsyncSession,
    username: str,
    password: str,
    *,
    roles: dict[str, list[str]] | None = None,
    requires_reset: bool = False,
) -> uuid.UUID:
    pw = bcrypt.hashpw(password.encode(), bcrypt.gensalt(prefix=b"2a")).decode()
    user_id: uuid.UUID = (
        await session.execute(
            text(
                "INSERT INTO auth.user_account"
                " (username, password_hash, is_requires_password_reset)"
                " VALUES (:u, :p, :r) RETURNING user_id"
            ),
            {"u": username, "p": pw, "r": requires_reset},
        )
    ).scalar_one()
    for role, perms in (roles or {}).items():
        role_id = await seed_role(session, role, perms)
        await session.execute(
            text("INSERT INTO auth.user_role (user_id, role_id) VALUES (:u, :r)"),
            {"u": user_id, "r": role_id},
        )
    await session.commit()
    return user_id
