"""What the chatbot still asks Go for: "use last answer" (US2-4, US2-5).

Both calls use Go's service routes, authenticated with a shared service key
rather than a farmer's JWT (the chatbot has no such cookie for anyone -- see
mobile-backend's internal/middleware/service_auth_middleware.go).

Everything else the chatbot used to ask Go for is in-process now: saving an
answer is src/chat/submit.py (-> tasks.answers.submit) and the task list is
tasks.service.list_for. These two go when `tasks.service.autofill` exists
(TASK-04, docs-and-plan#264): it replaces the last-answer read and the
sanitizer in one function, shared by the app and the chatbot.
"""

from typing import Any

import httpx

from src.config import settings
from src.exceptions import UpstreamServiceError
from src.request_id import REQUEST_ID_HEADER, get_request_id


async def fetch_last_answer(*, user_id: str, handler: str) -> dict[str, Any] | None:
    """GET Go's /service/tasks/last-answer (#100, US2-4) -- the raw answer
    JSON from this farmer's most recent COMPLETED submission for `handler`,
    or None if there isn't one yet. Deliberately unfiltered: task_id, stale
    parent IDs (farm_activity_id/harvest_id/batch_id), and OPTION values
    that may no longer resolve in the CURRENT form are all still in here --
    sanitize_for_autofill (in src/chat/reuse.py) is what strips those
    before anything gets offered to a farmer, not this function.
    """
    async with httpx.AsyncClient(base_url=settings.GO_BACKEND_URL, timeout=30.0) as client:
        response = await client.get(
            "/service/tasks/last-answer",
            params={"user_id": user_id, "handler": handler},
            headers={
                "X-Service-Key": settings.GO_SERVICE_KEY,
                REQUEST_ID_HEADER: get_request_id(),
            },
        )

    if response.status_code == 404:
        return None
    if response.status_code >= 400:
        raise UpstreamServiceError(
            f"Go backend returned {response.status_code} for last-answer lookup: "
            f"{_error_detail(response)}"
        )

    body = response.json()
    answer = body.get("answer")
    if not isinstance(answer, dict):
        raise UpstreamServiceError(
            f"Go's last-answer response had no usable 'answer' object: {body!r}"
        )
    return answer


async def fetch_sanitized_autofill(
    *, answer: dict[str, Any], questions: list[dict[str, Any]]
) -> dict[str, Any]:
    """POST Go's /service/autofill/sanitize (#105, US2-5) -- the ONE shared
    filtering implementation both chat and any remaining static form call,
    so the two channels can't drift apart on what's safe to prefill a
    farmer with from a past submission. `questions` must already be in
    mobile-backend's internal/validation.Question JSON shape
    (fieldName/inputType/choices, choices as {id, name}) --
    sanitize_for_autofill (in src/chat/reuse.py) builds that from this
    chatbot's own Question dataclasses; this function is just the HTTP call.
    """
    async with httpx.AsyncClient(base_url=settings.GO_BACKEND_URL, timeout=30.0) as client:
        response = await client.post(
            "/service/autofill/sanitize",
            json={"answer": answer, "questions": questions},
            headers={
                "X-Service-Key": settings.GO_SERVICE_KEY,
                REQUEST_ID_HEADER: get_request_id(),
            },
        )

    if response.status_code >= 400:
        raise UpstreamServiceError(
            f"Go backend returned {response.status_code} for autofill sanitize: "
            f"{_error_detail(response)}"
        )

    body = response.json()
    sanitized = body.get("answer")
    if not isinstance(sanitized, dict):
        raise UpstreamServiceError(
            f"Go's autofill-sanitize response had no usable 'answer' object: {body!r}"
        )
    return sanitized


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text
    detail = str(body.get("error", response.text))
    # Go's 400 validation failures put the per-field reasons in "details"
    # (e.g. which field/why) -- without them the message is just a generic
    # "validation failed" with no way to tell what to fix.
    details = body.get("details")
    if isinstance(details, list) and details:
        detail += f" -- {'; '.join(str(d) for d in details)}"
    return detail
