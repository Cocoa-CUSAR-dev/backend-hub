# AGENTS.md

**Read [CONVENTIONS.md](./CONVENTIONS.md) first.** It is the rulebook for this repo: where code goes, who owns which table, and how auth and errors work. CI enforces it with `scripts/check_conventions.py`. This file adds only the short, repo-specific points an agent tends to miss.

## Version matrix

| Package | Version | Notes |
|---|---|---|
| Python | >=3.12 | |
| fastapi | >=0.115 | |
| line-bot-sdk | >=3.14 | v3 API (`linebot.v3.*`) — not the old v2 `LineBotApi` |
| litellm | >=1.52 | |
| apscheduler | >=3.10 | `AsyncIOScheduler` |
| sqlalchemy | >=2.0 | async engine, `Mapped`/`mapped_column` style |
| asyncpg | >=0.30 | driver — URLs use `postgresql+asyncpg://` |

## Do

- New queries go in `src/db/<schema>.py`, in the file whose `Owns:` list has the table (CONVENTIONS.md §2).
- New settings go in `src/config.py` (one `Settings` class). New errors go in `src/exceptions.py`. Clock reads go through `src/clock.py`.
- Every service function takes `(session, actor, ...)` and calls `require(actor, "<perm>")` first (`src/actor.py`).
- Name every table's PK `<entity>_id` (e.g. `conversation_id`), never a generic `id`. `src/db/models.py`'s `uuid_pk()` helps.
- LLM calls go through `src/llm/client.py` only (`LLM_MODEL` / `LLM_API_KEY`), so the provider stays swappable.
- Scheduled jobs: register in `src/jobs/scheduler.py` and keep the body a plain async function. That way it stays testable without APScheduler, and callable from `/internal/cron/*` on serverless hosts.
- Required env vars for tests are set in `tests/conftest.py` before any `src` import, because `Settings()` is built at import time.
- Run before pushing: `ruff check src tests scripts`, `mypy src`, `pytest`, `python scripts/check_conventions.py`.

## Don't

- **No Alembic or any other migration tool.** Schema changes are Flyway migrations in the `database` repo (ADR 0005).
- **Don't add lines to `scripts/conventions_baseline.txt`.** It lists legacy code and may only shrink (`--prune` after fixing one).
- **Don't add new HTTP calls to Kotlin or Go.** The one that remains (`src/tasks/client.py`, Go's "use last answer") is replaced by a direct service call when `tasks.service.autofill` exists.
- **Don't treat "some required slots still missing" as a trigger for `GuidedFlow`.** Only a genuine LLM failure or timeout (`LLMUnavailable`) does. The switch is two-way and can happen mid-conversation (`on_llm_recovered`).
- **Don't use `BackgroundTasks` for anything where a silently lost task would hurt.** That belongs in the scheduler.
- **Don't hand-roll LINE webhook signature verification.** `src/line/client.py`'s `parse_events` uses `WebhookParser`.

## Review checklist

- [ ] New table or column → has a Flyway migration in the `database` repo, and the table is in a `src/db/` `Owns:` list
- [ ] A test that imports `src.main` without `tests/conftest.py`'s env vars set
- [ ] A `def` (sync) route or dependency doing real I/O → should be `async def`
- [ ] Provider-specific LLM code outside `src/llm`
- [ ] The PR checklist from CONVENTIONS.md §14 is in the PR body
