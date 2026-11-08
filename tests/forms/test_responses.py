"""forms.responses against real Postgres: the per-question view, the review
table (names, sources, AI-only), one answer, and a correction with its audit
row. Rolled back after each test. The answers against Kotlin's are in
tests/contract/ (K-23..K-26)."""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.actor import Actor
from src.exceptions import Forbidden, FormNotFound, Invalid, ResponseNotFound
from src.forms import responses
from src.forms.schemas import CorrectionIn
from tests.db.conftest import db_session, insert  # noqa: F401 -- the fixture

RESEARCHER_PERMS = frozenset({"read:response:all", "update:response:all"})


@dataclass
class World:
    form_id: UUID
    task_id: UUID
    questions: dict[str, UUID]
    farmer: UUID
    processor: UUID
    nameless: UUID
    responses: dict[str, UUID]


async def _user(session: AsyncSession, name: str) -> UUID:
    user_id = await insert(session, "auth.user_account", username=f"{name}-{uuid4().hex[:6]}")
    assert user_id is not None
    return user_id


async def _response(
    session: AsyncSession, w: World, user: UUID, at: datetime | None, answer: dict[str, Any]
) -> UUID:
    response_id = await insert(
        session, "form.response", task_log_id=w.task_id, task_form_id=w.form_id, user_id=user,
        submitted_at=at, answer=json.dumps(answer, ensure_ascii=False), status="COMPLETED",
    )  # fmt: skip
    assert response_id is not None
    return response_id


@pytest.fixture
async def world(db_session: AsyncSession) -> World:  # noqa: F811
    s = db_session
    task = await insert(s, "form.task", title="ส่งโกโก้")
    form = await insert(s, "form.task_form", task_id=task, handler="harvest", version=1)
    first = await insert(s, "form.section", form_id=form, title="a", sort_order=0)
    later = await insert(s, "form.section", form_id=form, title="b", sort_order=1)
    questions: dict[str, UUID] = {}
    for section, field, label, kind, order in (
        (later, "note", "หมายเหตุ", "VARCHAR", 0),
        (first, "farm_id", "ฟาร์ม", "OPTION", 0),
        (first, "kg", "น้ำหนัก", "FLOAT", 1),
        (first, None, "ข้อความ", "VARCHAR", 2),
    ):
        q = await insert(
            s, "form.question", section_id=section, field_name=field, label=label,
            input_type=kind, sort_order=order,
        )  # fmt: skip
        assert q is not None
        questions[field or "none"] = q
    farmer = await _user(s, "f")
    processor = await _user(s, "p")
    nameless = await _user(s, "x")
    province = await insert(s, "ref.province_constant", province_name_th="ชุมพร")
    district = await insert(s, "ref.district_constant", province_id=province)
    subdistrict = await insert(s, "ref.subdistrict_constant", district_id=district)
    place = {"province_id": province, "district_id": district, "subdistrict_id": subdistrict}
    await insert(
        s, "agriculture.farmer", user_id=farmer, first_name="สมชาย", last_name="ใจดี", **place
    )
    await insert(s, "processing.processor", user_id=processor, first_name="แปร", last_name="รูป")
    await insert(s, "processing.processor", user_id=farmer, first_name="ไม่", last_name="ใช้")
    assert form and task
    w = World(form, task, questions, farmer, processor, nameless, {})
    w.responses["farmer"] = await _response(
        s, w, farmer, datetime(2026, 10, 5, 2), {"farm_id": "f-1", "kg": 12.5, "note": "เช้า"}
    )
    w.responses["processor"] = await _response(
        s, w, processor, datetime(2026, 10, 6, 2), {"farm_id": "f-2", "kg": "3", "note": None}
    )
    w.responses["nameless"] = await _response(s, w, nameless, None, {"kg": 1})
    return w


def _actor(perms: frozenset[str] = RESEARCHER_PERMS, user_id: UUID | None = None) -> Actor:
    return Actor(user_id=user_id or uuid4(), roles=frozenset(), perms=perms, channel="api")


# ---- by question --------------------------------------------------------------------


async def test_answers_by_question_in_question_order_with_names(
    db_session: AsyncSession,  # noqa: F811
    world: World,
) -> None:
    collector = await _user(db_session, "c")
    await insert(
        db_session, "processing.hub_collector", user_id=collector, first_name="รวบ", last_name="รวม"
    )
    await _response(db_session, world, collector, datetime(2026, 10, 7), {"kg": 4})

    rows = await responses.answers_by_question(db_session, _actor(), world.form_id)

    by_title = {r.question_title: [(a.full_name, a.answer) for a in r.answers] for r in rows}
    assert [r.question_title for r in rows] == ["ฟาร์ม", "น้ำหนัก", "ข้อความ", "หมายเหตุ"]
    # A farmer's name first, then processor, then hub collector, then the
    # username: everyone's answer shows (Kotlin dropped the last two). A null
    # answer is left out; a number reads as Jackson's asText. (Ordered by
    # response id, as Kotlin paged them.)
    assert sorted(by_title["ฟาร์ม"]) == sorted([("สมชาย ใจดี", "f-1"), ("แปร รูป", "f-2")])
    weights = dict(by_title["น้ำหนัก"])
    nameless = next(name for name in weights if name.startswith("x-"))  # no profile: username
    assert weights == {"สมชาย ใจดี": "12.5", "แปร รูป": "3", "รวบ รวม": "4", nameless: "1"}
    assert by_title["หมายเหตุ"] == [("สมชาย ใจดี", "เช้า")]
    assert by_title["ข้อความ"] == []  # a question with no field holds no answers


async def test_answers_by_question_are_paged_by_response(
    db_session: AsyncSession,  # noqa: F811
    world: World,
) -> None:
    one = await responses.answers_by_question(db_session, _actor(), world.form_id, size=1)
    assert sum(len(r.answers) for r in one) <= 3  # one response's answers at most
    with pytest.raises(Invalid):
        await responses.answers_by_question(db_session, _actor(), world.form_id, size=0)


async def test_unknown_form_and_no_permission(
    db_session: AsyncSession,  # noqa: F811
    world: World,
) -> None:
    with pytest.raises(FormNotFound):
        await responses.answers_by_question(db_session, _actor(), uuid4())
    with pytest.raises(FormNotFound):
        await responses.review(db_session, _actor(), uuid4())
    with pytest.raises(Forbidden):
        await responses.review(db_session, _actor(frozenset({"read:response:own"})), world.form_id)


# ---- review ---------------------------------------------------------------------------


async def test_review_is_newest_first_field_by_field(
    db_session: AsyncSession,  # noqa: F811
    world: World,
) -> None:
    rows = await responses.review(db_session, _actor(), world.form_id)

    assert [r.response_id for r in rows] == [
        world.responses["processor"],
        world.responses["farmer"],
        world.responses["nameless"],  # never sent: last
    ]
    assert [r.submitter for r in rows][2].startswith("x-")  # no profile: the username
    fields = {f.field_name: f for f in rows[1].fields}
    assert list(fields) == ["farm_id", "kg", "note"]  # each field once, question order
    assert (fields["kg"].value, fields["kg"].editable, fields["kg"].source) == ("12.5", True, None)
    assert (fields["farm_id"].editable, rows[0].fields[2].value) == (False, None)


async def test_review_says_where_the_chatbot_got_each_value(
    db_session: AsyncSession,  # noqa: F811
    world: World,
) -> None:
    # Two finished conversations; the one that last changed closest to when
    # the answer was sent is the one that sent it.
    for updated, source in (
        (datetime(2026, 10, 5, 2, 1), "llm_extracted"),
        (datetime(2026, 10, 1), "guided_flow"),
    ):
        conversation = await insert(
            db_session, "chat.conversation", user_id=world.farmer, task_id=world.task_id,
            task_form_id=world.form_id, status="completed", updated_at=updated,
        )  # fmt: skip
        await insert(
            db_session, "chat.conversation_answer", conversation_id=conversation,
            question_id=world.questions["kg"], source=source,
        )  # fmt: skip

    rows = await responses.review(db_session, _actor(), world.form_id)
    only_ai = await responses.review(db_session, _actor(), world.form_id, ai_only=True)

    farmer_row = next(r for r in rows if r.response_id == world.responses["farmer"])
    assert {f.field_name: f.source for f in farmer_row.fields} == {
        "farm_id": None,
        "kg": "llm_extracted",
        "note": None,
    }
    assert [r.response_id for r in only_ai] == [world.responses["farmer"]]


# ---- one answer ---------------------------------------------------------------------


async def test_one_answer_of_this_form_only(
    db_session: AsyncSession,  # noqa: F811
    world: World,
) -> None:
    found = await responses.one_response(
        db_session, _actor(), world.form_id, world.responses["farmer"]
    )
    assert (found.form_id, found.user_id, found.answer["kg"]) == (world.form_id, world.farmer, 12.5)
    with pytest.raises(ResponseNotFound):
        await responses.one_response(db_session, _actor(), world.form_id, uuid4())


# ---- corrections --------------------------------------------------------------------


async def test_a_correction_rewrites_one_field_and_logs_it(
    db_session: AsyncSession,  # noqa: F811
    world: World,
) -> None:
    researcher = await _user(db_session, "r")
    out = await responses.correct(
        db_session,
        _actor(user_id=researcher),
        world.form_id,
        world.responses["farmer"],
        "kg",
        CorrectionIn(value="13", reason="  "),
    )

    assert (out.field_name, out.value, out.editable) == ("kg", "13.0", True)
    answer = (
        await db_session.execute(
            text("SELECT answer FROM form.response WHERE response_id = :r"),
            {"r": world.responses["farmer"]},
        )
    ).scalar()
    assert answer == {"farm_id": "f-1", "kg": 13.0, "note": "เช้า"}  # a number stays a number
    log = (
        await db_session.execute(
            text(
                "SELECT field_name, old_value, new_value, corrected_by, reason"
                " FROM form.response_correction_log WHERE response_id = :r"
            ),
            {"r": world.responses["farmer"]},
        )
    ).one()
    assert tuple(log) == ("kg", "12.5", "13.0", researcher, None)  # a blank reason is none


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("nope", "x", "fieldName"),
        ("farm_id", "f-9", "fieldName"),  # OPTION can't be corrected here
        ("kg", "heavy", "value"),
    ],
)
async def test_a_correction_that_cant_apply_is_invalid(
    db_session: AsyncSession,  # noqa: F811
    world: World,
    field: str,
    value: str,
    error: str,
) -> None:
    with pytest.raises(Invalid) as e:
        await responses.correct(
            db_session, _actor(), world.form_id, world.responses["farmer"], field,
            CorrectionIn(value=value),
        )  # fmt: skip
    assert error in e.value.fields


async def test_a_correction_needs_update_permission_and_a_real_answer(
    db_session: AsyncSession,  # noqa: F811
    world: World,
) -> None:
    with pytest.raises(Forbidden):
        await responses.correct(
            db_session, _actor(frozenset({"read:response:all"})), world.form_id,
            world.responses["farmer"], "kg", CorrectionIn(value="1"),
        )  # fmt: skip
    with pytest.raises(ResponseNotFound):
        await responses.correct(
            db_session, _actor(), world.form_id, uuid4(), "kg", CorrectionIn(value="1")
        )
