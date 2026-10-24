"""Check a submitted answer against its form: one validator for every door.

Two layers, applied per active question:

1. The question itself (ported from mobile-backend's answer_validator.go):
   required fields, the value's type for the input type, and OPTION values
   that are one of the question's choices.
2. The field's validation rule (field_validation_rule, V16), if it has one:
   number ranges, text length, no future dates, coordinates on the globe.
   The chatbot already applied these when asking one question at a time
   (conversation/validation.py); the mobile app never had them on the
   server. The rule's own errorMessage is the message.

Rules are read as stored: camelCase keys (maxLength, maxDate, validLatLng,
errorMessage), straight from load_form.

Returns field -> message (the first problem per field); empty means valid.
Answer keys with no matching question are ignored, as in Go.
"""

import math
from datetime import date, datetime
from typing import Any

from src import clock
from src.forms.schemas import FormOut, QuestionOut

REQUIRED = "จำเป็นต้องกรอก"
_DEFAULT_RULE_MESSAGE = "รูปแบบคำตอบไม่ถูกต้อง กรุณาลองใหม่อีกครั้ง"

# Go's strconv.ParseBool.
_TRUE = {"1", "t", "T", "TRUE", "true", "True"}
_FALSE = {"0", "f", "F", "FALSE", "false", "False"}


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return isinstance(value, list) and not value


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
    return None


def _date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _datetime(value: Any) -> datetime | None:
    """RFC 3339, or "YYYY-MM-DD[ T]HH:MM:SS", or a bare date (Go's layouts).
    Returned naive, in the zone it was written in."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=None)


def _points(value: Any) -> list[tuple[float, float]] | None:
    """[{"lat": .., "lng": ..}, ...] -> [(lat, lng), ...], or None."""
    if not isinstance(value, list) or not value:
        return None
    points = []
    for point in value:
        if not isinstance(point, dict):
            return None
        lat, lng = _number(point.get("lat")), _number(point.get("lng"))
        if lat is None or lng is None:
            return None
        points.append((lat, lng))
    return points


def _choice_key(value: Any) -> str:
    """How Go's %v printed an id: 5.0 -> "5", "A" -> "A"."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _type_error(question: QuestionOut, value: Any) -> str | None:
    kind = (question.input_type or "").upper()
    if kind == "INT" and _integer(value) is None:
        return "ต้องเป็นจำนวนเต็ม"
    if kind == "FLOAT" and _number(value) is None:
        return "ต้องเป็นตัวเลข"
    if kind == "DATE" and _date(value) is None:
        return "รูปแบบวันที่ไม่ถูกต้อง"
    if kind == "DATETIME" and _datetime(value) is None:
        return "รูปแบบวันที่เวลาไม่ถูกต้อง"
    if kind == "BOOLEAN" and _boolean(value) is None:
        return "ต้องเป็นค่า true/false"
    if kind == "OPTION":
        ids = {_choice_key(choice.id) for choice in question.choices or []}
        if _choice_key(value) not in ids:
            return "ตัวเลือกไม่ถูกต้อง"
    if kind == "GEODATA" and _points(value) is None:
        return "ข้อมูลพิกัดไม่ถูกต้อง"
    if kind in ("VARCHAR", "") and not isinstance(value, str):
        return "ต้องเป็นข้อความ"
    return None


def _breaks_rule(rule: dict[str, Any], value: Any) -> bool:
    """Whether a value that already has the right type breaks its rule."""
    kind = rule.get("type", "")
    if kind in ("FLOAT", "INT"):
        number = _number(value)
        if number is None:
            return False  # the type check reports it
        low, high = rule.get("min"), rule.get("max")
        return (low is not None and number < low) or (high is not None and number > high)
    if kind == "VARCHAR":
        limit = rule.get("maxLength")
        return isinstance(value, str) and limit is not None and len(value) > limit
    if kind == "DATE" and rule.get("maxDate") == "today":
        day = _date(value)
        return day is not None and day > clock.bangkok_today()
    if kind == "DATETIME" and rule.get("maxDate") == "today":
        moment = _datetime(value)
        return moment is not None and moment > clock.bangkok_now().replace(tzinfo=None)
    if kind == "GEODATA" and rule.get("validLatLng"):
        points = _points(value) or []
        return any(not (-90 <= lat <= 90 and -180 <= lng <= 180) for lat, lng in points)
    return False


def _question_error(question: QuestionOut, value: Any) -> str | None:
    if _is_empty(value):
        return REQUIRED if question.is_mandatory else None
    error = _type_error(question, value)
    if error is not None:
        return error
    rule = question.validation_rule
    if rule and _breaks_rule(rule, value):
        return str(rule.get("errorMessage") or _DEFAULT_RULE_MESSAGE)
    return None


def answer_errors(form: FormOut, answer: dict[str, Any]) -> dict[str, str]:
    """field -> message for every active question the answer gets wrong."""
    errors: dict[str, str] = {}
    for section in form.sections:
        if not section.is_active:
            continue
        for question in section.questions:
            field = question.field_name
            if not question.is_active or not field or field in errors:
                continue
            error = _question_error(question, answer.get(field))
            if error is not None:
                errors[field] = error
    return errors
