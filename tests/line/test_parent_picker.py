import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.db.forms import ChildHandlerRow
from src.line import handler_registry
from src.line.parent_picker import (
    EMPTY_PROMPT,
    PROMPT,
    UNSUPPORTED_PROMPT,
    ParentOption,
    choices_for,
    field_name_for,
    has_picker,
    kind_for_handler,
)

# The two maps this module hard-coded before US2-13, kept verbatim as the
# parity reference: the registry rows database V30 seeds must reproduce them
# exactly.
_LEGACY_PARENT_KIND_BY_HANDLER = {
    "farm_activity_fertilizer": "farm_activity",
    "farm_activity_chemical": "farm_activity",
    "harvest_grade_detail": "harvest",
    "fermentation_batch": "batch",
    "drying_batch": "batch",
}
_LEGACY_FIELD_NAME = {
    "farm_activity": "farm_activity_id",
    "harvest": "harvest_id",
    "batch": "batch_id",
}

# form.handler_registry's child rows exactly as V30 inserts them (the query
# only selects rows with a parent_handler).
_V30_CHILD_ROWS = [
    ChildHandlerRow(handler=h, parent_handler=kind, parent_field=_LEGACY_FIELD_NAME[kind])
    for h, kind in _LEGACY_PARENT_KIND_BY_HANDLER.items()
]


@pytest.fixture(autouse=True)
def _fresh_registry_cache() -> None:
    handler_registry.reset_cache()


@pytest.fixture
def registry_read() -> AsyncMock:
    """The registry rows the database would return; tests can swap them."""
    read = AsyncMock(return_value=_V30_CHILD_ROWS)
    with patch("src.db.forms.child_handlers", new=read):
        yield read


def _registry_session() -> MagicMock:
    return MagicMock()


async def test_registry_reproduces_the_legacy_parent_kinds(registry_read: AsyncMock) -> None:
    session = _registry_session()
    for handler, kind in _LEGACY_PARENT_KIND_BY_HANDLER.items():
        assert await kind_for_handler(session, handler) == kind


async def test_a_standalone_handler_has_no_parent_kind(registry_read: AsyncMock) -> None:
    assert await kind_for_handler(_registry_session(), "processing_record") is None


async def test_registry_reproduces_the_legacy_field_names(registry_read: AsyncMock) -> None:
    session = _registry_session()
    for kind, field_name in _LEGACY_FIELD_NAME.items():
        assert await field_name_for(session, kind) == field_name


def test_every_legacy_kind_has_a_picker_and_its_prompts() -> None:
    for kind in _LEGACY_FIELD_NAME:
        assert has_picker(kind)
        assert kind in PROMPT
        assert kind in EMPTY_PROMPT


async def test_a_child_handler_added_by_migration_needs_no_code(
    registry_read: AsyncMock,
) -> None:
    """Design doc §6: a new child handler under an EXISTING parent kind."""
    registry_read.return_value = [
        *_V30_CHILD_ROWS,
        ChildHandlerRow(
            handler="farm_activity_irrigation",
            parent_handler="farm_activity",
            parent_field="farm_activity_id",
        ),
    ]
    assert await kind_for_handler(_registry_session(), "farm_activity_irrigation") == (
        "farm_activity"
    )


def test_a_brand_new_parent_kind_is_refused_honestly() -> None:
    """A parent kind with no picker query here can't be started in chat."""
    assert not has_picker("inspection")
    assert "ยังไม่รองรับ" in UNSUPPORTED_PROMPT


async def test_the_registry_is_read_once_per_ttl(registry_read: AsyncMock) -> None:
    session = _registry_session()
    for handler in _LEGACY_PARENT_KIND_BY_HANDLER:
        await kind_for_handler(session, handler)
    assert registry_read.await_count == 1


def _mock_session(rows: list) -> MagicMock:
    session = MagicMock()
    session.execute = AsyncMock(return_value=rows)
    return session


async def test_choices_for_farm_activity_builds_labelled_options() -> None:
    rows = [
        SimpleNamespace(
            id=uuid.uuid4(), created_at=datetime(2026, 8, 1), farm_name="สวนทดสอบ", type_name="ใส่ปุ๋ย"
        )
    ]
    session = _mock_session(rows)

    result = await choices_for(session, "farm_activity", uuid.uuid4())

    assert result == [ParentOption(id=str(rows[0].id), label="ใส่ปุ๋ย 01/08/2026 — สวนทดสอบ")]


async def test_choices_for_batch_builds_labelled_options() -> None:
    rows = [SimpleNamespace(id=uuid.uuid4(), created_at=datetime(2026, 8, 1), place="สถานี A")]
    session = _mock_session(rows)

    result = await choices_for(session, "batch", uuid.uuid4())

    assert result == [ParentOption(id=str(rows[0].id), label="แบทช์ 01/08/2026 — สถานี A")]
