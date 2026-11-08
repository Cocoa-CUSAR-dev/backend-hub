"""Every error this service raises on purpose, in one place (CONVENTIONS.md §6).

Services raise these; each door translates them once:
- HTTP: `register_exception_handlers` below -> status code + JSON body.
- LINE chat: the conversation layer turns them into a Thai reply.

Raise a generic type (`NotFound("farm")`, `Forbidden(...)`) when it says
enough. Add a named subclass here only when a caller needs to catch that
specific case (e.g. `LLMUnavailable` switches the chat into guided mode).
Never raise FastAPI's own HTTP error class outside a router.
"""

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AppError(Exception):
    """Base for every deliberate error (not bugs)."""

    status_code = status.HTTP_400_BAD_REQUEST
    detail = "Service error"

    def __init__(self, detail: str | None = None) -> None:
        if detail:
            self.detail = detail
        super().__init__(self.detail)


# ---- generic types: prefer these ------------------------------------------


class Unauthorized(AppError):
    """Caller is not identified (no / bad token, signature or secret)."""

    status_code = status.HTTP_401_UNAUTHORIZED
    detail = "Not authenticated"


class Forbidden(AppError):
    """Caller is identified but lacks the permission."""

    status_code = status.HTTP_403_FORBIDDEN
    detail = "Not allowed"


class NotFound(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    detail = "Not found"


class Conflict(AppError):
    status_code = status.HTTP_409_CONFLICT
    detail = "Conflict"


class Invalid(AppError):
    """Input failed validation. `fields` maps field name -> message, so the
    web app can show the error next to the field and the chat can re-ask it.
    """

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    detail = "Invalid input"

    def __init__(self, detail: str | None = None, fields: dict[str, str] | None = None) -> None:
        super().__init__(detail)
        self.fields = fields or {}


class Unavailable(AppError):
    """An outside service (LLM, LINE, R2) failed or timed out."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    detail = "Service unavailable"


class UpstreamServiceError(AppError):
    """Kotlin or Go returned an unexpected response. Deleted once the old
    backends are switched off -- distinct from a 4xx caused by the user's
    own input: one of the backends is unreachable or broken.
    """

    status_code = status.HTTP_502_BAD_GATEWAY
    detail = "Upstream service error"


# ---- named cases that callers catch specifically -------------------------


class InvalidLineSignature(AppError):
    detail = "Invalid LINE webhook signature"


class InvalidLiffToken(AppError):
    detail = "Invalid or expired LIFF ID token"


class LineAccountNotLinked(Forbidden):
    """A verified LIFF session whose LINE user_id has no auth.line_identity
    row yet -- the LIFF screen itself has to show this (no reply token).
    """

    detail = "บัญชี LINE นี้ยังไม่ได้เชื่อมกับบัญชีในระบบ"


class InvalidCronSecret(Unauthorized):
    detail = "Invalid or missing cron secret"


class InvalidServiceKey(Unauthorized):
    detail = "Invalid or missing service key"


class ConversationNotFound(NotFound):
    detail = "Conversation not found"


class AnswerInFlight(Conflict):
    """Another message for this conversation is still being processed --
    see handle_answer's docstring for why it is dropped, not queued.
    """

    detail = "Another answer for this conversation is already being processed -- try again"


class FormNotFound(NotFound):
    detail = "Form not found"


class ResponseNotFound(NotFound):
    detail = "Response not found"


class ReminderNotFound(NotFound):
    detail = "Reminder schedule not found"


class InvalidReminderReference(Invalid):
    """A task, role or user id in the request doesn't exist."""

    detail = "The task, role or user in the request does not exist"


class DiaryNotAvailable(NotFound):
    """No submissions today for this user yet -- the backend is behaving
    correctly, there is just nothing to generate a diary from.
    """

    detail = "No diary could be generated -- no submissions found for today"


class LLMUnavailable(Unavailable):
    """Genuine LLM failure/timeout -- the signal that switches the chat from
    LLMConversation into GuidedFlow. NOT raised for partial extraction.
    """

    detail = "LLM provider unavailable"


class AnswerRejectedByDatabase(Invalid):
    """Writing an answer into its handler table broke a constraint the
    validator can't see: a NOT NULL column left empty ("missing"), a chosen
    id whose row is gone ("gone"), or a value Postgres can't read as the
    column's type ("format"). Go answered these with a raw 500; they are the
    farmer's input, so 422. The submit service catches it to name the field
    by its question label.
    """

    MESSAGES = {
        "missing": "จำเป็นต้องกรอก แต่ไม่ได้รับคำตอบ",
        "gone": "ค่าที่เลือกไม่มีอยู่ในระบบแล้ว กรุณาเลือกใหม่",
        "format": "มีคำตอบบางข้อที่รูปแบบไม่ถูกต้อง",
    }

    def __init__(self, reason: str, column: str | None = None) -> None:
        self.reason = reason
        self.column = column
        super().__init__(fields={column or "answer": self.MESSAGES[reason]})


class HandlerNotSupported(AppError):
    """This handler's save logic isn't built yet. Not an UpstreamServiceError:
    retrying can never succeed, so the farmer is told that honestly.
    """

    status_code = status.HTTP_501_NOT_IMPLEMENTED
    detail = "This form type can't be saved automatically yet"


# ---- HTTP translation (the only place) ------------------------------------


def _body(exc: AppError) -> dict[str, Any]:
    body: dict[str, Any] = {"detail": exc.detail}
    if isinstance(exc, Invalid) and exc.fields:
        body["fields"] = exc.fields
    return body


async def app_error_handler(request: Request | None, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=_body(exc))


# Where a request error sits: "body" / "query" / "path" / "header" / "cookie"
# is the part of the request, not the field, so it is left out of the name.
_REQUEST_PARTS = {"body", "query", "path", "header", "cookie"}


def _field_name(loc: tuple[int | str, ...]) -> str:
    """("body", "gis", 1, "lat") -> "gis.1.lat"; ("query", "date") -> "date"."""
    parts = [str(part) for part in loc]
    if len(parts) > 1 and parts[0] in _REQUEST_PARTS:
        parts = parts[1:]
    return ".".join(parts)


async def request_validation_handler(
    request: Request | None, exc: RequestValidationError
) -> JSONResponse:
    """A request that fails pydantic validation never reaches a service, so
    FastAPI would answer with its own list-shaped `detail`. Answer the same
    shape as `Invalid` instead, so every 422 reads the same: one message per
    field (the first one, if pydantic reports several for a field).
    """
    fields: dict[str, str] = {}
    for error in exc.errors():
        fields.setdefault(_field_name(tuple(error.get("loc", ()))), str(error.get("msg", "")))
    return await app_error_handler(request, Invalid(fields=fields))


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(
        RequestValidationError,
        request_validation_handler,  # type: ignore[arg-type]
    )
