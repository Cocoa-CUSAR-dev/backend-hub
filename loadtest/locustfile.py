"""Load test for backend-hub's web and mobile routes (docs-and-plan#322, #333).

Five kinds of simulated users, each logging in once, then, with think time
between calls, doing what its app does most:

- Farmer (mobile): the task list, a task's form, dropdowns, submitting and
  editing answers, /me, the diary, farms and plots, refreshing the login;
  and the LINE webhook (signed, no events) when LINE_CHANNEL_SECRET is set.
- HubCollector (mobile): hubs, harvests, tasks.
- Processor (mobile): stations, batches, tasks.
- Researcher (web): the form list with stats, one form and its responses, the
  handlers catalog, the dashboard charts (harvest by month and in total, the
  map versions, new accounts) and the Excel export.
- Admin (web): the researcher list.

How many of each: the weights below, changed with FARMER_WEIGHT,
HUB_COLLECTOR_WEIGHT, PROCESSOR_WEIGHT, RESEARCHER_WEIGHT and ADMIN_WEIGHT
(e.g. RESEARCHER_WEIGHT=4 for the "researcher heavy" scenario), or pick classes
on the command line (`locust ... Farmer Researcher`).

Every run is logged (runlog.py): a line in loadtest/runs/LOG.md and a file
with the per-route numbers. Name the run with LOADTEST_SCENARIO and add a note
with LOADTEST_NOTE.

Needs `loadtest/users.json` from seed.py. Against a Vercel preview behind
Vercel Authentication, set VERCEL_BYPASS to the Protection Bypass for
Automation secret. See loadtest/README.md for how to run it.
"""

import base64
import hashlib
import hmac
import json
import os
import random
from datetime import date
from itertools import cycle
from pathlib import Path

import runlog
from locust import HttpUser, between, events, task

SEEDED = json.loads(Path(__file__).with_name("users.json").read_text(encoding="utf-8"))
TASK_ID = SEEDED["task_id"]
FORM_ID = SEEDED.get("form_id")
LINE_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
BYPASS = os.environ.get("VERCEL_BYPASS", "")
WEBHOOK_BODY = json.dumps({"destination": "Uloadtest", "events": []})

# The dashboard's default range: the last 12 months.
_today = date.today()
LAST_YEAR = {"from": f"{_today.year - 1}-{_today.month:02d}", "to": f"{_today:%Y-%m}"}


def _weight(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _accounts(group: str) -> cycle:
    # users.json from before #333 had only farmers, under "users".
    accounts = SEEDED.get(group) or (SEEDED.get("users", []) if group == "farmers" else [])
    return cycle(accounts) if accounts else cycle([None])


_farmers = _accounts("farmers")
_collectors = _accounts("hub_collectors")
_processors = _accounts("processors")
_researchers = _accounts("researchers")
_admins = _accounts("admins")

events.init.add_listener(runlog.on_init)
events.spawning_complete.add_listener(runlog.on_spawned)
events.quitting.add_listener(runlog.on_quit)


@events.init.add_listener
def _warn(environment, **_kwargs):
    if not LINE_SECRET:
        print("LINE_CHANNEL_SECRET not set: the webhook task is skipped.")


class _SignedIn(HttpUser):
    """Logs in once with the next seeded account of its kind."""

    abstract = True
    wait_time = between(1, 3)
    accounts: cycle

    def on_start(self) -> None:
        if BYPASS:
            self.client.headers["x-vercel-protection-bypass"] = BYPASS
        self.account = next(self.accounts)
        if self.account is None:
            raise RuntimeError(f"users.json has no {type(self).__name__} accounts: rerun seed.py")
        r = self.client.post(
            "/api/v1/auth/login",
            json={"username": self.account["username"], "password": self.account["password"]},
            name="POST /api/v1/auth/login",
        )
        r.raise_for_status()
        self._use(r.json())

    def _use(self, tokens: dict) -> None:
        self.client.headers["Authorization"] = f"Bearer {tokens['access_token']}"
        self.refresh_token = tokens.get("refresh_token")

    def tasks_list(self) -> None:
        self.client.get("/api/v1/tasks", name="GET /api/v1/tasks")


class Farmer(_SignedIn):
    weight = _weight("FARMER_WEIGHT", 10)
    accounts = _farmers
    last_response_id: str | None = None

    @task(5)
    def task_list(self) -> None:
        self.tasks_list()

    @task(3)
    def task_form(self) -> None:
        self.client.get(f"/api/v1/tasks/{TASK_ID}/form", name="GET /api/v1/tasks/{id}/form")

    @task(2)
    def dropdown(self) -> None:
        self.client.get("/api/v1/ref/constants/province", name="GET /api/v1/ref/constants/{key}")

    @task(2)
    def me(self) -> None:
        self.client.get("/api/v1/me", name="GET /api/v1/me")

    @task(1)
    def submit(self) -> None:
        r = self.client.post(
            f"/api/v1/tasks/{TASK_ID}/responses",
            json={"answer": self._answer()},
            name="POST /api/v1/tasks/{id}/responses",
        )
        if r.ok:
            self.last_response_id = r.json().get("response_id")

    @task(1)
    def edit(self) -> None:
        if not self.last_response_id:
            return
        self.client.put(
            f"/api/v1/tasks/{TASK_ID}/responses/{self.last_response_id}",
            json={"answer": self._answer()},
            name="PUT /api/v1/tasks/{id}/responses/{response_id}",
        )

    @task(1)
    def diary(self) -> None:
        self.client.get("/api/v1/me/history", name="GET /api/v1/me/history")

    @task(1)
    def farms(self) -> None:
        self.client.get("/api/v1/agriculture/farms", name="GET /api/v1/agriculture/farms")

    @task(1)
    def plots(self) -> None:
        self.client.get("/api/v1/agriculture/plots", name="GET /api/v1/agriculture/plots")

    @task(1)
    def refresh(self) -> None:
        # The app refreshes when its hour-long access token runs out; here
        # about once every couple of minutes per farmer.
        if not self.refresh_token or random.random() > 0.2:
            return
        r = self.client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": self.refresh_token},
            name="POST /api/v1/auth/refresh",
        )
        if r.ok:
            self._use(r.json())

    @task(1)
    def line_webhook(self) -> None:
        if not LINE_SECRET:
            return
        signature = base64.b64encode(
            hmac.new(LINE_SECRET.encode(), WEBHOOK_BODY.encode(), hashlib.sha256).digest()
        ).decode()
        self.client.post(
            "/line/webhook",
            data=WEBHOOK_BODY,
            headers={"Content-Type": "application/json", "X-Line-Signature": signature},
            name="POST /line/webhook",
        )

    def _answer(self) -> dict:
        return {"farm_id": self.account["farm_id"], "weight_kg": round(random.uniform(1, 50), 1)}


class HubCollector(_SignedIn):
    weight = _weight("HUB_COLLECTOR_WEIGHT", 1)
    accounts = _collectors

    @task(3)
    def hubs(self) -> None:
        self.client.get("/api/v1/collection/hubs", name="GET /api/v1/collection/hubs")

    @task(3)
    def harvests(self) -> None:
        self.client.get("/api/v1/collection/harvests", name="GET /api/v1/collection/harvests")

    @task(2)
    def task_list(self) -> None:
        self.tasks_list()


class Processor(_SignedIn):
    weight = _weight("PROCESSOR_WEIGHT", 1)
    accounts = _processors

    @task(3)
    def stations(self) -> None:
        self.client.get("/api/v1/processing/stations", name="GET /api/v1/processing/stations")

    @task(3)
    def batches(self) -> None:
        self.client.get("/api/v1/processing/batches", name="GET /api/v1/processing/batches")

    @task(2)
    def task_list(self) -> None:
        self.tasks_list()


class Researcher(_SignedIn):
    weight = _weight("RESEARCHER_WEIGHT", 1)
    accounts = _researchers
    # A researcher reads a page before clicking on.
    wait_time = between(3, 8)

    @task(3)
    def forms(self) -> None:
        self.client.get(
            "/api/v1/forms", params={"include": "stats"}, name="GET /api/v1/forms?include=stats"
        )

    @task(2)
    def form(self) -> None:
        if FORM_ID:
            self.client.get(f"/api/v1/forms/{FORM_ID}", name="GET /api/v1/forms/{id}")

    @task(2)
    def responses(self) -> None:
        if FORM_ID:
            self.client.get(
                f"/api/v1/forms/{FORM_ID}/responses", name="GET /api/v1/forms/{id}/responses"
            )

    @task(1)
    def catalog(self) -> None:
        self.client.get("/api/v1/forms/handlers/catalog", name="GET /api/v1/forms/handlers/catalog")

    @task(3)
    def harvest_series(self) -> None:
        mode = random.choice(["sum", "delta", "average", "frequency"])
        self.client.get(
            f"/api/v1/analytics/harvest/time-series/{mode}",
            params=LAST_YEAR,
            name="GET /api/v1/analytics/harvest/time-series/{mode}",
        )

    @task(2)
    def harvest_summary(self) -> None:
        mode = random.choice(["total", "count"])
        self.client.get(
            f"/api/v1/analytics/harvest/summary/{mode}",
            params=LAST_YEAR,
            name="GET /api/v1/analytics/harvest/summary/{mode}",
        )

    @task(1)
    def harvest_map(self) -> None:
        # No drawn area: every farm that has a location.
        mode = random.choice(["sum", "frequency"])
        self.client.post(
            f"/api/v1/analytics/harvest/spatial/time-series/{mode}",
            params=LAST_YEAR,
            json={},
            name="POST /api/v1/analytics/harvest/spatial/time-series/{mode}",
        )
        self.client.post(
            "/api/v1/analytics/harvest/spatial/summary/total",
            params=LAST_YEAR,
            json={},
            name="POST /api/v1/analytics/harvest/spatial/summary/{mode}",
        )

    @task(1)
    def new_accounts(self) -> None:
        self.client.get(
            "/api/v1/analytics/users/time-series/sum",
            params=LAST_YEAR,
            name="GET /api/v1/analytics/users/time-series/{mode}",
        )
        self.client.get(
            "/api/v1/analytics/users/summary/count",
            params=LAST_YEAR,
            name="GET /api/v1/analytics/users/summary/{mode}",
        )

    @task(1)
    def export(self) -> None:
        self.client.get("/api/v1/reports/raw-data", name="GET /api/v1/reports/raw-data")


class Admin(_SignedIn):
    weight = _weight("ADMIN_WEIGHT", 1)
    accounts = _admins
    wait_time = between(3, 8)

    @task
    def researchers(self) -> None:
        self.client.get("/api/v1/admin/researchers", name="GET /api/v1/admin/researchers")
