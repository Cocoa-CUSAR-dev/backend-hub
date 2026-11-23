"""Logs every load-test run (docs-and-plan#333).

When Locust stops, this appends one line to `loadtest/runs/LOG.md` and writes
`loadtest/runs/<time>.md` with the numbers per route and the errors. Both are
committed, so the team can see every run that was made, not only the ones in
the report.

Name the run with LOADTEST_SCENARIO (e.g. "normal day") and add a note with
LOADTEST_NOTE (e.g. "after moving Vercel to sin1"). Secrets (passwords, the
Vercel bypass, the LINE secret) are never written.
"""

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from locust.runners import WorkerRunner

RUNS = Path(__file__).with_name("runs")
REPO = Path(__file__).resolve().parent.parent
LOG = RUNS / "LOG.md"
LOG_HEADER = (
    "# Load-test runs\n\n"
    "Every run of `locustfile.py`, newest last, written by `runlog.py`."
    " Each links to its per-route numbers.\n\n"
    "| When (UTC) | Scenario | Target | Commit | Users | Time | Requests | Failed | req/s"
    " | p50 | p95 | p99 | Details | Note |\n"
    "|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|\n"
)

_environment = None
_users: dict[str, int] = {}


def on_init(environment, **_kwargs) -> None:
    global _environment
    _environment = environment


def on_spawned(user_count: int, **_kwargs) -> None:
    runner = _environment.runner if _environment else None
    counts = getattr(runner, "user_classes_count", None) or {}
    _users.clear()
    _users.update({name: n for name, n in counts.items() if n} or {"users": user_count})


def on_quit(environment, **_kwargs) -> None:
    if isinstance(environment.runner, WorkerRunner):
        return  # the master logs the whole run
    stats = environment.stats
    total = stats.total
    now = datetime.now(UTC)
    started = datetime.fromtimestamp(total.start_time, UTC) if total.start_time else now
    seconds = max(0, round((now - started).total_seconds()))
    target = _target(environment.host)
    scenario = _cell(os.environ.get("LOADTEST_SCENARIO", "")) or "-"
    note = _cell(os.environ.get("LOADTEST_NOTE", ""))
    users = ", ".join(f"{n} {name}" for name, n in _users.items()) or "-"
    commit = _commit()

    RUNS.mkdir(exist_ok=True)
    detail = RUNS / f"{now:%Y-%m-%dT%H%M%SZ}.md"
    n = 2
    while detail.exists():  # two runs in the same second
        detail = RUNS / f"{now:%Y-%m-%dT%H%M%SZ}-{n}.md"
        n += 1
    detail.write_text(
        _detail(now, scenario, note, target, commit, users, seconds, stats), encoding="utf-8"
    )
    if not LOG.exists():
        LOG.write_text(LOG_HEADER, encoding="utf-8")
    row = [
        f"{now:%Y-%m-%d %H:%M}",
        scenario,
        target,
        commit,
        users,
        _duration(seconds),
        f"{total.num_requests:,}",
        f"{total.num_failures:,}",
        f"{total.total_rps:.1f}",
        _ms(total, 0.50),
        _ms(total, 0.95),
        _ms(total, 0.99),
        f"[{detail.stem}]({detail.name})",
        note,
    ]
    with LOG.open("a", encoding="utf-8") as log:
        log.write("| " + " | ".join(row) + " |\n")
    print(f"run logged: {LOG} and {detail}")


def _detail(now, scenario, note, target, commit, users, seconds, stats) -> str:
    total = stats.total
    lines = [
        f"# Load-test run {now:%Y-%m-%d %H:%M} UTC",
        "",
        f"- Scenario: {scenario}",
        f"- Target: {target}",
        f"- Commit: {commit}",
        f"- Users: {users}",
        f"- Time: {_duration(seconds)}",
        f"- Requests: {total.num_requests:,}, failed: {total.num_failures:,},"
        f" {total.total_rps:.1f} req/s",
    ]
    if note:
        lines.append(f"- Note: {note}")
    lines += [
        "",
        "| Route | Requests | Failed | p50 | p95 | p99 | max |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for entry in sorted(stats.entries.values(), key=lambda e: e.name):
        lines.append(
            f"| `{entry.name}` | {entry.num_requests:,} | {entry.num_failures:,}"
            f" | {_ms(entry, 0.50)} | {_ms(entry, 0.95)} | {_ms(entry, 0.99)}"
            f" | {round(entry.max_response_time or 0):,} |"
        )
    lines.append(
        f"| **All** | {total.num_requests:,} | {total.num_failures:,} | {_ms(total, 0.50)}"
        f" | {_ms(total, 0.95)} | {_ms(total, 0.99)} | {round(total.max_response_time or 0):,} |"
    )
    lines += ["", "Times in ms."]
    if stats.errors:
        lines += ["", "## Errors", "", "| Count | Route | Error |", "|---:|---|---|"]
        for error in sorted(stats.errors.values(), key=lambda e: -e.occurrences):
            lines.append(
                f"| {error.occurrences:,} | `{error.method} {error.name}`"
                f" | {_cell(str(error.error))[:200]} |"
            )
    return "\n".join(lines) + "\n"


def _ms(entry, percentile: float) -> str:
    if not entry.num_requests:
        return "-"
    return f"{round(entry.get_response_time_percentile(percentile)):,}"


def _duration(seconds: int) -> str:
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}m{seconds:02d}s" if minutes else f"{seconds}s"


def _target(host: str | None) -> str:
    # Scheme and host only: never a path or query that might carry a token.
    if not host:
        return "-"
    parts = urlsplit(host)
    return parts.netloc or host


def _cell(text: str) -> str:
    return " ".join(text.replace("|", "/").split())


def _commit() -> str:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=REPO,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no", "--", "src"],
            capture_output=True,
            text=True,
            cwd=REPO,
        ).stdout.strip()
        return f"{sha}{'+changes' if dirty else ''}"
    except (OSError, subprocess.CalledProcessError):
        return "-"
