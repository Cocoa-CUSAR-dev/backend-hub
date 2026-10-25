"""/api/v1/forms routes: each reaches the right service function with the
caller as actor and its query, and answers camelCase JSON. The service is
faked; its rules are in test_service.py."""

from datetime import datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import AsyncClient

from src.auth import tokens
from src.exceptions import NotFound
from src.forms import service
from src.forms.schemas import FormOut, FormSummaryOut, FormTaskOut

USER_ID = uuid4()


def _auth() -> dict[str, str]:
    token = tokens.issue_access_token(
        USER_ID, "researcher", frozenset({"researcher"}), frozenset({"read:form:all"})
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> dict[str, AsyncMock]:
    fakes = {name: AsyncMock() for name in ("list_for", "get")}
    for name, mock in fakes.items():
        monkeypatch.setattr(service, name, mock)
    return fakes


async def test_routes_need_a_bearer_token(client: AsyncClient, fake: dict[str, AsyncMock]) -> None:
    assert (await client.get("/api/v1/forms")).status_code == 401
    assert (await client.get(f"/api/v1/forms/{uuid4()}")).status_code == 401
    fake["list_for"].assert_not_awaited()


async def test_list_writes_kotlins_keys(client: AsyncClient, fake: dict[str, AsyncMock]) -> None:
    form_id, task_id = uuid4(), uuid4()
    fake["list_for"].return_value = [
        FormSummaryOut(
            form_id=form_id,
            task_id=task_id,
            title="t",
            content="",
            handler="harvest",
            is_multiple_submit=True,
            description=None,
            created_at=datetime(2026, 8, 25, 3),
        )
    ]

    r = await client.get("/api/v1/forms", headers=_auth())

    assert r.status_code == 200
    assert r.json() == [
        {
            "formId": str(form_id),
            "taskId": str(task_id),
            "title": "t",
            "content": "",
            "handler": "harvest",
            "isMultipleSubmit": True,
            "description": None,
            "createdAt": "2026-08-25T03:00:00",
        }
    ]
    assert fake["list_for"].await_args.kwargs == {"include_stats": False, "page": 0, "size": 50}


async def test_include_stats_passes_paging(client: AsyncClient, fake: dict[str, AsyncMock]) -> None:
    fake["list_for"].return_value = [
        FormTaskOut(
            form_id=None,
            task_id=uuid4(),
            title="t",
            description="",
            task_type="TASK",
            open_at=datetime(2026, 10, 10),
            close_at=None,
        )
    ]

    r = await client.get(
        "/api/v1/forms", params={"include": "stats", "page": 1, "size": 2}, headers=_auth()
    )

    assert r.status_code == 200
    assert r.json()[0]["formId"] is None and r.json()[0]["openAt"] == "2026-10-10T00:00:00"
    assert fake["list_for"].await_args.kwargs == {"include_stats": True, "page": 1, "size": 2}


async def test_unknown_include_is_422(client: AsyncClient, fake: dict[str, AsyncMock]) -> None:
    r = await client.get("/api/v1/forms", params={"include": "everything"}, headers=_auth())

    assert r.status_code == 422
    fake["list_for"].assert_not_awaited()


async def test_get_passes_the_form_id(client: AsyncClient, fake: dict[str, AsyncMock]) -> None:
    form_id = uuid4()
    fake["get"].return_value = FormOut(
        form_id=form_id, title="t", description=None, is_multiple_submit=False, sections=[]
    )

    r = await client.get(f"/api/v1/forms/{form_id}", headers=_auth())

    assert r.status_code == 200
    assert r.json()["formId"] == str(form_id)
    args = fake["get"].await_args
    assert args.args[2] == form_id and args.kwargs == {"include_stats": False}


async def test_get_not_found_is_404(client: AsyncClient, fake: dict[str, AsyncMock]) -> None:
    fake["get"].side_effect = NotFound("Form not found")

    r = await client.get(f"/api/v1/forms/{uuid4()}", params={"include": "stats"}, headers=_auth())

    assert r.status_code == 404
    assert fake["get"].await_args.kwargs == {"include_stats": True}
