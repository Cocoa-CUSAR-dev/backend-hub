"""Record what web-backend (Kotlin) answers, as fixtures for the port (docs-and-plan#270).

Not a test: run it by hand against a local Kotlin + Postgres, see README.md.

    python tests/contract/record_web.py                 # every route
    python tests/contract/record_web.py K-10 K-21       # some routes

Every case that writes (POST/PUT/PATCH/DELETE) starts from a freshly reset
seed, so cases never see each other's changes.
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import jwt

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.contract.fixtures import (  # noqa: E402
    ACTORS,
    WEB,
    Case,
    Fixture,
    dump,
    kotlin_value,
    seed_sql,
    xlsx_summary,
)

KOTLIN = os.environ.get("CONTRACT_KOTLIN_URL", "http://localhost:3001")
DB = os.environ.get("CONTRACT_DB_URL", "postgresql://contract:contract@localhost:55432/cocoa")
JWT_KEY = os.environ.get("CONTRACT_JWT_KEY", "test-jwt-key-that-is-at-least-32-bytes-long")
SERVICE_KEY = os.environ.get("CONTRACT_SERVICE_KEY", "contract-service-key")

# Tables a seed or a request writes; reset truncates them and reloads the seed.
# Rows the migrations insert (permission keys, the handler registry, field rules)
# live in tables that are not in this list, or are only added to (auth.permission).
KEEP = {
    "auth.permission",
    "form.handler_registry",
    "form.field_dependency",
    "form.field_validation_rule",
}
SCHEMAS = (
    "auth",
    "form",
    "agriculture",
    "processing",
    "collection",
    "research",
    "storage",
    "chat",
    "notify",
    "ref",
)

# ---- seeded ids ---------------------------------------------------------------
P1, P2 = "30000000-0000-4000-8000-000000000001", "30000000-0000-4000-8000-000000000002"
D1, D2 = "31000000-0000-4000-8000-000000000001", "31000000-0000-4000-8000-000000000002"
FARM1 = "40000000-0000-4000-8000-000000000001"
TASK = {n: f"50000000-0000-4000-8000-00000000000{n}" for n in range(1, 6)}
FORM = {n: f"51000000-0000-4000-8000-00000000000{n}" for n in range(1, 5)}
SECTION1, SECTION3 = "52000000-0000-4000-8000-000000000001", "52000000-0000-4000-8000-000000000003"
Q = {n: f"53000000-0000-4000-8000-0000000000{n:02d}" for n in (1, 2, 3, 4, 21, 22, 23, 24)}
RESP1, RESP2, RESP4 = (f"54000000-0000-4000-8000-00000000000{n}" for n in (1, 2, 4))
ROLE_FARMER = "10000000-0000-4000-8000-000000000003"
RESEARCHER_2 = "20000000-0000-4000-8000-000000000008"
UNKNOWN = "5f000000-0000-4000-8000-000000000999"
# A drawn area around Chumphon: farm 1 is inside, farm 2 (Chiang Mai) is not.
CHUMPHON = (
    '{"type":"Polygon","coordinates":'
    "[[[98.9,10.2],[99.5,10.2],[99.5,10.8],[98.9,10.8],[98.9,10.2]]]}"
)
RANGE = {"from": "2026-08", "to": "2026-10"}


@dataclass
class C:
    """One request to record."""

    name: str
    actor: str | None
    params: dict[str, str] = field(default_factory=dict)
    query: dict[str, str] = field(default_factory=dict)
    body: Any = None
    service_key: str | None = None  # "ok" / "bad"
    # What the hub answers instead, where Kotlin is wrong today (see README.md).
    hub: dict[str, Any] = field(default_factory=dict)


@dataclass
class Route:
    kid: str
    after: str
    method: str
    kotlin: str
    hub: dict[str, Any] | None
    seeds: list[str]
    cases: list[C]
    note: str = ""


def forbidden(actor: str = "farmer_1", **kw: Any) -> C:
    return C("forbidden", actor, **kw)


def anon(**kw: Any) -> C:
    return C("no token", None, **kw)


FORM_SEEDS = ["base", "forms"]
ANSWER_SEEDS = ["base", "forms", "answers"]
PLACE_SEEDS = ["base", "places"]


def harvest_question(
    field_name: str, label: str, order: int, input_type: str = "VARCHAR"
) -> dict[str, Any]:
    return {
        "label": label,
        "description": None,
        "inputType": input_type,
        "fieldName": field_name,
        "isMandatory": False,
        "sortOrder": order,
    }


NEW_FORM = {
    "title": "ฟอร์มทดสอบ contract",
    "description": "สร้างจาก contract test",
    "taskType": "FORM",
    "openAt": "2026-11-01T00:00:00",
    "closeAt": "2026-11-30T16:59:59",
    "handler": "harvest",
    "isMultipleSubmit": False,
    "sections": [
        {
            "title": "การส่ง",
            "description": None,
            "sortOrder": 1,
            "questions": [
                harvest_question("farm_id", "ฟาร์ม", 0, "OPTION") | {"isMandatory": True},
                harvest_question("hub_id", "จุดรวบรวม", 1, "OPTION") | {"isMandatory": True},
                harvest_question("harvest_date", "วันที่ส่ง", 2, "DATE") | {"isMandatory": True},
            ],
        }
    ],
}
NEW_FORM_FULL = NEW_FORM | {
    "title": "ฟอร์มทดสอบ contract ทำซ้ำ",
    "reminder": {
        "enabled": True,
        "timeOfDay": "07:30:00",
        "recipients": [{"type": "ROLE", "id": ROLE_FARMER}],
    },
    "recurrence": {"frequency": "WEEKLY", "weekdays": [1, 4], "untilDate": "2026-12-31"},
    "recipients": [{"type": "ROLE", "id": ROLE_FARMER}],
}

UPDATE_FORM1 = {
    "description": "แก้คำอธิบาย",
    "isMultipleSubmit": False,
    "sections": [
        {
            "sectionId": SECTION1,
            "title": "การส่งผลผลิต",
            "description": None,
            "isActive": True,
            "sortOrder": 1,
            "questions": [
                {
                    "questionId": Q[1],
                    "label": "ฟาร์มที่ส่ง",
                    "description": None,
                    "inputType": "OPTION",
                    "fieldName": "farm_id",
                    "isMandatory": True,
                    "isActive": True,
                    "sortOrder": 0,
                },
                {
                    "questionId": Q[2],
                    "label": "หน่วยรวบรวมที่จัดส่ง",
                    "description": None,
                    "inputType": "OPTION",
                    "fieldName": "hub_id",
                    "isMandatory": True,
                    "isActive": True,
                    "sortOrder": 1,
                },
                {
                    "questionId": Q[3],
                    "label": "วันที่ส่งมา",
                    "description": None,
                    "inputType": "DATE",
                    "fieldName": "harvest_date",
                    "isMandatory": True,
                    "isActive": True,
                    "sortOrder": 2,
                },
                {
                    "questionId": None,
                    "label": "แปลงที่เก็บ",
                    "description": "ใหม่",
                    "inputType": "OPTION",
                    "fieldName": "plot_id",
                    "isMandatory": False,
                    "isActive": True,
                    "sortOrder": 3,
                },
            ],
        }
    ],
}
EDIT_FORM3 = {
    "description": "ปิดคำถามวิธีจัดการ",
    "sections": [
        {
            "sectionId": SECTION3,
            "description": None,
            "isActive": True,
            "questions": [
                {"questionId": Q[22], "description": None, "isActive": False, "isMandatory": False},
                {"questionId": Q[23], "description": None, "isActive": True, "isMandatory": False},
            ],
        }
    ],
}


def analytics(
    kid: str, after: str, method: str, kotlin: str, hub: str, spatial: bool = False
) -> Route:
    body = {"geoJson": CHUMPHON} if spatial else None
    hub_map = by_mode(method, hub)
    if spatial:
        # The drawn area is a PostGIS query; tests/resources/schema.sql has no PostGIS.
        hub_map["requires"] = ["postgis"]
    if spatial:
        cases = [
            C("drawn area around Chumphon", "researcher", query=RANGE, body=body),
            C("no area", "researcher", query=RANGE, body={}),
            C("grade A in the area", "researcher", query=RANGE | {"gradeCode": "A"}, body=body),
            forbidden(query=RANGE, body=body),
            anon(query=RANGE, body=body),
        ]
    else:
        cases = [
            C("all farms", "researcher", query=RANGE),
            C("one province", "researcher", query=RANGE | {"provinceId": P1}),
            C("grade A", "researcher", query=RANGE | {"gradeCode": "A"}),
            C(
                "district not in province",
                "researcher",
                query=RANGE | {"provinceId": P1, "districtId": D2},
            ),
            C("missing range", "researcher", hub={"status": 422}),  # Kotlin: 500
            forbidden(query=RANGE),
            anon(query=RANGE),
        ]
    return Route(kid, after, method, kotlin, hub_map, PLACE_SEEDS, cases)


def by_mode(method: str, hub: str) -> dict[str, Any]:
    """The hub serves one route per family with the mode in the path (CONVENTIONS §4):
    .../time-series/sum -> .../time-series/{mode}, filled with "sum"."""
    base, mode = hub.rsplit("/", 1)
    return {"method": method, "path": f"{base}/{{mode}}", "params": {"mode": f"={mode}"}}


def user_analytics(kid: str, after: str, kotlin: str, hub: str) -> Route:
    return Route(
        kid,
        after,
        "GET",
        kotlin,
        by_mode("GET", hub),
        ["base"],
        [
            C("all roles", "researcher", query={"from": "2026-07", "to": "2026-10"}),
            C(
                "farmers only",
                "researcher",
                query={"from": "2026-07", "to": "2026-10", "roleName": "farmer"},
            ),
            forbidden(query={"from": "2026-07", "to": "2026-10"}),
            anon(query={"from": "2026-07", "to": "2026-10"}),
        ],
    )


F = "/api/v1/forms"
T = "/api/v1/tasks"
A = "/api/v1/analytics"
HUB_FORM = f"{F}/{{form_id}}"

ROUTES: list[Route] = [
    # ---- me: history and diary (ME-03..05, K-51) ------------------------------
    Route(
        "K-07",
        "ME-05",
        "GET",
        "/api/v1/me/diaries/{date}",
        {"method": "GET", "path": "/api/v1/me/diaries/{date}"},
        ANSWER_SEEDS,
        [
            C("a day with a diary", "farmer_1", {"date": "2026-10-05"}),
            C("a day without", "farmer_1", {"date": "2026-10-06"}),
            C("bad date", "farmer_1", {"date": "2026-13-01"}),
            forbidden("researcher", params={"date": "2026-10-05"}),
            anon(params={"date": "2026-10-05"}),
        ],
    ),
    Route(
        "K-08",
        "ME-03",
        "GET",
        "/api/v1/me/history",
        {"method": "GET", "path": "/api/v1/me/history"},
        ANSWER_SEEDS,
        [
            C("my days", "farmer_1"),
            C("first page of one", "farmer_1", query={"page": "0", "size": "1"}),
            C("someone with nothing", "farmer_2"),
            forbidden("researcher"),
            anon(),
        ],
    ),
    Route(
        "K-09",
        "ME-04",
        "GET",
        "/api/v1/me/history/{date}",
        {"method": "GET", "path": "/api/v1/me/history/{date}"},
        ANSWER_SEEDS,
        [
            C("one day", "farmer_1", {"date": "2026-10-05"}),
            C("an empty day", "farmer_1", {"date": "2026-10-20"}),
            C("only my own answers", "farmer_2", {"date": "2026-10-05"}),
            forbidden("researcher", params={"date": "2026-10-05"}),
            anon(params={"date": "2026-10-05"}),
        ],
    ),
    # ---- forms: read (FORM-01, FORM-02) ------------------------------------------
    Route(
        "K-10",
        "FORM-01",
        "GET",
        F,
        {"method": "GET", "path": F},
        FORM_SEEDS,
        [
            C("researcher", "researcher", hub={"unordered": True}),
            C("admin", "admin", hub={"unordered": True}),
            forbidden(),
            anon(),
        ],
        note="Hides the form with no section (task 4). Kotlin doesn't order this list.",
    ),
    Route(
        "K-11",
        "FORM-02",
        "GET",
        f"{F}/{{formId}}",
        {"method": "GET", "path": HUB_FORM, "params": {"form_id": "formId"}},
        FORM_SEEDS,
        [
            C("harvest form", "researcher", {"formId": FORM[1]}),
            C("form with inactive parts", "researcher", {"formId": FORM[3]}),
            C("farmer, open form", "farmer_1", {"formId": FORM[1]}),
            # The hub applies the task list's assignment rule (#261); Kotlin served the form.
            C(
                "farmer, form assigned to someone else",
                "farmer_2",
                {"formId": FORM[2]},
                hub={"status": 404},
            ),
            C("unknown form", "researcher", {"formId": UNKNOWN}),
            anon(params={"formId": FORM[1]}),
        ],
    ),
    Route(
        "K-21",
        "FORM-01",
        "GET",
        T,
        {"method": "GET", "path": F, "query": {"include": "stats"}},
        FORM_SEEDS,
        [
            # The hub adds formId to each row (docs-and-plan#271).
            C("researcher", "researcher", hub={"ignore": ["*.formId"]}),
            C(
                "page of two",
                "researcher",
                query={"page": "0", "size": "2"},
                hub={"ignore": ["*.formId"]},
            ),
            C("size 0", "researcher", query={"size": "0"}),
            forbidden(),
            # FORM-01 needs read:form:all, which admins hold (K-21 asked for read:task:all).
            forbidden("admin", hub={"status": 200, "compare": "status"}),
            anon(),
        ],
        note="Shows every task, including task 4 (form without section) and task 5 (no form).",
    ),
    Route(
        "K-22",
        "FORM-02",
        "GET",
        f"{T}/{{taskId}}",
        {
            "method": "GET",
            "path": HUB_FORM,
            "query": {"include": "stats"},
            "params": {"form_id": "form_of:taskId"},
        },
        FORM_SEEDS,
        [
            C("task 1", "researcher", {"taskId": TASK[1]}, hub={"ignore": ["formId"]}),
            # /forms/{form_id} needs a form; this task has none.
            C("task without form", "researcher", {"taskId": TASK[5]}, hub={"status": 404}),
            C("unknown task", "researcher", {"taskId": UNKNOWN}),
            forbidden(params={"taskId": TASK[1]}),
            anon(params={"taskId": TASK[1]}),
        ],
        note="Kotlin takes a task id; FORM-02 takes a form id (docs-and-plan#271), so the replay "
        "sends the task's form id (hub.params form_of:taskId).",
    ),
    Route(
        "K-50",
        "FORM-02",
        "GET",
        "/api/v1/service/forms/{formId}",
        None,
        FORM_SEEDS,
        [
            C("chatbot reads a form", None, {"formId": FORM[1]}, service_key="ok"),
            C("wrong service key", None, {"formId": FORM[1]}, service_key="bad"),
            C("unknown form", None, {"formId": UNKNOWN}, service_key="ok"),
        ],
        note="Becomes an in-process call (forms.service.get); no hub route to replay against.",
    ),
    # ---- forms: builder (FORM-03..05) --------------------------------------------
    Route(
        "K-12",
        "FORM-03",
        "POST",
        F,
        {"method": "POST", "path": F},
        FORM_SEEDS,
        [
            C("minimal harvest form", "researcher", body=NEW_FORM),
            C("with reminder, weekly repeat and recipients", "researcher", body=NEW_FORM_FULL),
            C("no sections", "researcher", body=NEW_FORM | {"sections": []}),
            C(
                "a field the handler doesn't have",
                "researcher",
                body=NEW_FORM
                | {
                    "sections": [
                        NEW_FORM["sections"][0]
                        | {"questions": [harvest_question("not_a_column", "ผิด", 0)]}
                    ]
                },
                # Refused for hub_id and farm_id, which are NOT NULL only in
                # the migrations' collection.harvest.
                hub={"requires": ["registry"]},
            ),
            C("unknown handler", "researcher", body=NEW_FORM | {"handler": "no_such_handler"}),
            forbidden(body=NEW_FORM),
            forbidden("admin", body=NEW_FORM),
        ],
    ),
    Route(
        "K-13",
        "FORM-04",
        "PUT",
        f"{F}/{{formId}}",
        {"method": "PUT", "path": HUB_FORM, "params": {"form_id": "formId"}},
        FORM_SEEDS,
        [
            C(
                "rename a question and add one",
                "researcher",
                {"formId": FORM[1]},
                body=UPDATE_FORM1,
            ),
            C(
                "no sections",
                "researcher",
                {"formId": FORM[1]},
                body=UPDATE_FORM1 | {"sections": []},
            ),
            C("unknown form", "researcher", {"formId": UNKNOWN}, body=UPDATE_FORM1),
            forbidden(params={"formId": FORM[1]}, body=UPDATE_FORM1),
        ],
    ),
    Route(
        "K-14",
        "FORM-05",
        "PUT",
        f"{F}/{{formId}}/edit",
        {"method": "PUT", "path": f"{HUB_FORM}/edit", "params": {"form_id": "formId"}},
        FORM_SEEDS,
        [
            C("switch a question off", "researcher", {"formId": FORM[3]}, body=EDIT_FORM3),
            C("unknown form", "researcher", {"formId": UNKNOWN}, body=EDIT_FORM3),
            forbidden(params={"formId": FORM[3]}, body=EDIT_FORM3),
        ],
    ),
    # ---- forms: handlers (FORM-06, FORM-07, catalog) -----------------------------
    Route(
        "K-15",
        "FORM-06",
        "GET",
        f"{F}/handlers",
        {"method": "GET", "path": f"{F}/handlers", "requires": ["registry"]},
        FORM_SEEDS,
        [
            C("researcher", "researcher"),
            forbidden(),
            forbidden("admin"),
            anon(),
        ],
    ),
    Route(
        "K-16",
        "FORM-07",
        "GET",
        f"{F}/handlers/{{handler}}/fields",
        {"method": "GET", "path": f"{F}/handlers/{{handler}}/fields", "requires": ["registry"]},
        FORM_SEEDS,
        [
            C("harvest", "researcher", {"handler": "harvest"}),
            C("child handler", "researcher", {"handler": "harvest_grade_detail"}),
            C("unknown handler", "researcher", {"handler": "no_such_handler"}),
            forbidden(params={"handler": "harvest"}),
        ],
    ),
    Route(
        "K-CAT",
        "FORM-CATALOG",
        "GET",
        f"{F}/handlers/catalog",
        {"method": "GET", "path": f"{F}/handlers/catalog", "requires": ["registry"]},
        ANSWER_SEEDS,
        [C("researcher", "researcher"), forbidden(), anon()],
        note="Not on the Before sheet (added to Kotlin after 2026-10-14).",
    ),
    # ---- forms: reminders (FORM-08..11) ------------------------------------------
    Route(
        "K-17",
        "FORM-08",
        "GET",
        f"{F}/{{formId}}/reminder",
        {"method": "GET", "path": f"{HUB_FORM}/reminder", "params": {"form_id": "formId"}},
        FORM_SEEDS,
        [
            C("form with a reminder", "researcher", {"formId": FORM[1]}),
            C("form without", "researcher", {"formId": FORM[3]}),
            C("unknown form", "researcher", {"formId": UNKNOWN}),
            forbidden(params={"formId": FORM[1]}),
        ],
    ),
    Route(
        "K-18",
        "FORM-09",
        "PUT",
        f"{F}/{{formId}}/reminder",
        {"method": "PUT", "path": f"{HUB_FORM}/reminder", "params": {"form_id": "formId"}},
        FORM_SEEDS,
        [
            C(
                "turn one on",
                "researcher",
                {"formId": FORM[3]},
                body={
                    "enabled": True,
                    "timeOfDay": "07:30:00",
                    "recipients": [{"type": "ROLE", "id": ROLE_FARMER}],
                },
            ),
            C(
                "turn one off",
                "researcher",
                {"formId": FORM[1]},
                body={"enabled": False, "timeOfDay": "08:00:00"},
            ),
            C(
                "unknown form",
                "researcher",
                {"formId": UNKNOWN},
                body={"enabled": True, "timeOfDay": "07:30:00"},
            ),
            forbidden(params={"formId": FORM[1]}, body={"enabled": False, "timeOfDay": "08:00:00"}),
        ],
    ),
    Route(
        "K-19",
        "FORM-10",
        "GET",
        "/api/v1/reminders/recipient-options/roles",
        {"method": "GET", "path": f"{F}/reminder-recipients/roles"},
        ["base"],
        [
            C("researcher", "researcher"),
            forbidden(),
            anon(),
        ],
    ),
    Route(
        "K-20",
        "FORM-11",
        "GET",
        "/api/v1/reminders/recipient-options/users",
        {"method": "GET", "path": f"{F}/reminder-recipients/users"},
        ["base"],
        [
            C("search contract", "researcher", query={"q": "contract"}),
            C("search farmer", "researcher", query={"q": "farmer"}),
            C("empty search", "researcher"),
            forbidden(query={"q": "contract"}),
        ],
    ),
    # ---- forms: responses and review (FORM-12..14) ---------------------------------
    Route(
        "K-23",
        "FORM-12",
        "GET",
        f"{T}/{{taskId}}/responses",
        {"method": "GET", "path": f"{HUB_FORM}/responses", "params": {"form_id": "form_of:taskId"}},
        ANSWER_SEEDS,
        [
            C("task 1", "researcher", {"taskId": TASK[1]}),
            C("multi-submit task", "researcher", {"taskId": TASK[2]}),
            C("page of one", "researcher", {"taskId": TASK[1]}, query={"page": "0", "size": "1"}),
            C("unknown task", "researcher", {"taskId": UNKNOWN}),
            forbidden(params={"taskId": TASK[1]}),
        ],
        note="Hub takes the form id (docs-and-plan#271).",
    ),
    Route(
        "K-24",
        "FORM-13",
        "GET",
        f"{T}/{{taskId}}/responses/{{responseId}}",
        {
            "method": "GET",
            "path": f"{HUB_FORM}/responses/{{response_id}}",
            "params": {"form_id": "form_of:taskId", "response_id": "responseId"},
        },
        ANSWER_SEEDS,
        [
            # Kotlin joins task_form.form_id to response.task_log_id (a task id),
            # so it never finds one.
            C(
                "one answer",
                "researcher",
                {"taskId": TASK[1], "responseId": RESP1},
                hub={"status": 200, "compare": "status"},
            ),
            C("answer of another task", "researcher", {"taskId": TASK[1], "responseId": RESP2}),
            C("unknown answer", "researcher", {"taskId": TASK[1], "responseId": UNKNOWN}),
            forbidden(params={"taskId": TASK[1], "responseId": RESP1}),
        ],
    ),
    Route(
        "K-25",
        "FORM-12",
        "GET",
        f"{T}/{{taskId}}/review",
        {
            "method": "GET",
            "path": f"{HUB_FORM}/responses",
            "query": {"view": "review"},
            "params": {"form_id": "form_of:taskId"},
        },
        ANSWER_SEEDS,
        [
            C("task 1", "researcher", {"taskId": TASK[1]}),
            C("page of one", "researcher", {"taskId": TASK[1]}, query={"page": "0", "size": "1"}),
            # An unknown form is 404, as on every other /forms/{id} route (Kotlin: 200 []).
            C("unknown task", "researcher", {"taskId": UNKNOWN}, hub={"status": 404}),
            forbidden(params={"taskId": TASK[1]}),
        ],
    ),
    Route(
        "K-26",
        "FORM-14",
        "PATCH",
        f"{T}/{{taskId}}/review/{{responseId}}/fields/{{fieldName}}",
        {
            "method": "PATCH",
            "path": f"{HUB_FORM}/responses/{{response_id}}/fields/{{field_name}}",
            "params": {
                "form_id": "form_of:taskId",
                "response_id": "responseId",
                "field_name": "fieldName",
            },
        },
        ANSWER_SEEDS,
        [
            C(
                "fix a text field",
                "researcher",
                {"taskId": TASK[1], "responseId": RESP1, "fieldName": "logistic_result"},
                body={"value": "รถมอเตอร์ไซค์", "reason": "ผู้ตอบแจ้งแก้"},
            ),
            C(
                "fix a date",
                "researcher",
                {"taskId": TASK[1], "responseId": RESP4, "fieldName": "harvest_date"},
                body={"value": "2026-10-07"},
            ),
            C(
                "not a date",
                "researcher",
                {"taskId": TASK[1], "responseId": RESP4, "fieldName": "harvest_date"},
                body={"value": "7 Oct"},
            ),
            C(
                "choice fields can't be edited",
                "researcher",
                {"taskId": TASK[1], "responseId": RESP1, "fieldName": "farm_id"},
                body={"value": FARM1},
            ),
            C(
                "unknown field",
                "researcher",
                {"taskId": TASK[1], "responseId": RESP1, "fieldName": "nope"},
                body={"value": "x"},
            ),
            C(
                "unknown answer",
                "researcher",
                {"taskId": TASK[1], "responseId": UNKNOWN, "fieldName": "logistic_result"},
                body={"value": "x"},
            ),
            forbidden(
                params={"taskId": TASK[1], "responseId": RESP1, "fieldName": "logistic_result"},
                body={"value": "x"},
            ),
        ],
    ),
    # ---- analytics (ANL-01..06) ----------------------------------------------------
    *[
        analytics(
            f"K-{27 + i}",
            "ANL-01",
            "GET",
            f"{A}/harvest/time-series/{m}",
            f"{A}/harvest/time-series/{m}",
        )
        for i, m in enumerate(("sum", "delta", "average", "frequency"))
    ],
    *[
        analytics(
            f"K-{31 + i}", "ANL-02", "GET", f"{A}/harvest/summary/{m}", f"{A}/harvest/summary/{m}"
        )
        for i, m in enumerate(("total", "count"))
    ],
    user_analytics("K-33", "ANL-03", f"{A}/users/time-series/sum", f"{A}/users/time-series/sum"),
    user_analytics(
        "K-34", "ANL-03", f"{A}/users/time-series/delta", f"{A}/users/time-series/delta"
    ),
    user_analytics("K-35", "ANL-04", f"{A}/users/summary/count", f"{A}/users/summary/count"),
    *[
        analytics(
            f"K-{36 + i}",
            "ANL-05",
            "POST",
            f"{A}/harvest/spatial/time-series/{m}",
            f"{A}/harvest/spatial/time-series/{m}",
            spatial=True,
        )
        for i, m in enumerate(("delta", "sum", "average", "frequency"))
    ],
    *[
        analytics(
            f"K-{40 + i}",
            "ANL-06",
            "POST",
            f"{A}/harvest/spatial/summary/{m}",
            f"{A}/harvest/spatial/summary/{m}",
            spatial=True,
        )
        for i, m in enumerate(("total", "count"))
    ],
    # ---- reports (RPT-01) ----------------------------------------------------------
    Route(
        "K-42",
        "RPT-01",
        "GET",
        "/api/v1/reports/raw-data/download",
        {"method": "GET", "path": "/api/v1/reports/raw-data"},
        PLACE_SEEDS,
        [
            C("every sheet", "researcher"),
            C("farms only", "researcher", query={"sheets": "FARM"}),
            C("harvests only", "researcher", query={"sheets": "HARVEST"}),
            C("unknown sheet", "researcher", query={"sheets": "NOPE"}),
            forbidden(),
            anon(),
        ],
        note="web-backend dev answers 400 for every export: XlsxService pages 1000 rows but "
        "PageRequest allows at most 500, and the harvest sheet throws on a null yes/no or number "
        "(XlsxService.kt:101-108). Recorded from a local build with exactly those two fixed "
        "(exportPageSize = 500; a null yes/no cell is '-', a null number 0.0, as the same file "
        "does for null text and area): CONTRACT_KOTLIN_URL=<that build> record_web.py K-42.",
    ),
    # ---- admin: researchers (ADMIN-02..04) -----------------------------------------
    Route(
        "K-45",
        "ADMIN-02",
        "GET",
        "/api/v1/researchers",
        {"method": "GET", "path": "/api/v1/admin/researchers"},
        ["base"],
        [
            C("admin", "admin"),
            C("by organization", "admin", query={"organization": "CUSAR"}),
            forbidden("researcher"),
            anon(),
        ],
    ),
    Route(
        "K-47",
        "ADMIN-03",
        "GET",
        "/api/v1/researchers/{userId}",
        {
            "method": "GET",
            "path": "/api/v1/admin/researchers/{user_id}",
            "params": {"user_id": "userId"},
        },
        ["base"],
        [
            C("one researcher", "admin", {"userId": RESEARCHER_2}),
            C("unknown user", "admin", {"userId": UNKNOWN}),
            forbidden("researcher", params={"userId": RESEARCHER_2}),
        ],
    ),
    Route(
        "K-49",
        "ADMIN-04",
        "DELETE",
        "/api/v1/researchers/{userId}",
        {
            "method": "DELETE",
            "path": "/api/v1/admin/researchers/{user_id}",
            "params": {"user_id": "userId"},
        },
        ["base"],
        [
            C("remove one", "admin", {"userId": RESEARCHER_2}),
            C("unknown user", "admin", {"userId": UNKNOWN}),
            forbidden("researcher", params={"userId": RESEARCHER_2}),
        ],
    ),
    # ---- chatbot -> Kotlin calls that become in-process -----------------------------
    Route(
        "K-51",
        "TASK-06",
        "POST",
        "/api/v1/service/diaries/generate",
        None,
        ANSWER_SEEDS,
        [
            C(
                "farmer with a conversation, nothing sent today",
                None,
                body={"userId": ACTORS["farmer_1"][0]},
                service_key="ok",
            ),
            C(
                "farmer without a conversation",
                None,
                body={"userId": ACTORS["farmer_2"][0]},
                service_key="ok",
            ),
            C("wrong service key", None, body={"userId": ACTORS["farmer_1"][0]}, service_key="bad"),
        ],
        note="Becomes diary.service.generate_for, called by tasks.service.submit; no hub route.",
    ),
    Route(
        "K-RUN",
        "JOB-03",
        "POST",
        "/api/v1/service/recurrence/run",
        {"method": "POST", "path": "/internal/cron/recurrence"},
        FORM_SEEDS,
        [
            C("open due rounds", None, service_key="ok", hub={"compare": "status"}),
            C("wrong service key", None, service_key="bad"),
        ],
        note="Not on the Before sheet. What it opens depends on today's date: "
        "compare the status only.",
    ),
]


# ---- running ----------------------------------------------------------------------


async def perms_of(conn: asyncpg.Connection, user_id: str) -> tuple[list[str], list[str]]:
    rows = await conn.fetch(
        """SELECT r.role_name, p.permission_key
           FROM auth.user_role ur JOIN auth.role r ON r.role_id = ur.role_id
           LEFT JOIN auth.role_permission rp ON rp.role_id = r.role_id
           LEFT JOIN auth.permission p ON p.permission_id = rp.permission_id
           WHERE ur.user_id = $1""",
        user_id,
    )
    return sorted({r["role_name"] for r in rows}), sorted(
        {r["permission_key"] for r in rows if r["permission_key"]}
    )


def token(user_id: str, username: str, roles: list[str], perms: list[str]) -> str:
    """The same claims backend-hub issues (src/auth/tokens.py), which Kotlin accepts."""
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": user_id,
            "username": username,
            "roles": roles,
            "perms": perms,
            "typ": "access",
            "iat": now,
            "exp": now.timestamp() + 3600,
            "userId": user_id,
            "user_id": user_id,
            "permissions": perms,
        },
        JWT_KEY,
        algorithm="HS256",
    )


async def reset(conn: asyncpg.Connection, seeds: list[str]) -> None:
    """Back to the migrations' rows plus exactly the seeds a route replays with,
    so the recorded answer depends on nothing the replay won't load."""
    tables = [
        f"{r['schemaname']}.{r['tablename']}"
        for r in await conn.fetch(
            "SELECT schemaname, tablename FROM pg_tables WHERE schemaname = ANY($1)", list(SCHEMAS)
        )
        if f"{r['schemaname']}.{r['tablename']}" not in KEEP
    ]
    await conn.execute(f"TRUNCATE {', '.join(tables)} CASCADE")
    for sql in seed_sql(seeds):
        await conn.execute(sql)


def fill(template: str, params: dict[str, str]) -> str:
    for k, v in params.items():
        template = template.replace("{" + k + "}", v)
    return template


async def record(route: Route, conn: asyncpg.Connection, client: httpx.Client) -> Fixture:
    cases: list[Case] = []
    await reset(conn, route.seeds)
    for c in route.cases:
        if route.method != "GET":
            await reset(conn, route.seeds)
        headers: dict[str, str] = {}
        if c.actor:
            uid, name = ACTORS[c.actor]
            headers["Authorization"] = "Bearer " + token(uid, name, *await perms_of(conn, uid))
        if c.service_key:
            headers["X-Service-Key"] = SERVICE_KEY if c.service_key == "ok" else "wrong-key"
        path = fill(route.kotlin, c.params)
        r = client.request(route.method, path, params=c.query, json=c.body, headers=headers)
        ctype = r.headers.get("content-type", "")
        if "spreadsheetml" in ctype:
            response: Any = {"xlsx": xlsx_summary(r.content)}
        elif "json" in ctype and r.content:
            response = kotlin_value(r.json())
        else:
            response = r.text or None
        cases.append(
            Case(
                name=c.name,
                actor=c.actor,
                status=r.status_code,
                method=route.method,
                path=path,
                params=c.params,
                query=c.query,
                body=c.body,
                response=response,
                service_key=bool(c.service_key),
                hub=c.hub,
            )
        )
        print(f"  {route.kid} {r.status_code} {c.name}")
    return Fixture(
        kid=route.kid,
        after=route.after,
        kotlin={"method": route.method, "path": route.kotlin},
        hub=route.hub,
        seeds=route.seeds,
        recorded_on=datetime.now(UTC).date().isoformat(),
        cases=cases,
        note=route.note,
    )


async def main(only: list[str]) -> None:
    conn = await asyncpg.connect(DB)
    try:
        with httpx.Client(base_url=KOTLIN, timeout=60) as client:
            for route in ROUTES:
                if only and route.kid not in only:
                    continue
                fx = await record(route, conn, client)
                (WEB / f"{route.kid}.json").write_text(dump(fx), encoding="utf-8")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
