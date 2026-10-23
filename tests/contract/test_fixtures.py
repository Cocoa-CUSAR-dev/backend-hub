"""The replay helper itself, and the recorded fixtures' own consistency. No DB needed."""

import re
from datetime import date

import pytest

from tests.contract.fixtures import (
    SEED_DIR,
    SEED_ORDER,
    WEB,
    Case,
    all_fixtures,
    compare,
    kotlin_value,
    mask,
    seed_ids,
    without,
)

TODAY = date(2026, 10, 23)
SEEDED = "40000000-0000-4000-8000-000000000001"
NEW = "d1479b41-0fbf-4dd7-b773-52d6fbc63091"


def case(status: int = 200, response: object = None, **hub: object) -> Case:
    return Case(
        name="c",
        actor="researcher",
        status=status,
        method="GET",
        path="/x",
        response=response,
        hub=hub,
    )


def overridden(status: int, hub_status: int) -> Case:
    return Case(name="c", status=status, method="GET", path="/x", hub={"status": hub_status})


# ---- masking ------------------------------------------------------------------


def test_mask_keeps_seeded_ids_and_hides_new_ones() -> None:
    known = seed_ids()
    assert mask({"a": SEEDED, "b": NEW}, known, TODAY) == {"a": SEEDED, "b": "<new-id>"}


def test_mask_hides_times_from_the_last_day_only() -> None:
    value = ["2026-10-23T11:00:00", "2026-10-22T23:59:59Z", "2026-10-05T02:30:00", "2026-10-23"]
    assert mask(value, set(), TODAY) == ["<now>", "<now>", "2026-10-05T02:30:00", "2026-10-23"]


def test_mask_hides_tokens() -> None:
    assert mask("eyJhbGciOi.eyJzdWIi.c2ln", set(), TODAY) == "<token>"


# ---- Kotlin envelope ------------------------------------------------------------


def test_kotlin_value_unwraps_the_envelope() -> None:
    assert kotlin_value({"value": [1], "error": None}) == [1]
    assert kotlin_value({"value": None, "error": "Form not found"}) == {"error": "Form not found"}
    assert kotlin_value([1, 2]) == [1, 2]


# ---- comparing --------------------------------------------------------------------


def test_bad_input_400_becomes_422_and_error_bodies_are_not_compared() -> None:
    assert compare(case(400, {"error": "x"}), 422, {"detail": "y"}, TODAY, TODAY).ok
    assert not compare(case(400, {"error": "x"}), 400, {"detail": "y"}, TODAY, TODAY).ok


def test_status_override_wins() -> None:
    assert compare(overridden(500, 422), 422, {"detail": "y"}, TODAY, TODAY).ok


def test_body_must_match_after_masking() -> None:
    kotlin = case(200, {"formId": NEW, "title": "a"})
    assert compare(
        kotlin, 200, {"formId": "e0000000-0000-4000-8000-000000000000", "title": "a"}, TODAY, TODAY
    ).ok
    diff = compare(kotlin, 200, {"formId": NEW, "title": "b"}, TODAY, TODAY)
    assert not diff.ok
    assert "$.title: Kotlin 'a', hub 'b'" in diff.message


def test_ignore_drops_fields_the_hub_adds_on_purpose() -> None:
    kotlin = case(200, [{"taskId": SEEDED}], ignore=["*.formId"])
    assert compare(kotlin, 200, [{"taskId": SEEDED, "formId": SEEDED}], TODAY, TODAY).ok
    assert without({"a": {"b": 1, "c": 2}}, ["a.b"]) == {"a": {"c": 2}}


def test_unordered_compares_lists_as_sets() -> None:
    kotlin = case(200, [{"n": 1}, {"n": 2}], unordered=True)
    assert compare(kotlin, 200, [{"n": 2}, {"n": 1}], TODAY, TODAY).ok
    assert not compare(case(200, [{"n": 1}, {"n": 2}]), 200, [{"n": 2}, {"n": 1}], TODAY, TODAY).ok


# ---- the recorded files ---------------------------------------------------------------

FIXTURES = all_fixtures()


def test_every_kotlin_route_in_258_has_a_fixture() -> None:
    kids = {fx.kid for fx in FIXTURES}
    wanted = {f"K-{n:02d}" for n in [*range(7, 43), 45, 47, 49, 50, 51]} | {"K-CAT", "K-RUN"}
    assert wanted <= kids, sorted(wanted - kids)


@pytest.mark.parametrize("fx", FIXTURES, ids=lambda fx: fx.kid)
def test_fixture_is_consistent(fx: object) -> None:
    from tests.contract.fixtures import Fixture

    assert isinstance(fx, Fixture)
    assert set(fx.seeds) <= set(SEED_ORDER)
    assert fx.cases
    if fx.hub:
        # Every hub path parameter is filled from a Kotlin one.
        names = set(re.findall(r"{(\w+)}", fx.hub["path"]))
        mapped = set(fx.hub.get("params", {}))
        same = {n for c in fx.cases for n in c.params}
        assert names <= mapped | same, (fx.kid, names - mapped - same)


def test_seed_tables_are_found_in_load_order() -> None:
    from tests.contract.fixtures import seed_tables

    tables = seed_tables(["answers", "base"])
    assert tables[0] == "ref.province_constant"
    assert "form.response" in tables
    assert "collection.harvest" not in tables


def test_no_secrets_or_hosts_in_fixtures() -> None:
    for f in [*WEB.glob("*.json"), *SEED_DIR.glob("*.sql")]:
        content = f.read_text(encoding="utf-8")
        for bad in ("eyJ", "neon.tech", "neondb", "password_hash", "localhost:"):
            assert bad not in content, f"{f.name} contains {bad!r}"
