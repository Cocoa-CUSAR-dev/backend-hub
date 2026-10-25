"""src/forms/coercion.py: a researcher's correction as the JSON value stored,
by web-backend's AnswerCoercion rules (parse as Java did, keep the stored kind)."""

import pytest

from src.exceptions import Invalid
from src.forms import coercion


@pytest.mark.parametrize(
    ("kind", "raw", "existing", "stored"),
    [
        ("INT", " 42 ", 5, 42),  # a number stays a number
        ("INT", "+42", "5", "42"),  # text stays text
        ("FLOAT", "12.50", 3.5, 12.5),
        ("FLOAT", "12.50", "3.5", "12.50"),  # text keeps what was typed
        ("FLOAT", ".5", None, ".5"),
        ("BOOLEAN", "TRUE", False, True),
        ("BOOLEAN", "false", "true", "false"),
        ("DATE", "2026-10-07", "2026-10-06", "2026-10-07"),
        ("DATETIME", "2026-10-07 09:30", None, "2026-10-07T09:30:00"),
        ("DATETIME", "2026-10-07T09:30:15.500", None, "2026-10-07T09:30:15.5"),
        ("DATETIME", "2026-10-07T09:30:15.000", None, "2026-10-07T09:30:15"),
        ("VARCHAR", "  รถมอเตอร์ไซค์ ", "รถกระบะ", "รถมอเตอร์ไซค์"),
        ("varchar", "x", None, "x"),  # the type is matched case-insensitively
    ],
)
def test_a_valid_value_keeps_the_stored_kind(
    kind: str, raw: str, existing: object, stored: object
) -> None:
    assert coercion.coerce(kind, raw, existing) == stored


@pytest.mark.parametrize(
    ("kind", "raw", "message"),
    [
        ("INT", "4.5", "Value must be a whole number"),
        ("INT", "๕", "Value must be a whole number"),  # only ASCII digits, as Java
        ("INT", "1_000", "Value must be a whole number"),
        ("INT", "99999999999999999999", "Value must be a whole number"),  # past a long
        ("FLOAT", "nan", "Value must be a number"),
        ("FLOAT", "1e999", "Value must be a number"),
        ("BOOLEAN", "yes", "Value must be true or false"),
        ("DATE", "7 Oct", "Value must be a date like 2026-09-27"),
        ("DATE", "20261007", "Value must be a date like 2026-09-27"),
        ("DATE", "2026-02-30", "Value must be a date like 2026-09-27"),
        ("DATETIME", "2026-10-07", "Value must be a date and time like 2026-09-27T09:30:00"),
        ("DATETIME", "2026-10-07T25:00", "Value must be a date and time like 2026-09-27T09:30:00"),
        ("VARCHAR", "   ", "Corrected value must not be blank"),
        ("OPTION", "x", "Fields of type OPTION cannot be corrected here"),
    ],
)
def test_a_value_that_doesnt_fit_is_invalid(kind: str, raw: str, message: str) -> None:
    with pytest.raises(Invalid) as e:
        coercion.coerce(kind, raw, None)
    assert e.value.fields == {"value": message}


def test_only_simple_types_are_editable() -> None:
    assert [
        k for k in ("VARCHAR", "int", "OPTION", "GEODATA", None) if coercion.is_editable(k)
    ] == [
        "VARCHAR",
        "int",
    ]


def test_display_and_as_text_read_like_jackson() -> None:
    assert coercion.display(None) is None
    assert coercion.display({"lat": 13.5, "ชื่อ": "ก"}) == '{"lat":13.5,"ชื่อ":"ก"}'
    assert coercion.display([1, 2]) == "[1,2]"
    assert (coercion.display(True), coercion.display(5), coercion.display(2.5)) == (
        "true",
        "5",
        "2.5",
    )
    assert (coercion.as_text({"a": 1}), coercion.as_text(False), coercion.as_text("x")) == (
        "",
        "false",
        "x",
    )
