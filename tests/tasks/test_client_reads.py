"""src/tasks/client.py's Go calls, through real httpx with respx
(docs-and-plan#247): the last answer and the autofill sanitizer, the two the
chatbot still asks Go for. Saving an answer and the task list are in-process
(tests/chat/test_submit.py, tasks.service.list_for).
"""

import json

import pytest
import respx
from httpx import Response

from src.config import settings
from src.exceptions import UpstreamServiceError
from src.tasks.client import fetch_last_answer, fetch_sanitized_autofill

_GO = settings.GO_BACKEND_URL


@respx.mock
async def test_last_answer_rejects_a_body_without_an_answer_object() -> None:
    respx.get(f"{_GO}/service/tasks/last-answer").mock(
        return_value=Response(200, json={"answer": "not a dict"})
    )
    with pytest.raises(UpstreamServiceError, match="no usable 'answer'"):
        await fetch_last_answer(user_id="u-1", handler="notes")


@respx.mock
async def test_last_answer_error_includes_gos_validation_details() -> None:
    """Go's 400s put the per-field reasons in "details"; without them the
    log would only say "validation failed"."""
    respx.get(f"{_GO}/service/tasks/last-answer").mock(
        return_value=Response(
            400, json={"error": "validation failed", "details": ["handler: required"]}
        )
    )
    with pytest.raises(UpstreamServiceError, match="validation failed -- handler: required"):
        await fetch_last_answer(user_id="u-1", handler="")


@respx.mock
async def test_sanitize_sends_the_raw_answer_and_returns_the_filtered_one() -> None:
    route = respx.post(f"{_GO}/service/autofill/sanitize").mock(
        return_value=Response(200, json={"answer": {"notes": "ค่าเดิม"}})
    )

    result = await fetch_sanitized_autofill(answer={"notes": "ค่าเดิม", "gis": [1]}, questions=[])

    assert result == {"notes": "ค่าเดิม"}
    sent = json.loads(route.calls.last.request.content)
    assert sent == {"answer": {"notes": "ค่าเดิม", "gis": [1]}, "questions": []}


@respx.mock
async def test_sanitize_error_with_a_non_json_body_keeps_the_raw_text() -> None:
    respx.post(f"{_GO}/service/autofill/sanitize").mock(
        return_value=Response(502, text="<html>Bad Gateway</html>")
    )
    with pytest.raises(UpstreamServiceError, match="Bad Gateway"):
        await fetch_sanitized_autofill(answer={}, questions=[])


@respx.mock
async def test_sanitize_rejects_a_body_without_an_answer_object() -> None:
    respx.post(f"{_GO}/service/autofill/sanitize").mock(return_value=Response(200, json={}))
    with pytest.raises(UpstreamServiceError, match="no usable 'answer'"):
        await fetch_sanitized_autofill(answer={}, questions=[])
