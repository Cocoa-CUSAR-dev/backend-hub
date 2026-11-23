# Load test (docs-and-plan#322, #333)

A repeatable load test for backend-hub's web and mobile routes, with [Locust](https://locust.io).
Results and findings: [REPORT.md](REPORT.md). Every run: [runs/LOG.md](runs/LOG.md).

## Who it plays

Each simulated user logs in once (`POST /api/v1/auth/login`), then, with think time between
calls, does what its app does most. The weights say how often each call comes up.

**Farmer** (mobile, 1–3 s between calls)

| Weight | Route | When the app calls it |
|---:|---|---|
| 5 | `GET /api/v1/tasks` | the home page, every time it opens |
| 3 | `GET /api/v1/tasks/{id}/form` | opening a task |
| 2 | `GET /api/v1/ref/constants/{key}` | a form's dropdowns |
| 2 | `GET /api/v1/me` | the profile, and on start |
| 1 | `POST /api/v1/tasks/{id}/responses` | submitting an answer (validation + dissection, real DB writes) |
| 1 | `PUT /api/v1/tasks/{id}/responses/{response_id}` | editing the answer it just sent |
| 1 | `GET /api/v1/me/history` | the diary |
| 1 | `GET /api/v1/agriculture/farms`, `/plots` | the ฟาร์ม tab |
| 1 | `POST /api/v1/auth/refresh` | the hourly token refresh (1 time in 5) |
| 1 | `POST /line/webhook` | LINE delivering a message (signed, no events: the signature check and the ack LINE waits for, without the background chat work that would call LINE's real reply API). Only with `LINE_CHANNEL_SECRET` set. |

**HubCollector** (mobile): hubs and harvests (3 each), tasks (2).

**Processor** (mobile): stations and batches (3 each), tasks (2).

**Researcher** (web, 3–8 s between clicks)

| Weight | Route | Page |
|---:|---|---|
| 3 | `GET /api/v1/forms?include=stats` | task list |
| 2 | `GET /api/v1/forms/{id}`, `/forms/{id}/responses` | one form and its answers |
| 1 | `GET /api/v1/forms/handlers/catalog` | Data Map |
| 3 | `GET /api/v1/analytics/harvest/time-series/{mode}` | dashboard chart, last 12 months |
| 2 | `GET /api/v1/analytics/harvest/summary/{mode}` | dashboard totals |
| 1 | `POST /api/v1/analytics/harvest/spatial/...` | map dashboard (no drawn area) |
| 1 | `GET /api/v1/analytics/users/...` | new accounts chart |
| 1 | `GET /api/v1/reports/raw-data` | Excel export |

**Admin** (web): the researcher list.

How many of each: weights Farmer 10, HubCollector 1, Processor 1, Researcher 1, Admin 1, so
65 users are 50 farmers and 5 of each other kind. Change them with `FARMER_WEIGHT`,
`HUB_COLLECTOR_WEIGHT`, `PROCESSOR_WEIGHT`, `RESEARCHER_WEIGHT` and `ADMIN_WEIGHT`, or run only
some kinds by naming them after the options (`locust ... Farmer Researcher`).

## Every run is logged

When Locust stops, `runlog.py` adds a line to [runs/LOG.md](runs/LOG.md) (time, scenario,
target host, commit, users, requests, failures, p50/p95/p99) and writes `runs/<time>.md` with
the numbers per route and the errors. **Commit both after every run**, including failed or
aborted ones, so the team can see every test that was made, not only the ones in the report.

Name the run with `LOADTEST_SCENARIO` and add a note with `LOADTEST_NOTE`. Only the target's
host name is written: never passwords, tokens or the bypass secret.

## Never against production

The seed script creates a table, accounts, farms, hubs, stations, harvests and a task, and the
test writes answers. Run it against your machine or a **Neon test branch** behind a
non-production deployment. `seed.py` refuses a database that isn't on this machine unless you
pass `--not-production`.

## Run it

```bash
python -m venv .loadenv
.loadenv/Scripts/pip install -r loadtest/requirements.txt   # bin/ on macOS/Linux

# 1. backend-hub against a test database (here: local, see README's "Run locally")
uvicorn src.main:app --port 8766

# 2. seed (writes loadtest/users.json, gitignored: it holds passwords).
#    Defaults: 50 farmers with 30 answers each, 5 hub collectors, 5 processors,
#    10 researchers, 1 admin, 24 harvests per farm over the last 12 months.
.loadenv/Scripts/python loadtest/seed.py --api http://127.0.0.1:8766 \
    --db postgresql://postgres@127.0.0.1:5432/hub_flow

# 3. run: 65 users, 5 more per second, 3 minutes; CSVs in loadtest/results/ (gitignored)
cd loadtest
LOADTEST_SCENARIO="normal day" LINE_CHANNEL_SECRET=<the target's channel secret> \
  ../.loadenv/Scripts/locust -f locustfile.py --host http://127.0.0.1:8766 \
  --headless -u 65 -r 5 -t 3m --only-summary --csv results/run

# 4. commit the run log (runs/LOG.md and the new runs/*.md)

# 5. remove what the seed made
cd .. && .loadenv/Scripts/python loadtest/seed.py --db <same DSN> --cleanup
```

Without `LINE_CHANNEL_SECRET` the webhook task is skipped. Drop `--headless` to watch it
live at http://localhost:8089.

On Windows, Locust needs gevent's compiled files. If Windows says "An Application Control
policy has blocked this file", that machine can't run Locust: use another machine or WSL.

### Scenarios (docs-and-plan#334)

| Scenario | Users | Command changes |
|---|---|---|
| smoke | 5, 1 min | `-u 5 -r 5 -t 1m` |
| normal day | 50 farmers + 5 researchers (+ 5 of each other kind), 10 min | `-u 65 -r 5 -t 10m` |
| morning rush | 100 farmers logging in fast | `Farmer` only, `-u 100 -r 20 -t 5m` (seed `--users 100`) |
| researcher heavy | 50 farmers + 10 researchers | `RESEARCHER_WEIGHT=2`, `-u 70 -r 5 -t 10m` |

Set `LOADTEST_SCENARIO` to the scenario's name for each run.

### Against a staging deployment (Vercel + Neon)

1. In Neon, create a branch from production (e.g. `loadtest`) and copy its connection string.
2. Deploy backend-hub as a Vercel preview with `DATABASE_URL` pointing at that branch.
   Previews are behind Vercel Authentication: add a
   [Protection Bypass for Automation](https://vercel.com/docs/deployment-protection/methods-to-bypass-deployment-protection/protection-bypass-automation)
   secret and set it as `VERCEL_BYPASS` when running Locust (it sends the
   `x-vercel-protection-bypass` header), or test with protection off for that preview only.
3. Seed with `--api <preview URL> --db <branch DSN, asyncpg form> --not-production`, with
   `VERCEL_BYPASS` set.
4. Run as above with `--host <preview URL>`.
5. Measure the cold start too: leave the preview idle 15+ minutes, then
   `curl -w '%{time_total}\n' -o /dev/null -s <preview URL>/health`.
6. Clean up, then delete the Neon branch.
