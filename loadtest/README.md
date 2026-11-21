# Load test (docs-and-plan#322)

A repeatable load test for backend-hub's busiest routes, with [Locust](https://locust.io).
Results and findings: [REPORT.md](REPORT.md).

Each simulated farmer logs in once, then, with 1–3 s between calls, does what the mobile
app does most:

| Weight | Route | When the app calls it |
|---:|---|---|
| 5 | `GET /api/v1/tasks` | the home page, every time it opens |
| 3 | `GET /api/v1/tasks/{id}/form` | opening a task |
| 2 | `GET /api/v1/ref/constants/{key}` | a form's dropdowns |
| 1 | `POST /api/v1/tasks/{id}/responses` | submitting an answer (validation + dissection, real DB writes) |
| 1 | `POST /line/webhook` | LINE delivering a message (signed, no events: the signature check and the ack LINE waits for, without the background chat work that would call LINE's real reply API) |

Login (`POST /api/v1/auth/login`) runs once per farmer when it starts.

## Never against production

The seed script creates a table, users, farms and a task, and the test writes answers.
Run it against your machine or a **Neon test branch** behind a non-production deployment.
`seed.py` refuses a database that isn't on this machine unless you pass `--not-production`.

## Run it

```bash
python -m venv .loadenv
.loadenv/Scripts/pip install -r loadtest/requirements.txt   # bin/ on macOS/Linux

# 1. backend-hub against a test database (here: local, see README's "Run locally")
uvicorn src.main:app --port 8766

# 2. seed 50 farmers and one open task (writes loadtest/users.json, gitignored: it holds passwords)
.loadenv/Scripts/python loadtest/seed.py --api http://127.0.0.1:8766 \
    --db postgresql://postgres@127.0.0.1:5432/hub_flow --users 50

# 3. run: 50 farmers, 5 more per second, 3 minutes; CSVs in loadtest/results/ (gitignored)
cd loadtest
LINE_CHANNEL_SECRET=<the target's channel secret> \
  ../.loadenv/Scripts/locust -f locustfile.py --host http://127.0.0.1:8766 \
  --headless -u 50 -r 5 -t 3m --only-summary --csv results/run

# 4. remove what the seed made
cd .. && .loadenv/Scripts/python loadtest/seed.py --db <same DSN> --cleanup
```

Without `LINE_CHANNEL_SECRET` the webhook task is skipped. Drop `--headless` to watch it
live at http://localhost:8089.

### Against a staging deployment (Vercel + Neon)

1. In Neon, create a branch from production (e.g. `loadtest`) and copy its connection string.
2. Deploy backend-hub as a Vercel preview with `DATABASE_URL` pointing at that branch.
   Previews are behind Vercel Authentication: add a
   [Protection Bypass for Automation](https://vercel.com/docs/deployment-protection/methods-to-bypass-deployment-protection/protection-bypass-automation)
   secret and pass it to Locust as a header (`x-vercel-protection-bypass`), or test with
   protection off for that preview only.
3. Seed with `--api <preview URL> --db <branch DSN, asyncpg form> --not-production`.
4. Run as above with `--host <preview URL>`.
5. Measure the cold start too: leave the preview idle 15+ minutes, then
   `curl -w '%{time_total}\n' -o /dev/null -s <preview URL>/health`.
6. Clean up, then delete the Neon branch.
