"""scripts/check_conventions.py -- the CI guard for CONVENTIONS.md."""

import importlib.util
from pathlib import Path

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
