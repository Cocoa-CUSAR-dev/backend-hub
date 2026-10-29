"""/api/v1/me/history and /me/diaries: each route reaches its service
function with the caller and the day, and a day that isn't a date is 422.
The service is faked; its rules are in test_service.py."""

from datetime import date, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import AsyncClient

from src.auth import tokens
from src.diary import service
from src.diary.schemas import AnswerFieldOut, DiaryOut

USER_ID = uuid4()


def _auth() -> dict[str, str]:
    token = tokens.issue_access_token(
        USER_ID, "farmer", frozenset({"farmer"}), frozenset({"read:response:own"})
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> dict[str, AsyncMock]:
    fakes = {
        "submission_days": AsyncMock(return_value=[date(2026, 10, 12)]),
        "answers_on": AsyncMock(
            return_value=[
                AnswerFieldOut(
                    field_name="farm_id", label="ฟาร์ม", input_type="OPTION", raw_value="f"
                )
            ]
        ),
        "diary_on": AsyncMock(
            return_value=DiaryOut(
                user_id=USER_ID,
                entry_date=date(2026, 10, 5),
                diary_text="วันนี้",
                updated_at=datetime(2026, 10, 5, 3, 5),
            )
        ),
    }
    for name, mock in fakes.items():
        monkeypatch.setattr(service, name, mock)
    return fakes


async def test_routes_need_a_bearer_token(client: AsyncClient, fake: dict[str, AsyncMock]) -> None:
    for path in (
        "/api/v1/me/history",
        "/api/v1/me/history/2026-10-05",
        "/api/v1/me/diaries/2026-10-05",
    ):
        assert (await client.get(path)).status_code == 401
    fake["submission_days"].assert_not_awaited()


async def test_history_days_take_page_and_size(
    client: AsyncClient, fake: dict[str, AsyncMock]
) -> None:
    r = await client.get("/api/v1/me/history?page=1&size=5", headers=_auth())

    assert (r.status_code, r.json()) == (200, ["2026-10-12"])
    assert fake["submission_days"].await_args.kwargs == {"page": 1, "size": 5}
    assert fake["submission_days"].await_args.args[1].user_id == USER_ID


async def test_one_days_answers_and_diary_in_kotlins_keys(
    client: AsyncClient, fake: dict[str, AsyncMock]
) -> None:
    answers = await client.get("/api/v1/me/history/2026-10-05", headers=_auth())
    diary = await client.get("/api/v1/me/diaries/2026-10-05", headers=_auth())

    assert answers.json() == [
        {"fieldName": "farm_id", "label": "ฟาร์ม", "inputType": "OPTION", "rawValue": "f"}
    ]
    assert diary.json() == {
        "userId": str(USER_ID),
        "entryDate": "2026-10-05",
        "diaryText": "วันนี้",
        "updatedAt": "2026-10-05T03:05:00",
    }
    assert fake["answers_on"].await_args.args[2] == date(2026, 10, 5)


@pytest.mark.parametrize("path", ["/api/v1/me/history/2026-13-01", "/api/v1/me/diaries/yesterday"])
async def test_a_day_that_isnt_a_date_is_422(
    client: AsyncClient, fake: dict[str, AsyncMock], path: str
) -> None:
    assert (await client.get(path, headers=_auth())).status_code == 422
    fake["answers_on"].assert_not_awaited()
    fake["diary_on"].assert_not_awaited()
