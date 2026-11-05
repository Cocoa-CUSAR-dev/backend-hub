# Cocoa Backend Hub

The one backend for the Cocoa Supply Chain Databank (Is Thai Cacao). It started as the LINE OA chatbot. The Kotlin `web-backend` (researcher web app) and the Go `mobile-backend` (mobile app) are now being merged into it, so that web, mobile and LINE share:

- one login
- one set of permission checks
- one function per action

**Before writing code, read [CONVENTIONS.md](./CONVENTIONS.md).** It says where every kind of code goes, and CI enforces it. [AGENTS.md](./AGENTS.md) is the short version for AI coding tools.

## Where the merge stands

| Part | Status |
|---|---|
| LINE chatbot (webhook, LIFF, conversation, reminders) | moved into `chat/`, `jobs/`, `line/client.py` and `reminders/`; all of its SQL is in `src/db/`, and the conventions check passes with only Go's "use last answer" client left in the baseline. LINE and LIFF list tasks with `tasks.service.list_for` as an `Actor`, a confirmed answer is saved with `tasks.answers.submit` (`chat/submit.py`, in its own session; it needs `create:response:own`, database V35), a form is read with `forms.detail.load_chat_form`, and the reminder schedule functions take an `Actor`. Still to do: the conversation functions in `chat/service.py` take no `actor` yet, and "use last answer" still asks Go (`src/tasks/client.py`) until `tasks.service.autofill` exists (docs-and-plan#264) |
| Foundation: one `config.py`, one `exceptions.py`, `actor.py`, `clock.py`, `src/db/` owners, conventions check in CI | done |
| Login + roles (from Kotlin + Go) | next |
| Mobile endpoints (from Go) / web endpoints (from Kotlin) | planned |

Until a part is merged, this service still calls the old backend for it: only `src/tasks/client.py` is left, calling Go for "use last answer". It is deleted when that part moves in.

## Tech stack

- **FastAPI**: async-native, and fits LINE's "acknowledge the webhook fast, process afterwards" pattern.
- **`line-bot-sdk`**: the official SDK, for webhook signature verification and LIFF.
- **LiteLLM**: provider-agnostic LLM calls. Switching models is a config string, not code (ADR 0004).
- **APScheduler**: reminder jobs, in-process. On serverless hosts they are triggered through `/internal/cron/*` instead.
- **SQLAlchemy (async) + asyncpg**: all queries live in `src/db/`, one file per DB schema.
- **Pydantic**: request/response schemas and LLM structured output.

There is **no Alembic.** The `database` repo (Flyway) is the only schema owner (ADR 0005).

## Project structure

See [CONVENTIONS.md §1](./CONVENTIONS.md#1-folder-map) for the target layout. In short:

```
src/
├── main.py  config.py  exceptions.py  database.py  actor.py  clock.py   # shared core
├── db/                  # ALL SQL, one file per schema (owners listed in each file)
├── llm/                 # the only LiteLLM caller
├── chat/                # LINE door + chatbot brain (webhook, LIFF, ui/, state machine, intent, parsing)
├── line/                # client.py (the one linebot importer) + service.py (outgoing messages); the rest is moving out
├── forms/  tasks/  diary/  sso/   # feature folders; tasks/client.py is the last call to Go
├── reminders/  notifications/
├── jobs/                # cron door (/internal/cron/*) and the scheduler
└── ...                  # feature folders arrive with the merge: auth/ me/ agriculture/ ...
scripts/check_conventions.py       # CI guard for CONVENTIONS.md
tests/                             # mirrors src/
liff/                              # the LIFF front end
```

## First-time setup

```bash
cp .env.sample .env   # fill in real values
python -m venv .venv && source .venv/Scripts/activate   # or .venv/bin/activate on macOS/Linux
pip install -e ".[dev]"
uvicorn src.main:app --reload
```

Every setting is in `src/config.py` and listed in `.env.sample`. SQLAlchemy needs a `postgresql+asyncpg://` URL, not the plain `postgresql://` that Go/Kotlin use.

**Windows with Smart App Control:** if importing SQLAlchemy fails with "An Application Control policy has blocked this file", reinstall it without its compiled speed-ups (`--no-cache-dir` matters: otherwise pip reuses the compiled build). In PowerShell, set the variable first with `$env:DISABLE_SQLALCHEMY_CEXT="1"`.

```bash
DISABLE_SQLALCHEMY_CEXT=1 pip install --no-cache-dir --force-reinstall --no-deps --no-binary sqlalchemy sqlalchemy
```

## Running tests and checks

```bash
pytest                                   # DB-backed tests also run with RUN_DB_TESTS=1
ruff check src tests scripts && mypy src
python scripts/check_conventions.py
```

`tests/conftest.py` sets dummy env vars before anything under `src` is imported, because `src/config.py` builds `Settings()` at import time. No real `.env` is needed for tests.

## CI

`.github/workflows/ci.yml` runs on every push:

1. lint (ruff + the conventions check)
2. type-check (mypy)
3. the full test suite, including DB tests against a Postgres service loaded with `tests/resources/schema.sql`
4. Docker build
