"""Async DB engine, sessions and transactions.

The `database` repo (Flyway) is the only schema owner -- this service never
creates or alters tables. Queries themselves live in src/db/, one module per
schema (CONVENTIONS.md §2).
"""

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from src.config import settings

engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
async_session_maker = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    """Shared declarative base for ORM models."""


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session


@asynccontextmanager
async def transaction(session: AsyncSession) -> AsyncIterator[AsyncSession]:
    """All-or-nothing block: commits if the body finishes, rolls back if it
    raises. Use it in a service when several writes must succeed together,
    e.g. saving an answer and its handler-table row.
    """
    try:
        yield session
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
