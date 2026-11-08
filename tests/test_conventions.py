"""scripts/check_conventions.py -- the CI guard for CONVENTIONS.md."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "check_conventions", ROOT / "scripts" / "check_conventions.py"
)
assert _spec and _spec.loader
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)


def test_repo_passes_its_own_conventions_check() -> None:
    assert check.main() == 0


def test_every_db_module_owns_distinct_tables() -> None:
    owned, conflicts = check.owners()
    assert conflicts == []
    assert owned["form.response"] == "src/db/tasks.py"
    assert check.owner_of("ref.farm_constant", owned) == "src/db/ref.py"


def test_only_sql_strings_count_as_table_use() -> None:
    sql = 'q = "SELECT * FROM form.response WHERE task_id = :t"\n'
    prose = 'doc = "the answer is stored in form.response"\n'
    attribute = "x = form.response\n"
    assert check.string_tables(sql) == {"form.response"}
    assert check.string_tables(prose) == set()
    assert check.string_tables(attribute) == set()


def test_reports_may_read_any_table_but_never_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned, _ = check.owners()
    monkeypatch.setattr(check, "rel", lambda _p: check.REPORTS)  # judge probes as reports.py
    reads = tmp_path / "reads.py"
    reads.write_text(
        'q = "SELECT f.farm_name FROM agriculture.farm f JOIN collection.harvest h ON true"\n',
        encoding="utf-8",
    )
    writes = tmp_path / "writes.py"
    writes.write_text('q = "UPDATE agriculture.farm SET farm_name = :n"\n', encoding="utf-8")

    assert check.check_file(reads, owned) == set()
    assert check.check_file(writes, owned) == {
        "reports-writes|src/db/reports.py|writes SQL (reports.py only reads)"
    }


def test_sql_inside_f_strings_is_checked_too() -> None:
    # Python 3.12+ tokenizes f-strings separately; the literal parts around
    # {placeholders} must still be read as SQL (docs-and-plan#268).
    simple = 'x = 1\nq = f"SELECT farm_id FROM agriculture.farm WHERE id = {x}"\n'
    split = 'q = f"SELECT {cols} FROM {table} JOIN form.task t ON t.task_id = {tid}"\n'
    nested = 'q = f"SELECT 1 FROM form.response WHERE a = {f"{b}"}"\n'
    prose = 'doc = f"the answer lives in form.response for {who}"\n'
    assert check.string_tables(simple) == {"agriculture.farm"}
    assert check.string_tables(split) == {"form.task"}
    assert check.string_tables(nested) == {"form.response"}
    assert check.string_tables(prose) == set()


def test_an_f_string_query_outside_its_owner_is_flagged() -> None:
    owned, _ = check.owners()
    module = check.ROOT / "src" / "db" / "_f_string_probe.py"
    module.write_text('cols = "farm_id"\nq = f"SELECT {cols} FROM agriculture.farm"\n')
    try:
        found = check.check_file(module, owned)
    finally:
        module.unlink()

    assert (
        "table-owner|src/db/_f_string_probe.py|agriculture.farm (owner: src/db/agriculture.py)"
        in found
    )
