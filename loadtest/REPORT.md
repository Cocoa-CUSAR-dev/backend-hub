# Load test report: 2026-11-21

docs-and-plan#322. How to rerun: [README.md](README.md).

## Setup

- backend-hub `dev` (ee63452), one uvicorn worker, on a Windows laptop.
- Postgres 16 on the same laptop (`hub_flow`: the test schema plus the V10–V36 permission
  migrations). So there's **no network latency to the database**, unlike Vercel → Neon.
- 50 seeded farmers; one open task with a two-question form whose answers are dissected into
  a handler table.
- Locust 2.32.4: 1–3 s think time, 5 new farmers per second until all are running.

These numbers show how the code behaves under load, not what farmers see on Vercel + Neon.
That needs the staging run in the README, which needs a Neon test branch.

## Results

**50 farmers, 3 minutes: 4,278 requests, 0 failures, 23.9 req/s**

| Route | p50 | p95 | p99 | max |
|---|---:|---:|---:|---:|
| `GET /tasks` | 10 ms | 21 ms | 2,000 ms | 2,959 ms |
| `GET /tasks/{id}/form` | 11 | 24 | 2,200 | 3,111 |
| `GET /ref/constants/{key}` | 9 | 17 | 1,400 | 1,990 |
| `POST /tasks/{id}/responses` | 17 | 39 | 2,000 | 2,874 |
| `POST /line/webhook` | 6 | 14 | 430 | 655 |
| `POST /auth/login` | 2,100 | 3,600 | 4,200 | 4,185 |

**100 farmers, 2 minutes: 5,205 requests, 0 failures, 43.8 req/s**

| Route | p50 | p95 | p99 | max |
|---|---:|---:|---:|---:|
| `GET /tasks` | 10 ms | 880 ms | 3,600 ms | 7,948 ms |
| `GET /tasks/{id}/form` | 11 | 1,100 | 3,500 | 7,379 |
| `GET /ref/constants/{key}` | 9 | 580 | 3,400 | 5,571 |
| `POST /tasks/{id}/responses` | 18 | 1,300 | 3,400 | 3,930 |
| `POST /line/webhook` | 6 | 140 | 640 | 1,648 |
| `POST /auth/login` | 1,700 | 4,400 | 7,200 | 7,152 |

## Findings

### 1. bcrypt blocks the whole server while anyone logs in (high)

`src/auth/service.py` calls `bcrypt.checkpw` / `hashpw` straight from `async` functions. Each call
takes about 250 ms of CPU, and the event loop serves nobody else in that time. While farmers
are logging in (here: the ramp-up, 5 per second), every route stalls, not just login. That's
the 2–4 s p99 on routes whose median is 10 ms, and login's own 2–4 s.

**Checked:** running the bcrypt calls in a thread (`asyncio.to_thread`) and rerunning 100 farmers:

| 100 farmers | bcrypt on the event loop | `asyncio.to_thread` |
|---|---:|---:|
| all routes p95 / p99 | 1,400 / 3,600 ms | **42 / 250 ms** |
| login p95 | 4,400 ms | **280 ms** |
| slowest request | 7,948 ms | **662 ms** |
| failures | 0 | 0 |

On Vercel each instance is one process, so the same thing happens whenever several people log
in at once (e.g. a morning reminder): register, login, change password, and LINE link.

### 2. Cold start: 20 s for the first request after idle (high)

Production, `GET https://backend-hub-two.vercel.app/health` (no database):
- first request after idle: **20.0 s**
- the next three: **0.44 / 0.53 / 0.43 s**

A farmer who opens the app first thing waits 20 s before anything loads, and LINE gives up on
a webhook long before that (it retries, so messages arrive late). Likely the import time of
the whole app: pythainlp, the LLM and chat modules, every router. To look at:
- measure `python -X importtime -c "import src.main"`;
- load pythainlp and the LLM clients on first use, not at import;
- or keep an instance warm (a scheduled ping every few minutes, or Vercel's keep-warm options).

### 3. 0.45 s for a request that does nothing (question)

Warm `/health` takes about 0.45 s from Bangkok. That looks like the function runs far from
Thailand (Vercel's default region is US East). If Neon is in Singapore, every query would also
cross the Pacific. **To check:** the Vercel project's Function Region and the Neon region;
`sin1` (Singapore) would likely bring this down to tens of ms.

### Fine as it is

- No failures at 100 concurrent farmers on one local worker.
- With finding 1 fixed: p95 under 50 ms for reads and submits at ~45 req/s.
- The webhook acknowledges in ~7 ms. LINE's 1 s limit isn't at risk once the server isn't
  blocked.

## Not measured

- Vercel + Neon under load (needs a Neon test branch and a preview deployment; see the README).
- The LINE webhook's background chat work: it calls LINE's real reply API.
- The chatbot's LLM calls, the Excel export, and the researcher web routes.

## Follow-ups

1. Run bcrypt in a thread (finding 1): **done in backend-hub#32**. At 100 farmers, p95/p99 went from 1,400/3,600 ms to 36/240 ms.
2. Cut the cold start: finding 2.
3. Check the Vercel and Neon regions: finding 3.
4. Run this test on staging once a Neon test branch exists.
