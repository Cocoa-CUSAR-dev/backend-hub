"""/api/v1/tasks routes: each one reaches the right service function with
the caller as actor, and answers with the right status and JSON shape. The
service itself is faked; its rules are tested in test_service.py."""

from datetime import date, datetime
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient

from src.auth import tokens
from src.exceptions import Forbidden, NotFound
from src.forms.schemas import FormOut
from src.tasks import service
from src.tasks.schemas import ResponseOut, TaskFormOut, TaskOut

USER_ID = uuid4()


def _auth(perms: frozenset[str] = frozenset({"read:form:assigned"})) -> dict[str, str]:
    token = tokens.issue_access_token(USER_ID, "0812345678", frozenset({"farmer"}), perms)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> dict[str, AsyncMock]:
    fakes = {name: AsyncMock() for name in ("list_for", "get", "get_form", "my_responses")}
    for name, mock in fakes.items():
        monkeypatch.setattr(service, name, mock)
    return fakes


async def test_routes_need_a_bearer_token(client: AsyncClient, fake: dict[str, AsyncMock]) -> None:
    response = await client.get("/api/v1/tasks")

    assert response.status_code == 401
    fake["list_for"].assert_not_awaited()


async def test_list_passes_date_and_paging_and_writes_go_times(
    client: AsyncClient, fake: dict[str, AsyncMock]
) -> None:
    task_id = uuid4()
    fake["list_for"].return_value = [
        TaskOut(
            task_id=task_id,
            title="เก็บเกี่ยว",
            description="",
            open_at=datetime(2026, 10, 1, 8),
            close_at=None,
            task_form_id=None,
            handler="harvest",
            is_multiple_submit=False,
            status="NOT_STARTED",
        )
    ]

    response = await client.get(
        "/api/v1/tasks?date=2026-10-01&page=2&page_size=20", headers=_auth()
    )

    assert response.status_code == 200
    assert response.json() == [
        {
            "task_id": str(task_id),
            "title": "เก็บเกี่ยว",
            "description": "",
            "open_at": "2026-10-01T08:00:00Z",
            "close_at": None,
            "task_form_id": None,
            "handler": "harvest",
            "is_multiple_submit": False,
            "status": "NOT_STARTED",
        }
    ]
    _, kwargs = fake["list_for"].await_args
    actor = fake["list_for"].await_args.args[1]
    assert (actor.user_id, actor.channel, "read:form:assigned" in actor.perms) == (
        USER_ID,
        "api",
        True,
    )
    assert kwargs == {"open_on": date(2026, 10, 1), "page": 2, "page_size": 20}


async def test_list_rejects_a_malformed_date(
    client: AsyncClient, fake: dict[str, AsyncMock]
) -> None:
    response = await client.get("/api/v1/tasks?date=01/10/2026", headers=_auth())

    assert response.status_code == 422
    fake["list_for"].assert_not_awaited()


async def test_get_returns_the_bare_answer(client: AsyncClient, fake: dict[str, AsyncMock]) -> None:
    task_id = uuid4()
    fake["get"].return_value = {"grade": "A", "weight_kg": 12.5}

    response = await client.get(f"/api/v1/tasks/{task_id}", headers=_auth())

    assert (response.status_code, response.json()) == (200, {"grade": "A", "weight_kg": 12.5})
    assert fake["get"].await_args.args[2] == task_id


async def test_service_errors_become_their_status(
    client: AsyncClient, fake: dict[str, AsyncMock]
) -> None:
    fake["get"].side_effect = NotFound("No answer to this task yet")
    fake["my_responses"].side_effect = Forbidden("Missing permission: read:response:own")

    missing = await client.get(f"/api/v1/tasks/{uuid4()}", headers=_auth())
    forbidden = await client.get(f"/api/v1/tasks/{uuid4()}/responses", headers=_auth())

    assert (missing.status_code, missing.json()) == (404, {"detail": "No answer to this task yet"})
    assert forbidden.status_code == 403


async def test_task_id_must_be_a_uuid(client: AsyncClient, fake: dict[str, AsyncMock]) -> None:
    response = await client.get("/api/v1/tasks/not-a-uuid/form", headers=_auth())

    assert response.status_code == 422
    fake["get_form"].assert_not_awaited()


async def test_form_keeps_kotlins_camel_case(
    client: AsyncClient, fake: dict[str, AsyncMock]
) -> None:
    form_id: UUID = uuid4()
    fake["get_form"].return_value = TaskFormOut(
        form=FormOut(
            form_id=form_id, title="F", description=None, is_multiple_submit=True, sections=[]
        ),
        version=3,
    )

    response = await client.get(f"/api/v1/tasks/{uuid4()}/form", headers=_auth())

    assert response.status_code == 200
    assert response.json() == {
        "form": {
            "formId": str(form_id),
            "title": "F",
            "description": None,
            "isMultipleSubmit": True,
            "sections": [],
        },
        "version": 3,
    }


async def test_responses_list(client: AsyncClient, fake: dict[str, AsyncMock]) -> None:
    response_id = uuid4()
    fake["my_responses"].return_value = [
        ResponseOut(
            response_id=response_id,
            task_form_id=None,
            submitted_at=datetime(2026, 10, 1, 2, 30),
            status="SUBMITTED",
            answer={"grade": "A"},
        )
    ]

    response = await client.get(f"/api/v1/tasks/{uuid4()}/responses", headers=_auth())

    assert response.status_code == 200
    assert response.json() == [
        {
            "response_id": str(response_id),
            "task_form_id": None,
            "submitted_at": "2026-10-01T02:30:00Z",
            "status": "SUBMITTED",
            "answer": {"grade": "A"},
        }
    ]
