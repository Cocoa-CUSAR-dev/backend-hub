# Contract tests: web-backend (Kotlin) → backend-hub

docs-and-plan#270. Every Kotlin route that #258 moves into this repo was called against a
real Kotlin, and its answers were saved here. When a route is ported, the same requests
are replayed against backend-hub, and the test fails if the hub answers differently.

```
tests/contract/
├── fixtures.py            # loading, masking, comparing (shared by recorder and replay)
├── record_web.py          # the recorder: run by hand, never in CI
├── test_web_contract.py   # the replay: one test per recorded case
├── test_fixtures.py       # tests of the helper and of the fixture files themselves
└── web/
    ├── K-07.json ... K-51.json, K-CAT.json, K-RUN.json   # one file per Kotlin route
    └── seed/              # reference.sql, base.sql, places.sql, forms.sql, answers.sql
```

`K-xx` is the row ID on the Before sheet of api-migration-map.xlsx, and `after` is the
row ID on the After sheet. `K-CAT` (`GET /forms/handlers/catalog`) and `K-RUN`
(`POST /service/recurrence/run`) were added to Kotlin after the sheet's snapshot, so they
have no K-row yet.

## When a replay runs

| Case | In CI |
|---|---|
| The fixture's `hub` route doesn't exist in `src.main.app` yet | skipped: "not ported yet" |
| `hub` is `null` (K-50, K-51: they become in-process calls) | skipped. Use the cases in the service tests instead |
| The route exists | **runs**, with `RUN_DB_TESTS=1` (CI sets it) |

A case runs inside one transaction that is rolled back at the end. It loads the seed
files the fixture names, signs in as the case's actor with a real hub token, sends the
request and compares the answer.

**A PR that ports a route** may see `add ... to tests/resources/schema.sql`: the seed
writes tables the CI schema doesn't have yet. docs-and-plan#271 added what `base` and
`forms` need. Still missing for the other seeds: `processing.hub`, `collection.harvest`,
`collection.harvest_grade_detail` (places), `form.diary_entry` and
`form.response_correction_log` (answers). Copy them from the `database` repo's migrations
into `schema.sql` in the PR that first needs them.

The seed loads only the files a fixture names, plus `reference.sql` first: the validation
rules and field dependencies the migrations insert, which a database built from
`schema.sql` doesn't have. The recorder resets Kotlin's database to the same files, so a
recorded answer never depends on rows the replay won't load.

## What counts as "the same"

The comparison ignores what the migration changes on purpose:

- **Envelope:** Kotlin answers `{"value": ..., "error": ...}`. The fixture stores only the value, and the hub answers without the wrapper.
- **400 → 422:** bad input is 422 in the hub (CONVENTIONS.md §6).
- **Error bodies:** never compared, only the status, because the hub's `{"detail"}` text differs from Kotlin's `error`.
- **Masking:** an id the seed and the request don't contain (a row the request created) becomes `<new-id>`, a time within a day of the recording becomes `<now>`, and a JWT becomes `<token>`.

Where the hub is meant to differ, set it on the case in the fixture file, and on the same
`C(...)` in `record_web.py` (`hub=...`) so a re-recording keeps it:

```json
"hub": {"status": 200, "ignore": ["*.formId"], "compare": "status", "unordered": true}
```

| Key | Meaning |
|---|---|
| `status` | The hub answers this status instead of Kotlin's (after 400 → 422) |
| `ignore` | Dotted paths left out of the comparison (`*` = every list item), e.g. a field the hub adds |
| `compare: "status"` | Only the status is compared |
| `unordered` | Lists are compared in any order. Use it where Kotlin never fixed an order |

The fixture's `hub` block says which route to call: `method`, `path`, extra `query`,
and `params` (hub name → Kotlin name). `form_of:taskId` sends the form id of that task,
because `/forms/{id}` takes a form id where Kotlin took a task id (docs-and-plan#271).

## Kotlin bugs found while recording

These cases record what Kotlin really answers, with a `hub` override for what the hub must answer:

| Route | Kotlin today | Why | Hub |
|---|---|---|---|
| K-42 raw-data export | always 400 | `XlsxService` pages 1000 rows, `PageRequest` allows at most 500 | 200 with the file |
| K-24 one answer | always 404 | joins `task_form.form_id = response.task_log_id`, but `task_log_id` is a task id | 200 |
| K-27..K-32 without `from`/`to` | 500 | missing required month | 422 |
| K-10 form list | any order | no `ORDER BY` | compared unordered |
| K-RUN | depends on today | opens rounds up to the current date | status only |

## Recording again

You need Docker, JDK 21 and the `database` and `web-backend` repos next to this one.

1. Start Postgres with PostGIS and apply every migration:

   ```bash
   docker run -d --name contract-pg -e POSTGRES_USER=contract -e POSTGRES_PASSWORD=contract \
     -e POSTGRES_DB=cocoa -p 55432:5432 postgis/postgis:17-3.5
   for f in $(ls ../database/migrations/V*.sql | sort -V); do
     docker exec -i contract-pg psql -q -v ON_ERROR_STOP=1 -U contract -d cocoa < "$f"; done
   ```

2. Build web-backend from a clean export, so a real `.env` (Neon) is never read:

   ```bash
   git -C ../web-backend archive origin/dev | tar -x -C /tmp/kotlin && cd /tmp/kotlin
   SPRING_DATASOURCE_URL=jdbc:postgresql://localhost:55432/cocoa SPRING_DATASOURCE_USERNAME=contract \
     SPRING_DATASOURCE_PASSWORD=contract ./gradlew generateJooq bootJar -x test
   ```

3. Run backend-hub as the chatbot on port 8000 (Kotlin calls it for reminders), with
   `DATABASE_URL=postgresql+asyncpg://contract:contract@localhost:55432/cocoa` and
   `CHATBOT_SERVICE_KEY=hub-chatbot-key`.

4. Run Kotlin with JDK 21 on port 3001, `-Duser.timezone=UTC`, against the same
   database. Set: `SERVER_CONTEXT_PATH=/api/v1`, `SERVER_PORT=3001`,
   `JWT_KEY=test-jwt-key-that-is-at-least-32-bytes-long`, `JWT_NAME=token`,
   `CHATBOT_SERVICE_KEY=contract-service-key`, `CHATBOT_SERVICE_URL=http://localhost:8000`,
   `CHATBOT_REMINDER_SERVICE_KEY=hub-chatbot-key`, `COOKIE_SECURE=false`, and the
   datasource and Hikari values from `application.properties`. On Windows, start it from
   PowerShell: Git Bash rewrites `/api/v1` into a Windows path.

5. Record:

   ```bash
   python tests/contract/record_web.py            # every route
   python tests/contract/record_web.py K-12 K-13  # some routes
   ```

   The recorder loads the seed, resets it before every request that writes, and signs
   each actor in with the same claims the hub issues (Kotlin accepts them). Override
   the defaults with `CONTRACT_KOTLIN_URL`, `CONTRACT_DB_URL`, `CONTRACT_JWT_KEY` and
   `CONTRACT_SERVICE_KEY`. Recording the export needs `openpyxl`
   (`pip install openpyxl`).

After re-recording, `git diff tests/contract/web/` shows what Kotlin changed.
