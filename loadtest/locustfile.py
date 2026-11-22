"""Load test for backend-hub's busiest routes (docs-and-plan#322).

Each simulated farmer logs in once, then, with think time between calls,
does what the mobile app does most:

    weight  route
      5     GET  /api/v1/tasks                      (home page, every open)
      3     GET  /api/v1/tasks/{id}/form            (opening a task)
      2     GET  /api/v1/ref/constants/province     (any form's dropdowns)
      1     POST /api/v1/tasks/{id}/responses       (submitting an answer)
      1     POST /line/webhook                      (LINE, signed, no events)

The webhook call carries no events: it measures the signature check and the
acknowledgement LINE waits for, without the background chat work, which
would call LINE's real reply API.

Needs `loadtest/users.json` from seed.py. Set LINE_CHANNEL_SECRET to the
target's channel secret to include the webhook; without it, that task is
skipped. See loadtest/README.md for how to run it.
"""

import base64
import hashlib
import hmac
import json
import os
import random
from itertools import cycle
from pathlib import Path

from locust import HttpUser, between, events, task

SEEDED = json.loads(Path(__file__).with_name("users.json").read_text(encoding="utf-8"))
TASK_ID = SEEDED["task_id"]
_accounts = cycle(SEEDED["users"])
LINE_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
WEBHOOK_BODY = json.dumps({"destination": "Uloadtest", "events": []})


@events.init.add_listener
def _warn(environment, **_kwargs):
    if not LINE_SECRET:
        print("LINE_CHANNEL_SECRET not set: the webhook task is skipped.")


class Farmer(HttpUser):
    wait_time = between(1, 3)

    def on_start(self) -> None:
        account = next(_accounts)
        self.farm_id = account["farm_id"]
        r = self.client.post(
            "/api/v1/auth/login",
            json={"username": account["username"], "password": account["password"]},
            name="POST /api/v1/auth/login",
        )
        r.raise_for_status()
        self.client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"

    @task(5)
    def task_list(self) -> None:
        self.client.get("/api/v1/tasks", name="GET /api/v1/tasks")

    @task(3)
    def task_form(self) -> None:
        self.client.get(f"/api/v1/tasks/{TASK_ID}/form", name="GET /api/v1/tasks/{id}/form")

    @task(2)
    def dropdown(self) -> None:
        self.client.get("/api/v1/ref/constants/province", name="GET /api/v1/ref/constants/{key}")

    @task(1)
    def submit(self) -> None:
        answer = {"farm_id": self.farm_id, "weight_kg": round(random.uniform(1, 50), 1)}
        self.client.post(
            f"/api/v1/tasks/{TASK_ID}/responses",
            json={"answer": answer},
            name="POST /api/v1/tasks/{id}/responses",
        )

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
