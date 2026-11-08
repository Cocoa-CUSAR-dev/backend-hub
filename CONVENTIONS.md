# Backend conventions

> Read this once before your first PR, and keep it open while reviewing.
> `AGENTS.md` points here, so AI coding tools follow the same rules.

This repo is becoming **the one backend**. The Kotlin `web-backend` and the Go `mobile-backend` are being moved into it, next to the LINE chatbot that already lives here. Until that is finished, some code still follows the old layout. Section 13 explains how the rules apply to it.

## The one rule behind all the others

**Every thing has one owner.** Each of these lives in exactly one place:

- every DB table
- every outside service
- every setting
- every action

Everyone else **calls** the owner. They never copy its work.

---

## 1. Folder map

```
src/
├── main.py              # app setup: middleware, routers, startup/shutdown
├── config.py            # ALL settings (env vars) -- the only place that reads env
├── exceptions.py        # ALL error types + how HTTP turns them into a reply
├── database.py          # engine, session, transaction()
├── actor.py             # Actor = who is calling (user_id, roles, perms, channel) + require()
├── clock.py             # ALL clock reads and timezone handling
│
├── db/                  # ALL SQL. One file per database schema.
│   ├── __init__.py      #   callers write `from src import db` -> db.tasks.latest_response(...)
│   ├── auth.py  forms.py  tasks.py  agriculture.py  collection.py  processing.py
│   ├── research.py  storage.py  ref.py  chat.py  notify.py
│   └── reports.py       #   read-only queries that join many schemas
│
├── line/client.py       # the ONLY file that imports linebot          (to be created)
├── llm/client.py        # the ONLY file that imports litellm
├── r2/client.py         # the ONLY file that imports boto3             (to be created)
│
├── <feature>/           # auth/ me/ forms/ tasks/ agriculture/ collection/ processing/
│   │                    # ref/ analytics/ reports/ diary/ reminders/ notifications/ admin/
│   ├── router.py        #   the door: HTTP in/out only
│   ├── service.py       #   the rules: permission + business logic
│   ├── schemas.py       #   pydantic models for input/output
│   └── (extra logic files when service.py gets long, e.g. tasks/answers.py)
│
├── chat/                # LINE door + chatbot brain (today: src/line/ + src/chat/)
└── jobs/                # cron door + scheduler   (today: src/internal/ + src/jobs/scheduler.py)
```

---

## 2. Who owns which table

**Before writing a query, look up the table here.** Add your function to *that* file. Never start a second one.

There is one file per database schema. Each file's docstring lists exactly what it owns, and `scripts/check_conventions.py` reads those lists.

| `src/db/` file | Owns these tables |
|---|---|
| `auth.py` | `auth.user_account`, `user_role`, `role`, `role_permission`, `permission`, `line_identity`, `line_link_code`, `refresh_token`, `sso_used_token` |
| `forms.py` | `form.task_form`, `section`, `question`, `question_visibility`, `field_validation_rule`, `handler_registry`, `field_dependency` |
| `tasks.py` | `form.task`, `assignment`, `task_recipient`, `task_recurrence`, `response`, `response_correction_log`, `diary_entry` |
| `agriculture.py` | `agriculture.*`: `farmer`, `farm`, `farmer_farm`, `plot`, `plot_breed`, every `farm_*` handler table |
| `collection.py` | `collection.harvest`, `harvest_collection`, `harvest_grade_detail` |
| `processing.py` | `processing.*`: `hub`, `hub_collector`, `processor`, `processing_station`, `processor_processing_station`, `processing_record`, `batch`, `fermentation_batch`, `drying_batch` |
| `research.py` | `research.researcher` |
| `storage.py` | `storage.file`, `storage.geo` |
| `ref.py` | `ref.*` (every `*_constant` table, through one generic getter) |
| `chat.py` | `chat.conversation`, `conversation_answer` |
| `notify.py` | `notify.reminder_schedule`, `reminder_recipient`, `reminder_log` |
| `reports.py` | none; only read-only joins for analytics and the Excel export |

The DB schema decides the file, not the API group. Hubs live in `processing.*`, so their queries go in `db/processing.py`, even though the API path is `/api/v1/collection/hubs`.

New table? It comes from a Flyway migration in the `database` repo (this repo never migrates). In the same PR that adds its first query, add the table to the owning file's `Owns:` list.

---

## 3. Layer rules

```
router.py / chat/ / jobs/   ->   service.py   ->   db/*  and  line/ llm/ r2/ clients   ->   config, database, exceptions, clock
```

| | Do | Never |
|---|---|---|
| **router.py** (and `chat/`, `jobs/`) | Get the `actor`, call **one** service function, return its result | Write SQL, check permissions, hold business `if`s, call `db.*` directly |
| **service.py** | Start with `require(actor, "<perm>")`. Call `db.*` and clients. Raise errors from `exceptions.py`. | Import `fastapi`, `Request`, `HTTPException`, `sqlalchemy`, `linebot`, `litellm` |
| **db/** | One `async def` per question, named by meaning. Return plain rows or dataclasses. | Check permissions, make business decisions, call services or clients |
| **clients** | Wrap the outside service: timeouts, retries, one shared instance | Read the DB, make business decisions |

**Service signature:** always `async def action(session: Session, actor: Actor, ...)`, with `actor` second, so every caller has to say who is acting. `Session` comes from `src/database.py`, so services never import sqlalchemy.

```python
from src import db
from src.actor import Actor, require
from src.database import Session
from src.exceptions import Forbidden, NotFound


async def list_plots_of_farm(session: Session, actor: Actor, farm_id: int) -> list[Plot]:
    require(actor, "read:farm:own")
    farm = await db.agriculture.get_farm(session, farm_id)
    if farm is None:
        raise NotFound("Farm not found")
    if farm.owner_id != actor.user_id and not actor.can("read:farm:all"):
        raise Forbidden("Not your farm")
    return await db.agriculture.plots_of_farm(session, farm_id)
```

Several writes that must succeed together go inside `async with transaction(session):` from `src/database.py`.

---

## 4. Same action -> same function

- If web, mobile, LINE, LIFF or cron need the same thing, they call **the same service function**.
- Never copy a service into `chat/` "because LINE is a bit different". Put the difference in the *reply* (JSON vs a Flex message), not in the logic.
- Need a variation? Add a parameter to the service. Don't write a second function.

---

## 5. Auth (`src/actor.py`)

- **Doors identify.** HTTP (cookie or Bearer), LINE (userId) and jobs (`Actor.system()`) all produce an `Actor`.
- **Services authorise.** `require(actor, perm)` sits *inside* the service, never only in the router.
- **Permission keys look like `verb:resource:scope`** (`read:farm:all`), not role names. `actor.perms` is the union over all of the user's roles, so one person can be a farmer and a processor.
- **Every permission key is listed below.** A new key comes with a Flyway migration in the `database` repo that grants it, and a new row here, in the same PR. A `require()` with an unlisted key means nobody can pass it.

  | Key | Lets you | Roles | Source |
  |---|---|---|---|
  | `read:profile:own` | read my own profile | admin, researcher, farmer, hub_collector, processor | baseline, V24, V32 |
  | `update:profile:own` | edit my profiles | admin, researcher, farmer, hub_collector, processor | baseline, V32 |
  | `create:user:any` | create researcher/admin accounts | admin | baseline |
  | `update:role:any` | grant/remove researcher, admin | admin | V32 |
  | `read:researcher:all` / `update:researcher:any` / `delete:researcher:any` | manage researchers | admin | baseline |
  | `read:researcher:own` / `create:researcher:own` | my researcher record | researcher | baseline |
  | `read:form:all` | every form | admin, researcher | baseline |
  | `create:form:all` / `update:form:all` | build and edit forms | researcher (update also admin) | baseline, V15 |
  | `read:form:assigned` | forms of tasks I can answer | farmer, hub_collector, processor | V10, V32 |
  | `read:task:all` | every task (researcher view) | researcher | V11 |
  | `read:response:all` / `update:response:all` | every answer, correct a field | researcher | V11, V28 |
  | `read:response:own` | my own answers | farmer, hub_collector, processor | V23, V32 |
  | `read:report:all` | dashboards, Excel export | admin, researcher | baseline |
  | `create:farm:own` / `read:farm:own` / `update:farm:own` | my farms, plots, farm photo | farmer | V33 |
  | `read:farm:all` | every farm and plot | researcher | V33 |

  No permission key is needed for things about your own account that a brand-new account (no roles yet) must be able to do: `GET /me`, adding a self-service role, changing your password.
- **One login.** `POST /api/v1/auth/login` returns tokens in the body **and** sets the cookie. There is no separate mobile login and web login.

---

## 6. Errors (`src/exceptions.py`)

- Raise the generic types: `Unauthorized` 401, `Forbidden` 403, `NotFound` 404, `Conflict` 409, `Invalid(fields=...)` 422, `Unavailable` 503.
- Add a named subclass only when a caller catches that specific case. For example, `LLMUnavailable` switches the chat into guided mode. Named subclasses live in `src/exceptions.py`, not in a feature folder.
- **HTTP body:** `{"detail": "..."}`. For `Invalid`, it also carries `"fields": {"<field>": "<message>"}`. `register_exception_handlers` in `src/exceptions.py` is the only HTTP translation.
- **LINE:** the chat layer turns the same errors into a Thai reply.
- Never `raise HTTPException` outside a `router.py`, and never send a LINE message from a service.

---

## 7. API paths

- Everything lives under `/api/v1/<group>/...`. The group is the feature folder name: `auth`, `me`, `forms`, `tasks`, `agriculture`, `collection`, `processing`, `ref`, `analytics`, `reports`, `admin`.
- `/me/...` is for things about the caller only. Everything else is a resource, and its list endpoint filters by permission. Don't add separate "my-x" and "all-x" URLs.
- Use plural nouns and kebab-case (`/processing/stations`, `/forms/reminder-recipients`). Path params are `{id}`.
- LINE stays outside `/api`: `/line/webhook`, `/line/liff/...`. Jobs are under `/internal/cron/...`.
- Every new endpoint gets a row on the **After** sheet of the API migration map in the same PR.
- **API docs** are served at `/docs` (Swagger UI) and `/redoc`, on every environment except production (`API_DOCS_ENABLED` forces it).
  - Every router sets `tags=["<group>"]`. A new group adds its tag and a one-line description to `src/api_docs.py`; a test fails if a tag is missing there.
  - Give each route a `summary=` and typed `response_model`, so the docs show what the endpoint returns.

---

## 8. Naming

| Thing | Style | Example |
|---|---|---|
| db functions | what you get, not how | `latest_response(session, user_id, task_id)`, `users_who_answered(...)` |
| service functions | the action | `submit`, `list_for`, `add_role`, `correct_field` |
| router functions | same name as the service they call | `submit` -> `service.submit` |
| schemas | `<Thing>In` / `<Thing>Out` | `AnswerIn`, `FarmOut` |
| permission keys | `verb:resource:scope` | `create:response:own`, `read:farm:all` |
| primary keys | `<entity>_id`, never `id` | `conversation_id`, `line_identity_id` |

---

## 9. Settings, time, logs

- **Settings:** read env vars **only** in `src/config.py`; everyone else imports `settings`. A new env var goes into `Settings`, `.env.sample` and the team's env registry sheet, all in the same PR.
- **Time:** read it **only** through `src/clock.py`:
  - `utc_now()` for anything stored.
  - `bangkok_now()` and `bangkok_today()` for "the user's time" and "today".
  - `to_bangkok()` for display.

  The DB stores UTC.
- **Logs:** use `logging.getLogger(__name__)` with structured fields. No `print`. Never log tokens, passwords or full answers.

---

## 10. Adding one API: the order

1. **`src/db/<schema>.py`:** find the owner file (§2) and add or reuse the query.
2. **Client:** only if the API talks to an outside service.
3. **`<feature>/service.py`:** one function, with `require(...)` first.
4. **`<feature>/schemas.py`:** input and output models.
5. **`<feature>/router.py`:** about 3 lines that call the service, plus `tags`, `summary` and `response_model` for the docs at `/docs`.
6. **Other doors** (`chat/`, `jobs/`): call the **same** service if they need it.
7. **Tests:** one each for db, service (allowed, forbidden, not found, invalid) and router.
8. **API migration map:** add the row to the After sheet.

---

## 11. Tests

- Mirror `src/` under `tests/`.
- Never call real LINE, LLM or R2 in tests. Use `respx` or the fakes for outside HTTP.
- Service tests must cover: allowed, forbidden, not found, invalid input.
- DB-backed tests run when `RUN_DB_TESTS=1` against `tests/resources/schema.sql` (CI does this).
- A bug fix comes with a test that fails without the fix.
- Required env vars are set in `tests/conftest.py` before any `src` import. `src/config.py` builds `Settings()` at import time.

---

## 12. Enforced by CI

| Check | Tool |
|---|---|
| SQL / `sqlalchemy` only in `src/db/` (+ `database.py`) | `scripts/check_conventions.py` |
| A `schema.table` in SQL appears only in the `src/db/` file that owns it, and no two files claim the same table | `scripts/check_conventions.py` |
| `linebot` / `litellm` / `boto3` / `httpx` only in their client file | `scripts/check_conventions.py` |
| `os.environ` only in `config.py`; clock reads only in `clock.py`; `HTTPException` only in `router.py` | `scripts/check_conventions.py` |
| Lint, format, types, tests | ruff · mypy · pytest |

Run it locally before pushing:

```bash
python scripts/check_conventions.py
```

---

## 13. Code from before these rules

Code written before these rules (mostly `src/line/`, `src/chat/` and `src/reminders/`) is listed in `scripts/conventions_baseline.txt`.

- **That list may only shrink.** A *new* violation fails CI. When you fix an old one, run `python scripts/check_conventions.py --prune`; CI also fails if a fixed line is still listed.
- **Never add lines to the baseline by hand.** If new code needs SQL, put the SQL in `src/db/`.
- **Touching a legacy file?** Move its SQL into the owner file if it's a small change. If not, leave it for the migration task that owns that file.

---

## 14. PR checklist (copy into the PR body)

```
- [ ] SQL only in src/db/, in the file that owns the table (§2)
- [ ] Service function starts with require(actor, ...) and is reused by every door that needs it
- [ ] Router has no logic -- just actor -> service -> return
- [ ] Errors from src/exceptions.py, no HTTPException outside routers
- [ ] New env var -> src/config.py + .env.sample + env registry
- [ ] New endpoint -> After sheet of the API migration map
- [ ] Tests: allowed / forbidden / not found / invalid
- [ ] python scripts/check_conventions.py passes
- [ ] รายการทดสอบสำหรับผู้รีวิว (ภาษาไทย): ...
```
