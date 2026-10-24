"""All database access (CONVENTIONS.md §2-3).

One module per database schema; each module's docstring lists the tables it
owns, and scripts/check_conventions.py fails CI if two modules claim the
same table or SQL for an owned table appears outside src/db/.

Call queries as `from src import db` -> `await db.tasks.latest_response(...)`.
"""

from src.db import (
    agriculture,
    auth,
    chat,
    collection,
    forms,
    models,
    notify,
    processing,
    ref,
    reports,
    research,
    storage,
    tasks,
)

__all__ = [
    "agriculture",
    "auth",
    "chat",
    "collection",
    "forms",
    "models",
    "notify",
    "processing",
    "ref",
    "reports",
    "research",
    "storage",
    "tasks",
]
