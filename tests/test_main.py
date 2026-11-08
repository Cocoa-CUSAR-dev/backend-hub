from httpx import AsyncClient

from src.exceptions import AppError, Invalid, UpstreamServiceError, app_error_handler


async def test_service_exception_handler_returns_its_status_code_and_detail() -> None:
    response = await app_error_handler(None, AppError("something specific"))

    assert response.status_code == 400
    assert response.body == b'{"detail":"something specific"}'


async def test_service_exception_handler_uses_the_subclasss_own_status_code() -> None:
    response = await app_error_handler(None, UpstreamServiceError())

    assert response.status_code == 502


async def test_invalid_includes_field_errors() -> None:
    response = await app_error_handler(None, Invalid(fields={"yield_kg": "must be >= 0"}))

    assert response.status_code == 422
    assert response.body == b'{"detail":"Invalid input","fields":{"yield_kg":"must be >= 0"}}'


async def test_dev_conversation_router_is_registered_in_local_environment(
    client: AsyncClient,
) -> None:
    # tests/conftest.py never sets ENVIRONMENT, so Settings() defaults to
    # Environment.LOCAL (not deployed) -- main.py only skips registering
    # this router when settings.ENVIRONMENT.is_deployed is True.
    response = await client.get("/conversation/test/ui")
    assert response.status_code == 200


async def test_openapi_is_exposed_when_not_deployed(client: AsyncClient) -> None:
    response = await client.get("/openapi.json")
    assert response.status_code == 200


async def test_swagger_and_redoc_pages_are_served(client: AsyncClient) -> None:
    assert (await client.get("/docs")).status_code == 200
    assert (await client.get("/redoc")).status_code == 200


async def test_openapi_documents_every_tag_it_uses(client: AsyncClient) -> None:
    spec = (await client.get("/openapi.json")).json()
    assert spec["info"]["title"] == "Cocoa Backend Hub API"
    declared = {t["name"] for t in spec["tags"]}
    used = {
        tag for path in spec["paths"].values() for op in path.values() for tag in op.get("tags", [])
    }
    # A router with a tag missing from src/api_docs.py shows up unexplained.
    assert used <= declared
    assert all(op.get("tags") for path in spec["paths"].values() for op in path.values())


# ----------------------------------------------------------------------
# request_id_middleware (X-2e)
# ----------------------------------------------------------------------


async def test_request_id_is_generated_when_not_provided(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.headers.get("X-Request-Id")


async def test_request_id_is_echoed_back_when_provided(client: AsyncClient) -> None:
    response = await client.get("/health", headers={"X-Request-Id": "test-request-id-123"})
    assert response.headers.get("X-Request-Id") == "test-request-id-123"
