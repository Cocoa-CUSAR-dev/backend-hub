"""diary.service with the db layer and the LLM faked: who may read, paging,
which answered fields a day shows and in what order, and how the diary is
written (polish kept, polish refused, LLM down, nothing to write). The SQL is
in tests/db/, the routes against Kotlin's answers in tests/contract/ (K-07..09)."""

from datetime import date, datetime
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from src import clock, db
from src.actor import Actor
from src.db.forms import QuestionField
from src.db.tasks import DayAnswer, DiaryEntry
from src.diary import service, writing
from src.exceptions import DiaryNotAvailable, Forbidden, Invalid, LLMUnavailable, NotFound
from src.forms import detail
from src.llm import client as llm

SESSION: Any = object()
FARMER = frozenset({"read:response:own", "create:response:own"})
FORM, OTHER_FORM = uuid4(), uuid4()


def _actor(perms: frozenset[str] = FARMER, user_id: UUID | None = None) -> Actor:
    return Actor(user_id=user_id or uuid4(), roles=frozenset(), perms=perms, channel="api")


@pytest.fixture(autouse=True)
def fake(monkeypatch: pytest.MonkeyPatch) -> dict[str, AsyncMock]:
    fakes = {
        "submission_days": AsyncMock(return_value=[date(2026, 10, 12)]),
        "answers_between": AsyncMock(return_value=[]),
        "diary_entry": AsyncMock(return_value=None),
        "save_diary_entry": AsyncMock(),
        "question_fields": AsyncMock(
            return_value={
                (FORM, "farm_id"): QuestionField("ฟาร์ม", "OPTION"),
                (FORM, "is_dry"): QuestionField("แห้งหรือไม่", "BOOLEAN"),
                (FORM, "kg"): QuestionField(None, "FLOAT"),
                (FORM, "note"): QuestionField("หมายเหตุ", "VARCHAR"),
                (OTHER_FORM, "farm_id"): QuestionField("ฟาร์มที่ไป", "OPTION"),
            }
        ),
        "ref_label": AsyncMock(return_value="สวนโกโก้ชุมพร"),
        "complete": AsyncMock(return_value="ไปสวนโกโก้ชุมพร ได้ 12.5 กิโล"),
    }
    for name in ("submission_days", "answers_between", "diary_entry", "save_diary_entry"):
        monkeypatch.setattr(db.tasks, name, fakes[name])
    monkeypatch.setattr(db.forms, "question_fields", fakes["question_fields"])
    monkeypatch.setattr(detail, "ref_label", fakes["ref_label"])
    monkeypatch.setattr(llm, "complete", fakes["complete"])
    monkeypatch.setattr(service, "transaction", _no_transaction)
    return fakes


class _no_transaction:
    def __init__(self, _session: Any) -> None:
        pass

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_exc: object) -> None:
        return None


# ---- who may read --------------------------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        lambda a: service.submission_days(SESSION, a),
        lambda a: service.answers_on(SESSION, a, date(2026, 10, 5)),
        lambda a: service.diary_on(SESSION, a, date(2026, 10, 5)),
    ],
)
async def test_reading_needs_read_response_own(call: Any) -> None:
    with pytest.raises(Forbidden):
        await call(_actor(frozenset({"read:response:all"})))


async def test_generating_needs_create_response_own() -> None:
    with pytest.raises(Forbidden):
        await service.generate_for(SESSION, _actor(frozenset({"read:response:own"})))


# ---- history days -----------------------------------------------------------------


async def test_days_are_paged_by_page_and_size(fake: dict[str, AsyncMock]) -> None:
    me = uuid4()
    assert await service.submission_days(SESSION, _actor(user_id=me), page=2, size=10) == [
        date(2026, 10, 12)
    ]
    assert fake["submission_days"].await_args.args[1] == me
    assert fake["submission_days"].await_args.kwargs == {"limit": 10, "offset": 20}


@pytest.mark.parametrize(
    ("page", "size", "field"), [(-1, 50, "page"), (0, 0, "size"), (0, 501, "size")]
)
async def test_a_bad_page_is_invalid(page: int, size: int, field: str) -> None:
    with pytest.raises(Invalid) as e:
        await service.submission_days(SESSION, _actor(), page=page, size=size)
    assert field in e.value.fields


# ---- one day's answers --------------------------------------------------------------


async def test_a_day_is_the_bangkok_day_in_utc(fake: dict[str, AsyncMock]) -> None:
    await service.answers_on(SESSION, _actor(), date(2026, 10, 5))
    start, end = fake["answers_between"].await_args.args[2:]
    assert (start, end) == (datetime(2026, 10, 4, 17), datetime(2026, 10, 5, 17))


async def test_a_days_fields_follow_the_answers_then_the_questions(
    fake: dict[str, AsyncMock],
) -> None:
    fake["answers_between"].return_value = [
        DayAnswer(
            FORM, {"note": "เช้า", "kg": 12.5, "farm_id": "f-1", "task_id": "t", "is_dry": True}
        ),
        DayAnswer(OTHER_FORM, {"farm_id": "f-2"}),
        DayAnswer(FORM, {"note": "  ", "kg": None, "is_dry": False}),
        DayAnswer(None, {"note": "no form"}),
        DayAnswer(FORM, None),
    ]

    fields = await service.answers_on(SESSION, _actor(), date(2026, 10, 5))

    assert [(f.field_name, f.label, f.input_type, f.raw_value) for f in fields] == [
        ("farm_id", "ฟาร์ม", "OPTION", "f-1"),
        ("is_dry", "แห้งหรือไม่", "BOOLEAN", "true"),
        ("kg", "kg", "FLOAT", "12.5"),  # no label: the field name
        ("note", "หมายเหตุ", "VARCHAR", "เช้า"),
        ("farm_id", "ฟาร์มที่ไป", "OPTION", "f-2"),  # the other form's label
        ("is_dry", "แห้งหรือไม่", "BOOLEAN", "false"),
    ]  # task_id (no question), blank and null answers, and a form-less answer are left out
    assert set(fake["question_fields"].await_args.args[1]) == {FORM, OTHER_FORM}


# ---- the diary ----------------------------------------------------------------------


async def test_a_day_without_a_diary_is_not_found() -> None:
    with pytest.raises(NotFound):
        await service.diary_on(SESSION, _actor(), date(2026, 10, 6))


async def test_a_diary_has_kotlins_fields(fake: dict[str, AsyncMock]) -> None:
    me = uuid4()
    fake["diary_entry"].return_value = DiaryEntry(
        me, date(2026, 10, 5), "วันนี้", datetime(2026, 10, 5, 3, 5)
    )
    out = await service.diary_on(SESSION, _actor(user_id=me), date(2026, 10, 5))
    assert out.model_dump(by_alias=True) == {
        "userId": me,
        "entryDate": date(2026, 10, 5),
        "diaryText": "วันนี้",
        "updatedAt": datetime(2026, 10, 5, 3, 5),
    }


TEMPLATE = f"{writing.WHOLE_FARM_LINE}\n- ฟาร์ม: สวนโกโก้ชุมพร\n- แห้งหรือไม่: ใช่\n- kg: 12.5"


def _today(fake: dict[str, AsyncMock]) -> None:
    fake["answers_between"].return_value = [
        DayAnswer(FORM, {"farm_id": "f-1", "kg": 12.5, "is_dry": True})
    ]


async def test_generate_keeps_a_faithful_polish(
    fake: dict[str, AsyncMock], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(clock, "bangkok_today", lambda: date(2026, 10, 5))
    _today(fake)
    me = uuid4()

    text = await service.generate_for(SESSION, _actor(user_id=me))

    assert text == "ไปสวนโกโก้ชุมพร ได้ 12.5 กิโล"
    prompt = fake["complete"].await_args.args[0]
    # The template the LLM sees: names, not ids; ใช่ for true; the whole farm;
    # in the form's question order.
    assert prompt.endswith(TEMPLATE)
    assert fake["ref_label"].await_args.args[1:] == ("farm_id", "f-1")
    user_id, day, saved, _now = fake["save_diary_entry"].await_args.args[1:]
    assert (user_id, day, saved) == (me, date(2026, 10, 5), text)


async def test_generate_keeps_the_template_when_the_polish_changes_a_fact(
    fake: dict[str, AsyncMock],
) -> None:
    _today(fake)
    fake["complete"].return_value = "ไปสวนโกโก้ชุมพร ได้ 13 กิโล"

    text = await service.generate_for(SESSION, _actor())

    assert text == TEMPLATE
    assert fake["save_diary_entry"].await_args.args[3] == text


async def test_generate_keeps_the_template_when_the_llm_is_down(fake: dict[str, AsyncMock]) -> None:
    _today(fake)
    fake["complete"].side_effect = LLMUnavailable()

    text = await service.generate_for(SESSION, _actor())

    assert text.startswith(writing.WHOLE_FARM_LINE)
    fake["save_diary_entry"].assert_awaited_once()


async def test_an_option_whose_choice_is_gone_keeps_its_id(fake: dict[str, AsyncMock]) -> None:
    fake["answers_between"].return_value = [DayAnswer(FORM, {"farm_id": "gone-id"})]
    fake["ref_label"].return_value = None
    fake["complete"].side_effect = LLMUnavailable()

    assert "- ฟาร์ม: gone-id" in await service.generate_for(SESSION, _actor())


async def test_nothing_submitted_today_is_no_diary(fake: dict[str, AsyncMock]) -> None:
    with pytest.raises(DiaryNotAvailable):
        await service.generate_for(SESSION, _actor(), date(2026, 10, 6))
    fake["complete"].assert_not_awaited()
    fake["save_diary_entry"].assert_not_awaited()
