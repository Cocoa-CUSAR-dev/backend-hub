from fastapi import status

from src.exceptions import (
    AnswerInFlight,
    AppError,
    ConversationNotFound,
    FormNotFound,
    HandlerNotSupported,
    InvalidLineSignature,
    LLMUnavailable,
    UpstreamServiceError,
)


def test_service_exception_uses_class_default_detail() -> None:
    exc = AppError()
    assert exc.status_code == status.HTTP_400_BAD_REQUEST
    assert exc.detail == "Service error"
    assert str(exc) == "Service error"


def test_service_exception_accepts_override_detail() -> None:
    exc = AppError("something specific went wrong")
    assert exc.detail == "something specific went wrong"
    assert str(exc) == "something specific went wrong"


def test_upstream_service_error_defaults() -> None:
    exc = UpstreamServiceError()
    assert exc.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc.detail == "Upstream service error"


def test_upstream_service_error_is_a_service_exception() -> None:
    # main.py's app_error_handler is registered on AppError
    # itself -- every subclass must actually inherit it or it won't be caught.
    assert issubclass(UpstreamServiceError, AppError)


def test_conversation_not_found_defaults() -> None:
    exc = ConversationNotFound()
    assert exc.status_code == status.HTTP_404_NOT_FOUND
    assert exc.detail == "Conversation not found"
    assert isinstance(exc, AppError)


def test_conversation_not_found_accepts_override_detail() -> None:
    # handle_answer raises this with a specific reason (e.g. "no open
    # question to answer") -- the override must actually take effect.
    exc = ConversationNotFound("Conversation has no open question to answer")
    assert exc.detail == "Conversation has no open question to answer"


def test_answer_in_flight_defaults() -> None:
    exc = AnswerInFlight()
    assert exc.status_code == status.HTTP_409_CONFLICT
    assert "already being processed" in exc.detail


def test_form_not_found_defaults() -> None:
    exc = FormNotFound()
    assert exc.status_code == status.HTTP_404_NOT_FOUND
    assert exc.detail == "Form not found"
    assert isinstance(exc, AppError)


def test_invalid_line_signature_defaults() -> None:
    exc = InvalidLineSignature()
    assert exc.status_code == status.HTTP_400_BAD_REQUEST
    assert exc.detail == "Invalid LINE webhook signature"
    assert isinstance(exc, AppError)


def test_llm_unavailable_defaults() -> None:
    exc = LLMUnavailable()
    assert exc.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert exc.detail == "LLM provider unavailable"
    assert isinstance(exc, AppError)


def test_handler_not_supported_defaults() -> None:
    exc = HandlerNotSupported()
    assert exc.status_code == status.HTTP_501_NOT_IMPLEMENTED
    assert isinstance(exc, AppError)
    # Deliberately NOT an UpstreamServiceError -- see the class's own
    # docstring: Go is working as intended here, not broken/unreachable.
    assert not isinstance(exc, UpstreamServiceError)
