"""Every 422 has one shape (docs-and-plan#269): request validation errors
that FastAPI raises before a service runs answer like `Invalid` does."""

from collections.abc import AsyncGenerator
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from src.exceptions import register_exception_handlers


class _Point(BaseModel):
    lat: float
    lng: float


class _Body(BaseModel):
    name: str
    province_id: UUID
    gis: list[_Point] = []


def _app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/things")
    async def create(body: _Body) -> dict[str, str]:
        return {"name": body.name}

    @app.get("/things/{thing_id}")
    async def read(thing_id: UUID, page: int = 0) -> dict[str, str]:
        return {"id": str(thing_id)}

    return app


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        yield c


async def test_body_errors_are_keyed_by_field(client: AsyncClient) -> None:
    response = await client.post("/things", json={"province_id": "not-a-uuid", "gis": [{"lat": 1}]})

    assert response.status_code == 422
    body = response.json()
    assert body["detail"] == "Invalid input"
    assert set(body["fields"]) == {"name", "province_id", "gis.0.lng"}
    assert body["fields"]["name"] == "Field required"


async def test_query_and_path_errors_have_the_same_shape(client: AsyncClient) -> None:
    response = await client.get("/things/not-a-uuid?page=abc")

    assert response.status_code == 422
    assert response.json()["detail"] == "Invalid input"
    assert set(response.json()["fields"]) == {"thing_id", "page"}


async def test_a_missing_body_is_reported_under_body(client: AsyncClient) -> None:
    response = await client.post("/things")

    assert response.status_code == 422
    assert set(response.json()["fields"]) == {"body"}


async def test_the_real_app_answers_the_same_shape() -> None:
    from src.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        response = await c.post("/api/v1/auth/login", json={"username": "0812345678"})

    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid input", "fields": {"password": "Field required"}}
