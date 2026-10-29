"""forms.handlers with the registry and the db layer faked: who may read, what
`required`, `dataType` and `allowedInputTypes` say for each column, and the
Data Map's counts. The SQL is in tests/db/test_forms.py, the whole answers
against Kotlin's in tests/contract/ (K-15, K-16, K-CAT)."""

from dataclasses import replace
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src import db
from src.actor import Actor
from src.db.forms import ColumnInfo, FieldDependencyRow, HandlerForm, HandlerRow
from src.db.tasks import TaskBrief
from src.exceptions import Forbidden, Invalid
from src.forms import handlers, registry
from src.forms.registry import Registry

SESSION: Any = object()
BUILDER = frozenset({"create:form:all", "read:form:all"})


def _actor(perms: frozenset[str]) -> Actor:
    return Actor(user_id=uuid4(), roles=frozenset(), perms=perms, channel="api")


def _handler(name: str, **more: Any) -> HandlerRow:
    values: dict[str, Any] = {
        "handler": name,
        "schema_name": "collection",
        "table_name": name,
        "parent_handler": None,
        "parent_field": None,
        "is_active": True,
        "label_th": f"ป้าย {name}",
    }
    return HandlerRow(**(values | more))


def _column(name: str, data_type: str, udt: str, **more: Any) -> ColumnInfo:
    values: dict[str, Any] = {
        "name": name,
        "data_type": data_type,
        "udt_name": udt,
        "not_null_without_default": False,
        "references": None,
    }
    return ColumnInfo(**(values | more))


PLOT = FieldDependencyRow("plot_id", "farm_id", "agriculture.plot", "farm_id", "แปลง")
HARVEST = _handler("harvest")
GRADE = _handler("harvest_grade_detail", parent_handler="harvest", parent_field="harvest_id")
COLUMNS = [
    _column("harvest_id", "uuid", "uuid", not_null_without_default=True),
    _column(
        "farm_id", "uuid", "uuid", not_null_without_default=True, references="agriculture.farm"
    ),
    _column("plot_id", "uuid", "uuid", references="agriculture.plot"),
    _column("harvest_date", "timestamp without time zone", "timestamp"),
    _column("grade_code", "character varying", "varchar", references="ref.grade_constant"),
    _column("note", "character varying", "varchar"),
    _column("kg", "numeric", "numeric"),
    _column("pods", "integer", "int4"),
    _column("is_dry", "boolean", "bool"),
    _column("picked_on", "date", "date"),
    _column("extra", "jsonb", "jsonb"),
]


@pytest.fixture(autouse=True)
def fake(monkeypatch: pytest.MonkeyPatch) -> dict[str, AsyncMock]:
    handlers.reset_cache()
    reg = Registry(
        tables={"harvest": "collection.harvest", "harvest_grade_detail": "x.y"},
        parents={"harvest_grade_detail": "harvest"},
        parent_fields=frozenset({"harvest_id"}),
        dependencies={"plot_id": PLOT},
        handlers=(HARVEST, GRADE),
        dependency_list=(PLOT,),
    )
    fakes = {
        "current": AsyncMock(return_value=reg),
        "table_columns": AsyncMock(return_value=COLUMNS),
        "handler_forms": AsyncMock(return_value=[]),
        "handler_field_usage": AsyncMock(return_value={}),
        "field_labels": AsyncMock(return_value={}),
        "task_briefs": AsyncMock(return_value={}),
        "response_counts_by_form": AsyncMock(return_value={}),
    }
    monkeypatch.setattr(registry, "current", fakes["current"])
    for name in ("table_columns", "handler_forms", "handler_field_usage", "field_labels"):
        monkeypatch.setattr(db.forms, name, fakes[name])
    for name in ("task_briefs", "response_counts_by_form"):
        monkeypatch.setattr(db.tasks, name, fakes[name])
    return fakes


async def test_handler_names_in_registry_order() -> None:
    assert await handlers.handler_names(SESSION, _actor(BUILDER)) == [
        "harvest",
        "harvest_grade_detail",
    ]


@pytest.mark.parametrize(
    ("call", "perm"),
    [
        (lambda a: handlers.handler_names(SESSION, a), "create:form:all"),
        (lambda a: handlers.handler_fields(SESSION, a, "harvest"), "create:form:all"),
        (lambda a: handlers.catalog(SESSION, a), "read:form:all"),
    ],
)
async def test_each_route_needs_kotlins_permission(call: Any, perm: str) -> None:
    other = "read:form:all" if perm == "create:form:all" else "create:form:all"
    with pytest.raises(Forbidden):
        await call(_actor(frozenset({other})))
    await call(_actor(frozenset({perm})))


async def test_fields_say_what_the_builder_needs(fake: dict[str, AsyncMock]) -> None:
    fields = {f.name: f for f in await handlers.handler_fields(SESSION, _actor(BUILDER), "harvest")}

    assert list(fields) == [c.name for c in COLUMNS]  # table order
    assert fake["table_columns"].await_args.args[1:] == ("collection", "harvest")
    assert [n for n, f in fields.items() if f.required] == ["harvest_id", "farm_id"]
    assert (fields["plot_id"].depends_on, fields["plot_id"].references) == (
        "farm_id",
        "agriculture.plot",
    )
    assert {n: f.data_type for n, f in fields.items()} == {
        "harvest_id": "uuid", "farm_id": "uuid", "plot_id": "uuid", "harvest_date": "timestamp",
        "grade_code": "varchar", "note": "varchar", "kg": "numeric", "pods": "integer",
        "is_dry": "boolean", "picked_on": "date", "extra": "jsonb",
    }  # fmt: skip
    assert {n: f.allowed_input_types for n, f in fields.items()} == {
        "harvest_id": ["OPTION"], "farm_id": ["OPTION"], "plot_id": ["OPTION"],
        "harvest_date": ["DATETIME", "DATE"], "grade_code": ["OPTION", "VARCHAR"],
        "note": ["VARCHAR"], "kg": ["FLOAT", "INT"], "pods": ["INT"], "is_dry": ["BOOLEAN"],
        "picked_on": ["DATE"], "extra": [],
    }  # fmt: skip


async def test_a_child_handlers_parent_field_is_not_required_but_extra_ones_are(
    fake: dict[str, AsyncMock],
) -> None:
    grade = replace(GRADE, extra_required_fields=("note",))
    fake["current"].return_value = replace(fake["current"].return_value, handlers=(HARVEST, grade))

    fields = await handlers.handler_fields(SESSION, _actor(BUILDER), "harvest_grade_detail")

    # harvest_id is NOT NULL, but the chatbot's parent picker fills it.
    assert [f.name for f in fields if f.required] == ["farm_id", "note"]


@pytest.mark.parametrize("name", ["no_such_handler", "harvest; --"])
async def test_an_unknown_handler_is_invalid(name: str, fake: dict[str, AsyncMock]) -> None:
    with pytest.raises(Invalid) as e:
        await handlers.handler_fields(SESSION, _actor(BUILDER), name)
    assert "handler" in e.value.fields
    fake["table_columns"].assert_not_awaited()


async def test_catalog_counts_forms_answers_and_the_newest_task(
    fake: dict[str, AsyncMock],
) -> None:
    old, new, undated, other = uuid4(), uuid4(), uuid4(), uuid4()
    forms = {name: uuid4() for name in ("old", "new", "undated", "other")}
    fake["handler_forms"].return_value = [
        HandlerForm("harvest", forms["old"], old),
        HandlerForm("harvest", forms["undated"], undated),
        HandlerForm("harvest", forms["new"], new),
        HandlerForm("someone_else", forms["other"], other),
    ]
    fake["task_briefs"].return_value = {
        old: TaskBrief("เก่า", datetime(2026, 1, 1)),
        new: TaskBrief("ใหม่", datetime(2026, 9, 1)),
        undated: TaskBrief("ไม่มีวัน", None),
        other: TaskBrief("อื่น", datetime(2027, 1, 1)),
    }
    fake["response_counts_by_form"].return_value = {
        forms["old"]: 2,
        forms["new"]: 3,
        forms["other"]: 9,
    }
    fake["handler_field_usage"].return_value = {"harvest": {"farm_id": 3}}
    fake["field_labels"].return_value = {"farm_id": "ฟาร์ม"}

    out = await handlers.catalog(SESSION, _actor(BUILDER))

    harvest, grade = out.handlers
    assert (harvest.forms.count, harvest.forms.latest_title) == (3, "ใหม่")
    assert harvest.forms.latest_open_at == datetime(2026, 9, 1)
    assert harvest.response_count == 5
    assert {f.name: f.used_by_forms for f in harvest.fields}["farm_id"] == 3
    assert (grade.forms.count, grade.forms.latest_title, grade.response_count) == (0, None, 0)
    assert (grade.table, grade.parent_handler, grade.parent_field) == (
        "collection.harvest_grade_detail",
        "harvest",
        "harvest_id",
    )
    assert [d.child_field for d in out.dependencies] == ["plot_id"]
    assert out.registration_tables[0].table == "agriculture.farmer"
    assert out.field_labels == {"farm_id": "ฟาร์ม"}
    body = out.model_dump(by_alias=True)
    assert set(body) == {"handlers", "dependencies", "registrationTables", "fieldLabels"}
    assert {"labelTh", "descriptionTh", "parentHandler", "responseCount"} <= set(
        body["handlers"][0]
    )
    assert {"usedByForms", "allowedInputTypes", "dependsOn"} <= set(
        body["handlers"][0]["fields"][0]
    )


async def test_a_task_with_no_date_is_the_newest_only_when_none_has_one(
    fake: dict[str, AsyncMock],
) -> None:
    task, form = uuid4(), uuid4()
    fake["handler_forms"].return_value = [HandlerForm("harvest", form, task)]
    fake["task_briefs"].return_value = {task: TaskBrief("ไม่มีวัน", None)}

    harvest = (await handlers.catalog(SESSION, _actor(BUILDER))).handlers[0]

    assert (harvest.forms.latest_title, harvest.forms.latest_open_at) == ("ไม่มีวัน", None)


async def test_catalog_is_cached(fake: dict[str, AsyncMock]) -> None:
    await handlers.catalog(SESSION, _actor(BUILDER))
    await handlers.catalog(SESSION, _actor(BUILDER))
    assert fake["handler_forms"].await_count == 1
    handlers.reset_cache()
    await handlers.catalog(SESSION, _actor(BUILDER))
    assert fake["handler_forms"].await_count == 2
