"""Fail CI when new code breaks CONVENTIONS.md's "one owner" rules.

    python scripts/check_conventions.py          # check (CI runs this)
    python scripts/check_conventions.py --prune  # drop fixed lines from the baseline

Rules (CONVENTIONS.md §12):
  sql-outside-db    sqlalchemy imported outside src/db/ and src/database.py
  table-owner       a "schema.table" string outside the src/db/ file that owns it
  owner-conflict    two src/db/ files claim the same table
  lib-outside-client
                    linebot / litellm / boto3 / httpx imported outside their client file
  env-outside-config
                    os.environ / os.getenv outside src/config.py
  clock-outside     datetime.now / date.today / utcnow outside src/clock.py
  http-exception    HTTPException outside a router.py

Code written before these rules is listed in scripts/conventions_baseline.txt.
That list may only shrink: a new violation fails, and so does a baseline line
that no longer happens (run --prune so the file stays honest).
"""

import ast
import io
import re
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
DB = SRC / "db"
BASELINE = ROOT / "scripts" / "conventions_baseline.txt"

SCHEMAS = "auth|form|agriculture|collection|processing|research|storage|ref|chat|notify"
TABLE_RE = re.compile(rf"\b({SCHEMAS})\.([a-z_][a-z0-9_]*)\b")

# library -> the only files allowed to import it
CLIENT_ONLY = {
    "linebot": {"src/line/client.py"},
    "litellm": {"src/llm/client.py"},
    "boto3": {"src/r2/client.py"},
    "httpx": {"src/line/client.py", "src/llm/client.py", "src/r2/client.py"},
}
SQL_ALLOWED = {"src/database.py"}


def rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def owners() -> tuple[dict[str, str], list[str]]:
    """Table (or 'schema.*') -> owning src/db file, from each file's 'Owns:' list."""
    owned: dict[str, str] = {}
    problems: list[str] = []
    for f in sorted(DB.glob("*.py")):
        doc = ast.get_docstring(ast.parse(f.read_text(encoding="utf-8"))) or ""
        if "Owns:" not in doc:
            continue
        for line in doc.split("Owns:", 1)[1].splitlines():
            name = line.strip()
            if not re.fullmatch(rf"({SCHEMAS})\.([a-z_][a-z0-9_]*|\*)", name):
                if name:
                    break
                continue
            if name in owned and owned[name] != rel(f):
                problems.append(f"owner-conflict|{name}|{owned[name]} and {rel(f)}")
            owned[name] = rel(f)
    return owned, problems


def owner_of(table: str, owned: dict[str, str]) -> str | None:
    return owned.get(table) or owned.get(table.split(".")[0] + ".*")


def _string_literals(text: str) -> list[str]:
    """The text of every string literal. An f-string (its own tokens since
    Python 3.12) is its literal parts joined, with each {...} field replaced
    by a space, so the SQL around a placeholder still reads as SQL."""
    literals: list[str] = []
    stack: list[list[str]] = []  # open f-strings; they can nest inside {...}
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.STRING:
            literals.append(tok.string)
        elif tok.type == tokenize.FSTRING_START:
            stack.append([])
        elif tok.type == tokenize.FSTRING_MIDDLE and stack:
            stack[-1].append(tok.string)
        elif tok.type == tokenize.FSTRING_END and stack:
            literals.append(" ".join(stack.pop()))
    return literals


def string_tables(text: str) -> set[str]:
    """schema.table names that appear inside string literals (SQL, comments in
    docstrings excluded on purpose: docstrings are strings too, so a module that
    only *mentions* a table in prose is skipped by checking for SQL keywords).
    """
    found: set[str] = set()
    for s in _string_literals(text):
        if not re.search(r"\b(select|insert|update|delete|from|join|into)\b", s, re.I):
            continue
        found.update(f"{m.group(1)}.{m.group(2)}" for m in TABLE_RE.finditer(s))
    return found


def imports(tree: ast.AST) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module.split(".")[0])
    return out


def check_file(f: Path, owned: dict[str, str]) -> set[str]:
    path = rel(f)
    text = f.read_text(encoding="utf-8")
    tree = ast.parse(text)
    found: set[str] = set()
    libs = imports(tree)
    in_db = path.startswith("src/db/")

    if "sqlalchemy" in libs and not in_db and path not in SQL_ALLOWED:
        found.add(f"sql-outside-db|{path}|imports sqlalchemy")
    for lib, allowed in CLIENT_ONLY.items():
        if lib in libs and path not in allowed:
            found.add(f"lib-outside-client|{path}|imports {lib}")
    if path != "src/config.py" and re.search(r"\bos\.(environ|getenv)\b", text):
        found.add(f"env-outside-config|{path}|reads os.environ")
    if path != "src/clock.py" and re.search(
        r"\b(datetime\.now|date\.today|datetime\.utcnow)\(", text
    ):
        found.add(f"clock-outside|{path}|reads the clock")
    if not path.endswith("router.py") and "HTTPException" in text:
        found.add(f"http-exception|{path}|uses HTTPException")
    for table in string_tables(text):
        owner = owner_of(table, owned)
        if owner != path:
            found.add(f"table-owner|{path}|{table} (owner: {owner or 'nobody yet'})")
    return found


def main() -> int:
    owned, current = owners()
    found = set(current)
    for f in sorted(SRC.rglob("*.py")):
        found |= check_file(f, owned)

    baseline = set()
    if BASELINE.exists():
        baseline = {
            line.strip()
            for line in BASELINE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        }

    if "--prune" in sys.argv:
        kept = sorted(baseline & found)
        lines = BASELINE.read_text(encoding="utf-8").splitlines()
        header = [line for line in lines if line.startswith("#")]
        BASELINE.write_text("\n".join(header + kept) + "\n", encoding="utf-8", newline="\n")
        print(f"baseline: kept {len(kept)}, removed {len(baseline) - len(kept)}")
        return 0

    new = sorted(found - baseline)
    fixed = sorted(baseline - found)
    if new:
        print("New convention violations (see CONVENTIONS.md):")
        for v in new:
            print("  " + v.replace("|", "  "))
    if fixed:
        print("Fixed -- remove from scripts/conventions_baseline.txt (run with --prune):")
        for v in fixed:
            print("  " + v.replace("|", "  "))
    if new or fixed:
        return 1
    print(f"conventions OK ({len(baseline)} legacy items left in the baseline)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
