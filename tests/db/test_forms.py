"""Form reads: src/db/forms.py, src/db/ref.py, and the pieces built on them
that need real rows (src/forms/registry.py, src/forms/detail.py)."""

import json
from collections.abc import Iterator
from datetime import datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src import db
from src.forms import detail, registry
from tests.db.conftest import insert


@pytest.fixture(autouse=True)
def _fresh_caches() -> Iterator[None]:
    registry.reset_cache()
    detail.reset_cache()
    yield
    registry.reset_cache()
    detail.reset_cache()


async def _form(session: AsyncSession, **extra: object) -> tuple[UUID, UUID]:
    task_id = await insert(session, "form.task", title="Harvest", open_at=datetime(2026, 10, 1))
    form_id = await insert(
        session, "form.task_form", task_id=task_id, title="Harvest form", handler="harvest", **extra
    )
    assert task_id is not None and form_id is not None
    return task_id, form_id


async def _section(session: AsyncSession, form_id: UUID, sort_order: int, **extra: object) -> UUID:
    section_id = await insert(
        session,
        "form.section",
        form_id=form_id,
        title=f"S{sort_order}",
        sort_order=sort_order,
        **extra,
    )
    assert section_id is not None
    return section_id


async def _question(
    session: AsyncSession, section_id: UUID, field_name: str, sort_order: int, **extra: object
) -> UUID:
    question_id = await insert(
        session,
        "form.question",
        section_id=section_id,
        label=field_name,
        field_name=field_name,
        sort_order=sort_order,
        **extra,
    )
    assert question_id is not None
    return question_id


# --- src/db/forms.py --------------------------------------------------------


async def test_form_of_task_and_forms_of_tasks(db_session: AsyncSession) -> None:
    task_id, form_id = await _form(db_session, version=3, is_multiple_submit=True)

    one = await db.forms.form_of_task(db_session, task_id)
    many = await db.forms.forms_of_tasks(db_session, [task_id, uuid4()])

    assert one is not None
    assert (one.form_id, one.version, one.handler, one.is_multiple_submit) == (
        form_id,
        3,
        "harvest",
        True,
    )
    assert many == {task_id: one}
    assert await db.forms.form_of_task(db_session, uuid4()) is None
    assert await db.forms.forms_of_tasks(db_session, []) == {}


async def test_form_rows_join_sections_questions_and_rules_in_order(
    db_session: AsyncSession,
) -> None:
    _, form_id = await _form(db_session)
    second = await _section(db_session, form_id, 2)
    first = await _section(db_session, form_id, 1)
    await _section(db_session, form_id, 3)  # no questions
    await _question(db_session, first, "weight_kg", 2)
    await _question(db_session, first, "harvest_date", 1)
    await _question(db_session, second, "note", 1)
    await insert(
        db_session,
        "form.field_validation_rule",
        field_name="weight_kg",
        validation_rule=json.dumps({"type": "number", "min": 0, "errorMessage": "kg"}),
    )

    rows = await db.forms.form_rows(db_session, form_id)

    assert [(r.section_sort_order, r.field_name) for r in rows] == [
        (1, "harvest_date"),
        (1, "weight_kg"),
        (2, "note"),
        (3, None),
    ]
    assert rows[1].validation_rule == {"type": "number", "min": 0, "errorMessage": "kg"}
    assert rows[0].validation_rule is None


async def test_form_rows_is_empty_for_an_unknown_form(db_session: AsyncSession) -> None:
    assert await db.forms.form_rows(db_session, uuid4()) == []


async def test_existing_tables_includes_real_tables(db_session: AsyncSession) -> None:
    tables = await db.forms.existing_tables(db_session)
    assert {"agriculture.plot", "ref.plot_constant"} <= tables


# --- src/db/ref.py ----------------------------------------------------------


async def test_ref_columns_and_choices(db_session: AsyncSession) -> None:
    province = await insert(db_session, "ref.province_constant", province_name_th="เชียงใหม่")

    assert await db.ref.columns_of(db_session, "province_constant") == [
        "province_id",
        "province_name_th",
        "province_name_en",
        "description",
    ]
    assert await db.ref.columns_of(db_session, "no_such_constant") == []
    choices = await db.ref.choices(
        db_session, "province_constant", "province_id", "province_name_th"
    )
    assert choices == [db.ref.Choice(id=str(province), name="เชียงใหม่")]


async def test_ref_choices_with_parent_take_the_parent_from_the_link_table(
    db_session: AsyncSession,
) -> None:
    farm = await insert(db_session, "agriculture.farm", farm_name="Farm")
    linked = await insert(db_session, "agriculture.plot", farm_id=farm, plot_name="A")
    await insert(db_session, "ref.plot_constant", plot_id=linked, plot_name="A")
    orphan = uuid4()
    await insert(db_session, "ref.plot_constant", plot_id=orphan, plot_name="B")

    choices = await db.ref.choices_with_parent(
        db_session,
        "plot_constant",
        "plot_id",
        "plot_name",
        link_table="agriculture.plot",
        child_column="plot_id",
        parent_column="farm_id",
    )

    assert sorted(choices, key=lambda c: c.name) == [
        db.ref.Choice(id=str(linked), name="A", parent_id=str(farm)),
        db.ref.Choice(id=str(orphan), name="B", parent_id=None),
    ]


async def test_ref_grade_codes_use_the_name_as_the_id(db_session: AsyncSession) -> None:
    await insert(db_session, "ref.grade_constant", grade_name="A")

    assert await db.ref.grade_codes(db_session) == [db.ref.Choice(id="A", name="A")]


async def test_ref_refuses_unsafe_identifiers(db_session: AsyncSession) -> None:
    with pytest.raises(ValueError, match="identifier"):
        await db.ref.choices(db_session, "plot_constant; DROP TABLE x", "plot_id", "plot_name")


# --- src/forms/registry.py --------------------------------------------------


async def test_handler_rows_read_the_seeded_registry(db_session: AsyncSession) -> None:
    rows = {row.handler: row for row in await db.forms.handler_rows(db_session)}

    assert (rows["harvest"].schema_name, rows["harvest"].table_name) == ("collection", "harvest")
    assert (
        rows["harvest_grade_detail"].parent_handler,
        rows["harvest_grade_detail"].parent_field,
    ) == (
        "harvest",
        "harvest_id",
    )


async def test_registry_maps_handlers_to_tables_that_exist(
    db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    # Own tables, created inside the test's transaction and rolled back with it.
    await db_session.execute(text("CREATE TABLE agriculture.probe_activity (probe_id uuid)"))
    await db_session.execute(text("CREATE TABLE agriculture.probe_retired (probe_id uuid)"))
    await db_session.execute(
        text(
            "INSERT INTO form.handler_registry (handler, schema_name, table_name, is_active)"
            " VALUES ('probe', 'agriculture', 'probe_activity', true),"
            "        ('probe_missing', 'agriculture', 'probe_gone', true),"
            "        ('probe_bad_name', 'agriculture', 'probe_activity; --', true),"
            "        ('probe_retired', 'agriculture', 'probe_retired', false)"
        )
    )

    reg = await registry.current(db_session)

    assert reg.tables["probe"] == "agriculture.probe_activity"
    for dropped in ("probe_missing", "probe_bad_name", "probe_retired"):
        assert dropped not in reg.tables
    # Parents come from every row, as seeded from V30.
    assert reg.parents["harvest_grade_detail"] == "harvest"
    assert {"farm_activity_id", "harvest_id", "batch_id"} <= reg.parent_fields
    assert "missing table" in caplog.text and "invalid identifier" in caplog.text


async def test_registry_keeps_valid_dependencies_and_drops_broken_ones(
    db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    rows = [
        ("plot_id", "farm_id", "agriculture.plot", "farm_id"),
        ("ghost_id", "farm_id", "agriculture.no_such_table", "farm_id"),
        ("bad_id", "farm_id", "agriculture.plot; --", "farm_id"),
    ]
    for child, parent, link_table, link_column in rows:
        await insert(
            db_session,
            "form.field_dependency",
            child_field=child,
            parent_field=parent,
            link_table=link_table,
            link_column=link_column,
            label_th="แปลง",
        )

    found = await registry.dependencies(db_session)

    assert list(found) == ["plot_id"]
    assert found["plot_id"].link_table == "agriculture.plot"
    assert "missing table" in caplog.text and "invalid identifier" in caplog.text


async def test_registry_lists_valid_active_handlers_in_sort_order(
    db_session: AsyncSession,
) -> None:
    await db_session.execute(text("CREATE TABLE agriculture.probe_a (probe_id uuid)"))
    await db_session.execute(text("CREATE TABLE agriculture.probe_b (probe_id uuid)"))
    await db_session.execute(
        text(
            "INSERT INTO form.handler_registry"
            " (handler, schema_name, table_name, is_active, sort_order, label_th,"
            "  extra_required_fields)"
            " VALUES ('probe_z', 'agriculture', 'probe_a', true, -2, 'ซี', '{farm_id}'),"
            "        ('probe_y', 'agriculture', 'probe_b', true, -1, 'วาย', '{}'),"
            "        ('probe_x', 'agriculture', 'probe_gone', true, -3, 'เอ็กซ์', '{}')"
        )
    )

    reg = await registry.current(db_session)

    names = [h.handler for h in reg.handlers]
    assert names[:2] == ["probe_z", "probe_y"]  # sort_order first, then the name
    assert "probe_x" not in names  # its table doesn't exist
    z = reg.handlers[0]
    assert (z.label_th, z.extra_required_fields) == ("ซี", ("farm_id",))


async def test_registry_lists_every_valid_dependency_for_the_data_map(
    db_session: AsyncSession,
) -> None:
    for parent in ("farm_id", "hub_id"):  # two rows for one child: the map draws both
        await insert(
            db_session, "form.field_dependency", child_field="plot_id", parent_field=parent,
            link_table="agriculture.plot", link_column=parent, label_th="แปลง",
        )  # fmt: skip

    reg = await registry.current(db_session)

    assert [(d.child_field, d.parent_field) for d in reg.dependency_list] == [
        ("plot_id", "farm_id"),
        ("plot_id", "hub_id"),
    ]
    assert reg.dependencies["plot_id"].parent_field == "farm_id"  # the first wins here


async def test_table_columns_in_order_with_not_null_and_foreign_keys(
    db_session: AsyncSession,
) -> None:
    await db_session.execute(
        text("CREATE TABLE agriculture.probe_parent (parent_id uuid PRIMARY KEY)")
    )
    await db_session.execute(
        text(
            "CREATE TABLE agriculture.probe_cols ("
            " probe_id uuid DEFAULT gen_random_uuid() PRIMARY KEY,"
            " parent_id uuid NOT NULL REFERENCES agriculture.probe_parent (parent_id),"
            " amount numeric, picked_at timestamp, note varchar(20) NOT NULL DEFAULT '',"
            " visits integer NOT NULL, is_dry boolean)"
        )
    )

    cols = await db.forms.table_columns(db_session, "agriculture", "probe_cols")

    assert [c.name for c in cols] == [
        "probe_id", "parent_id", "amount", "picked_at", "note", "visits", "is_dry",
    ]  # fmt: skip
    by_name = {c.name: c for c in cols}
    assert by_name["parent_id"].references == "agriculture.probe_parent"
    assert by_name["amount"].references is None
    assert [c.name for c in cols if c.not_null_without_default] == ["parent_id", "visits"]
    assert (by_name["note"].data_type, by_name["note"].udt_name) == ("character varying", "varchar")
    assert (by_name["visits"].udt_name, by_name["is_dry"].udt_name) == ("int4", "bool")
    assert by_name["picked_at"].data_type == "timestamp without time zone"
    assert await db.forms.table_columns(db_session, "agriculture", "no_such_table") == []


async def test_handler_forms_field_usage_and_labels(db_session: AsyncSession) -> None:
    task = await insert(db_session, "form.task", title="probe")
    form = await insert(
        db_session, "form.task_form", task_id=task, handler="probe_handler", version=1
    )
    section = await insert(db_session, "form.section", form_id=form, title="s", sort_order=0)
    off = await insert(
        db_session, "form.section", form_id=form, title="off", sort_order=1, is_active=False
    )
    for sec, field, label in (
        (section, "probe_field", " สวนของฉัน "),
        (section, "probe_field", "สวน"),
        (section, "probe_other", "อื่น"),
        (off, "probe_hidden", "ซ่อน"),
    ):
        await insert(
            db_session, "form.question", section_id=sec, label=label, field_name=field,
            input_type="VARCHAR", sort_order=0,
        )  # fmt: skip
    await insert(
        db_session, "form.question", section_id=section, label="สวน", field_name="probe_tie",
        input_type="VARCHAR", sort_order=0,
    )  # fmt: skip
    await insert(
        db_session, "form.question", section_id=section, label="ก่อน", field_name="probe_tie",
        input_type="VARCHAR", sort_order=0,
    )  # fmt: skip

    forms = [f for f in await db.forms.handler_forms(db_session) if f.handler == "probe_handler"]
    usage = (await db.forms.handler_field_usage(db_session))["probe_handler"]
    labels = await db.forms.field_labels(db_session)

    assert [(f.form_id, f.task_id) for f in forms] == [(form, task)]
    # The inactive section's question isn't asked, so it isn't counted.
    assert usage == {"probe_field": 1, "probe_other": 1, "probe_tie": 1}
    # One use each: trimmed, and a tie goes to the first label alphabetically.
    assert labels["probe_field"] == min("สวนของฉัน", "สวน")
    assert labels["probe_tie"] == "ก่อน"
    # Labels only skip inactive questions, as Kotlin's did, not inactive sections.
    assert labels["probe_hidden"] == "ซ่อน"


async def test_registry_is_cached(db_session: AsyncSession) -> None:
    assert await registry.dependencies(db_session) == {}
    await insert(
        db_session,
        "form.field_dependency",
        child_field="plot_id",
        parent_field="farm_id",
        link_table="agriculture.plot",
        link_column="farm_id",
        label_th="แปลง",
    )

    assert await registry.dependencies(db_session) == {}  # still the cached read
    registry.reset_cache()
    assert list(await registry.dependencies(db_session)) == ["plot_id"]


# --- src/forms/detail.py ----------------------------------------------------


async def test_load_form_builds_sections_questions_and_choices(db_session: AsyncSession) -> None:
    _, form_id = await _form(db_session, description="desc", is_multiple_submit=True)
    farm = await insert(db_session, "agriculture.farm", farm_name="Farm")
    plot = await insert(db_session, "agriculture.plot", farm_id=farm, plot_name="Plot A")
    await insert(db_session, "ref.plot_constant", plot_id=plot, plot_name="Plot A")
    province = await insert(db_session, "ref.province_constant", province_name_th="น่าน")
    await insert(db_session, "ref.grade_constant", grade_name="A")
    await insert(
        db_session,
        "form.field_dependency",
        child_field="plot_id",
        parent_field="farm_id",
        link_table="agriculture.plot",
        link_column="farm_id",
        label_th="แปลง",
    )
    section = await _section(db_session, form_id, 1, description="first")
    hidden = await _section(db_session, form_id, 2, is_active=False)
    await _question(db_session, section, "plot_id", 1, input_type="OPTION", is_mandatory=True)
    await _question(db_session, section, "province_id", 2, input_type="OPTION")
    await _question(db_session, section, "grade_code", 3, input_type="OPTION")
    await _question(
        db_session,
        section,
        "weight_kg",
        4,
        input_type="NUMBER",
        default_value=json.dumps(0),
        carry_forward=True,
    )
    await _question(db_session, hidden, "note", 1, is_active=False)

    form = await detail.load_form(db_session, form_id)

    assert form is not None
    assert (form.form_id, form.title, form.description, form.is_multiple_submit) == (
        form_id,
        "Harvest form",
        "desc",
        True,
    )
    first, second = form.sections
    assert (first.title, first.description, first.is_active) == ("S1", "first", True)
    assert second.is_active is False
    assert second.questions[0].is_active is False

    plot_q, province_q, grade_q, weight_q = first.questions
    assert plot_q.is_mandatory is True
    assert plot_q.depends_on == "farm_id"
    assert [(c.id, c.parent_id) for c in plot_q.choices or []] == [(str(plot), str(farm))]
    assert province_q.depends_on is None
    assert [(c.id, c.name) for c in province_q.choices or []] == [(str(province), "น่าน")]
    assert [(c.id, c.name) for c in grade_q.choices or []] == [("A", "A")]
    assert weight_q.choices is None
    assert weight_q.default_value == 0
    assert weight_q.carry_forward is True


async def test_load_form_gives_a_broken_option_field_no_choices(
    db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    _, form_id = await _form(db_session)
    section = await _section(db_session, form_id, 1)
    await _question(db_session, section, "mystery_id", 1, input_type="OPTION")
    await _question(db_session, section, "note", 2)

    form = await detail.load_form(db_session, form_id)

    assert form is not None
    broken, note = form.sections[0].questions
    assert broken.choices == []
    assert note.field_name == "note"
    assert "could not load choices" in caplog.text


async def test_load_form_reports_a_ref_table_without_the_expected_columns(
    db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    # Rolled back with the test, like every row.
    await db_session.execute(text("CREATE TABLE ref.odd_constant (code uuid, odd_name text)"))
    await db_session.execute(text("CREATE TABLE ref.half_constant (half_id uuid, title text)"))
    _, form_id = await _form(db_session)
    section = await _section(db_session, form_id, 1)
    await _question(db_session, section, "odd_id", 1, input_type="OPTION")  # no odd_id column
    await _question(db_session, section, "half_id", 2, input_type="OPTION")  # no half_name*

    form = await detail.load_form(db_session, form_id)

    assert form is not None
    assert [q.choices for q in form.sections[0].questions] == [[], []]
    assert "could not load choices" in caplog.text


async def test_load_form_resolves_each_ref_table_once(db_session: AsyncSession) -> None:
    _, form_id = await _form(db_session)
    section = await _section(db_session, form_id, 1)
    await _question(db_session, section, "province_id", 1, input_type="OPTION")
    province = await insert(db_session, "ref.province_constant", province_name_th="น่าน")

    await detail.load_form(db_session, form_id)
    assert detail._choice_columns["province_id"] == (
        "province_constant",
        "province_id",
        "province_name_th",
    )
    # A second load reuses the resolved table but still reads fresh rows.
    await db_session.execute(text("ALTER TABLE ref.province_constant RENAME TO province_gone"))
    await db_session.execute(text("CREATE TABLE ref.province_constant AS TABLE ref.province_gone"))
    await db_session.execute(text("DELETE FROM ref.province_constant"))
    again = await detail.load_form(db_session, form_id)

    assert again is not None
    assert again.sections[0].questions[0].choices == []
    assert province is not None


async def test_load_form_is_none_for_an_unknown_form(db_session: AsyncSession) -> None:
    assert await detail.load_form(db_session, uuid4()) is None


async def test_load_form_without_sections(db_session: AsyncSession) -> None:
    _, form_id = await _form(db_session)

    form = await detail.load_form(db_session, form_id)

    assert form is not None
    assert form.sections == []


async def test_load_form_keeps_a_section_with_no_questions(db_session: AsyncSession) -> None:
    _, form_id = await _form(db_session)
    await _section(db_session, form_id, 1)

    form = await detail.load_form(db_session, form_id)

    assert form is not None
    assert [(s.title, s.questions) for s in form.sections] == [("S1", [])]


async def test_child_handlers_are_the_active_ones_that_have_a_parent(
    db_session: AsyncSession,
) -> None:
    await db_session.execute(
        text(
            "INSERT INTO form.handler_registry"
            " (handler, schema_name, table_name, parent_handler, parent_field, is_active)"
            " VALUES ('off_child', 'collection', 'off_child', 'harvest', 'harvest_id', false)"
        )
    )

    rows = await db.forms.child_handlers(db_session)

    by_handler = {r.handler: (r.parent_handler, r.parent_field) for r in rows}
    assert by_handler["farm_activity_fertilizer"] == ("farm_activity", "farm_activity_id")
    assert by_handler["drying_batch"] == ("batch", "batch_id")
    assert "off_child" not in by_handler  # inactive
    assert "harvest" not in by_handler  # standalone: no parent


# ---- the researcher's form list (FORM-01) ------------------------------------------


async def test_forms_with_sections_skips_a_form_without_one(db_session: AsyncSession) -> None:
    _, with_section = await _form(db_session, created_at=datetime(2026, 8, 1))
    _, newer = await _form(db_session, created_at=datetime(2026, 9, 1))
    _, empty = await _form(db_session, created_at=datetime(2026, 10, 1))
    await _section(db_session, with_section, 1)
    await _section(db_session, newer, 1)

    rows = await db.forms.forms_with_sections(db_session)

    ids = [r.form_id for r in rows if r.form_id in {with_section, newer, empty}]
    assert ids == [newer, with_section]


async def test_form_by_id(db_session: AsyncSession) -> None:
    task_id, form_id = await _form(db_session)

    form = await db.forms.form_by_id(db_session, form_id)

    assert form is not None and form.task_id == task_id and form.handler == "harvest"
    assert await db.forms.form_by_id(db_session, uuid4()) is None
