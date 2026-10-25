"""What a researcher typed, as the JSON value that goes back into an answer
(US2-8), ported from web-backend's AnswerCoercion.

Two rules keep a correction from quietly corrupting the record: the text
must be valid for the question's input type (the same answer feeds the
diary and the handler tables), and the JSON kind already stored is kept
(some submissions store "5", others 5). OPTION and GEODATA can't be
corrected here: their value is an id or a structure that a text box can't
produce. Pure functions.
"""

import json
import math
import re
from datetime import date, datetime
from typing import Any

from src.exceptions import Invalid

EDITABLE_TYPES = frozenset({"VARCHAR", "INT", "FLOAT", "DATE", "DATETIME", "BOOLEAN"})

_LONG_MIN, _LONG_MAX = -(2**63), 2**63 - 1
_WHOLE = re.compile(r"[+-]?[0-9]+")
_DECIMAL = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_DATETIME = re.compile(
    r"([0-9]{4}-[0-9]{2}-[0-9]{2})T([0-9]{2}):([0-9]{2})(?::([0-9]{2})(?:\.([0-9]{1,9}))?)?"
)


def is_editable(input_type: str | None) -> bool:
    return (input_type or "").upper() in EDITABLE_TYPES


def _refuse(message: str) -> Invalid:
    return Invalid(message, fields={"value": message})


def coerce(input_type: str, raw: str, existing: Any) -> Any:
    """The new JSON value; Invalid when `raw` doesn't fit the type."""
    kind = input_type.upper()
    if kind not in EDITABLE_TYPES:
        raise _refuse(f"Fields of type {input_type} cannot be corrected here")
    text = raw.strip()
    if not text:
        raise _refuse("Corrected value must not be blank")
    stored_number = isinstance(existing, int | float) and not isinstance(existing, bool)
    if kind == "INT":
        if not _WHOLE.fullmatch(text) or not _LONG_MIN <= int(text) <= _LONG_MAX:
            raise _refuse("Value must be a whole number")
        return int(text) if stored_number else str(int(text))
    if kind == "FLOAT":
        if not _DECIMAL.fullmatch(text) or not math.isfinite(float(text)):
            raise _refuse("Value must be a number")
        return float(text) if stored_number else text
    if kind == "BOOLEAN":
        if text.lower() not in ("true", "false"):
            raise _refuse("Value must be true or false")
        flag = text.lower() == "true"
        return flag if isinstance(existing, bool) else str(flag).lower()
    if kind == "DATE":
        try:
            if not _DATE.fullmatch(text):
                raise ValueError(text)
            return date.fromisoformat(text).isoformat()
        except ValueError:
            raise _refuse("Value must be a date like 2026-09-27") from None
    if kind == "DATETIME":
        return _datetime(text.replace(" ", "T"))
    return text


def _datetime(text: str) -> str:
    """ISO with seconds always, and a fraction only when it isn't zero, at its
    shortest (Java's ISO_LOCAL_DATE_TIME): the mobile backend's validator
    wants "T15:04:05"."""
    match = _DATETIME.fullmatch(text)
    try:
        if match is None:
            raise ValueError(text)
        day, hour, minute, second, fraction = match.groups()
        datetime.fromisoformat(f"{day}T{hour}:{minute}:{second or '00'}")
    except ValueError:
        raise _refuse("Value must be a date and time like 2026-09-27T09:30:00") from None
    out = f"{day}T{hour}:{minute}:{second or '00'}"
    digits = (fraction or "").rstrip("0")
    return f"{out}.{digits}" if digits else out


def display(value: Any) -> str | None:
    """How a stored value reads on the review screen; None for no answer.
    An object or list is its compact JSON, as Jackson's toString() wrote it."""
    if value is None:
        return None
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return as_text(value)


def as_text(value: Any) -> str:
    """A scalar as Jackson's asText() wrote it ("" for an object or a list)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict | list) or value is None:
        return ""
    return str(value)
