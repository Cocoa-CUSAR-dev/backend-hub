"""Seed (and later remove) the data the load test needs (docs-and-plan#322, #333).

Accounts, each signed up through the real `POST /api/v1/auth/register`:
- farmers, each with a farm and a history of answers (submitted through the
  real API, then spread over the last 60 days so the diary has many days);
- hub collectors, each with a hub;
- processors, each with a processing station and a few batches;
- researchers (with a researcher profile) and admins.

Data for the researcher dashboard and the Excel export: harvests on every
farmer's farm over the last 12 months, at the seeded hubs, graded A/B/C, some
of them collected into batches. All places are one real subdistrict from the
ref tables, so the province/district filters have something to match.

One open task for everyone, with a two-question form (farm, weight) whose
answers land in `agriculture.loadtest_answers`, so submits go through the real
validation and dissection.

Run it against a local or test database only, never production: it creates a
table and writes rows.

    python loadtest/seed.py --api http://127.0.0.1:8766 \
        --db postgresql://postgres@127.0.0.1:5432/hub_flow --users 50

writes `loadtest/users.json` for the locustfile, and

    python loadtest/seed.py --db ... --cleanup

removes everything it made (the task stays for the next run).
"""

import argparse
import asyncio
import json
import os
import random
import secrets
import sys
from pathlib import Path

import asyncpg
import httpx

OUT = Path(__file__).with_name("users.json")
TASK_TITLE = "loadtest: ชั่งน้ำหนักผลโกโก้"
HANDLER = "loadtest_answers"
GRADES = ("A", "B", "C")
# Submitting answers through the API: how many at once.
PARALLEL = 8


async def seed(api: str, dsn: str, counts: dict[str, int], answers: int, harvests: int) -> None:
    db = await asyncpg.connect(dsn)
    try:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS agriculture.loadtest_answers ("
            " answer_id uuid DEFAULT gen_random_uuid() PRIMARY KEY,"
            " farm_id uuid NOT NULL REFERENCES agriculture.farm (farm_id),"
            " weight_kg numeric)"
        )
        await db.execute(
            "INSERT INTO form.handler_registry (handler, schema_name, table_name)"
            " VALUES ($1, 'agriculture', 'loadtest_answers') ON CONFLICT DO NOTHING",
            HANDLER,
        )
        task_id, form_id = await _task(db)
        place = await _place(db)

        # A Vercel preview behind Vercel Authentication needs the bypass secret.
        bypass = os.environ.get("VERCEL_BYPASS")
        headers = {"x-vercel-protection-bypass": bypass} if bypass else {}
        async with httpx.AsyncClient(base_url=api, timeout=60, headers=headers) as client:
            seeded: dict[str, list[dict]] = {}
            for role, count in counts.items():
                seeded[role] = [await _account(client, db, role) for _ in range(count)]

            for i, farmer in enumerate(seeded["farmers"]):
                farmer["farm_id"] = str(
                    await db.fetchval(
                        "INSERT INTO agriculture.farm"
                        " (farm_name, subdistrict_id, district_id, province_id)"
                        " VALUES ($1, $2, $3, $4) RETURNING farm_id",
                        f"loadtest {i}",
                        *place,
                    )
                )
                await db.execute(
                    "INSERT INTO agriculture.farmer_farm (farmer_id, farm_id) VALUES ($1, $2)",
                    farmer["user_id"],
                    farmer["farm_id"],
                )
            for i, collector in enumerate(seeded["hub_collectors"]):
                collector["hub_id"] = str(await _hub(db, collector["user_id"], i, place))
            for i, processor in enumerate(seeded["processors"]):
                processor["station_id"] = str(await _station(db, processor["user_id"], i, place))

            await _harvests(db, seeded, harvests)
            await _answers(client, db, seeded["farmers"], task_id, answers)
    finally:
        await db.close()

    OUT.write_text(
        json.dumps({"task_id": str(task_id), "form_id": str(form_id), **seeded}, indent=2),
        encoding="utf-8",
    )
    summary = ", ".join(f"{len(v)} {k}" for k, v in seeded.items())
    print(f"seeded {summary} and task {task_id} -> {OUT}")


ROLE_NAMES = {
    "farmers": "farmer",
    "hub_collectors": "hub_collector",
    "processors": "processor",
    "researchers": "researcher",
    "admins": "admin",
}


async def _account(client: httpx.AsyncClient, db: asyncpg.Connection, group: str) -> dict:
    """Sign one account up through the API and give it its role."""
    username = f"09{secrets.randbelow(10**8):08d}"
    password = secrets.token_urlsafe(12)
    r = await client.post(
        "/api/v1/auth/register", json={"username": username, "password": password}
    )
    r.raise_for_status()
    user_id = await db.fetchval(
        "SELECT user_id FROM auth.user_account WHERE username = $1", username
    )
    await db.execute(
        "INSERT INTO auth.user_role SELECT $1, role_id FROM auth.role WHERE role_name = $2"
        " ON CONFLICT DO NOTHING",
        user_id,
        ROLE_NAMES[group],
    )
    if group == "researchers":
        await db.execute(
            "INSERT INTO research.researcher (user_id, first_name, last_name, organization)"
            " VALUES ($1, 'loadtest', $2, 'loadtest')",
            user_id,
            username,
        )
    return {"username": username, "password": password, "user_id": str(user_id)}


async def _place(db: asyncpg.Connection) -> tuple:
    """(subdistrict, district, province) ids of one real subdistrict."""
    row = await db.fetchrow(
        "SELECT s.subdistrict_id, d.district_id, d.province_id"
        " FROM ref.subdistrict_constant s"
        " JOIN ref.district_constant d ON d.district_id = s.district_id"
        " ORDER BY s.subdistrict_name_th LIMIT 1"
    )
    if row is None:
        sys.exit("No subdistricts in ref.subdistrict_constant: load the ref data first.")
    return tuple(row)


async def _hub(db: asyncpg.Connection, user_id: str, i: int, place: tuple) -> object:
    hub_id = await db.fetchval(
        "INSERT INTO processing.hub (hub_name, subdistrict_id, district_id, province_id)"
        " VALUES ($1, $2, $3, $4) RETURNING hub_id",
        f"loadtest hub {i}",
        *place,
    )
    await db.execute(
        "INSERT INTO processing.hub_collector (user_id, hub_id, first_name)"
        " VALUES ($1, $2, 'loadtest')",
        user_id,
        hub_id,
    )
    return hub_id


async def _station(db: asyncpg.Connection, user_id: str, i: int, place: tuple) -> object:
    station_id = await db.fetchval(
        "INSERT INTO processing.processing_station"
        " (processing_station_name, subdistrict_id, district_id, province_id)"
        " VALUES ($1, $2, $3, $4) RETURNING processing_station_id",
        f"loadtest station {i}",
        *place,
    )
    await db.execute(
        "INSERT INTO processing.processor (user_id, first_name) VALUES ($1, 'loadtest')", user_id
    )
    await db.execute(
        "INSERT INTO processing.processor_processing_station (processor_id, processing_station_id)"
        " VALUES ($1, $2)",
        user_id,
        station_id,
    )
    for _ in range(3):
        await db.execute(
            "INSERT INTO processing.batch (processing_station_id, origin, quantity_kg)"
            " VALUES ($1, 'loadtest', $2)",
            station_id,
            random.randint(50, 500),
        )
    return station_id


async def _harvests(db: asyncpg.Connection, seeded: dict, per_farm: int) -> None:
    """Harvests over the last 12 months on every farm, at the seeded hubs,
    graded A/B/C; every third one collected into a batch."""
    hubs = [c["hub_id"] for c in seeded["hub_collectors"]]
    batches = [
        r["batch_id"]
        for r in await db.fetch(
            "SELECT batch_id FROM processing.batch WHERE processing_station_id = ANY($1::uuid[])",
            [p["station_id"] for p in seeded["processors"]],
        )
    ]
    if not hubs:
        return
    rows = []
    for farmer in seeded["farmers"]:
        for _ in range(per_farm):
            rows.append((farmer["farm_id"], random.choice(hubs), random.randint(0, 365)))
    for n, (farm_id, hub_id, days_ago) in enumerate(rows):
        harvest_id = await db.fetchval(
            "INSERT INTO collection.harvest (hub_id, farm_id, harvest_date)"
            " VALUES ($1, $2, now() - make_interval(days => $3)) RETURNING harvest_id",
            hub_id,
            farm_id,
            days_ago,
        )
        await db.execute(
            "INSERT INTO collection.harvest_grade_detail (harvest_id, grade_code, quantity_kg)"
            " VALUES ($1, $2, $3)",
            harvest_id,
            random.choice(GRADES),
            round(random.uniform(5, 80), 1),
        )
        if batches and n % 3 == 0:
            await db.execute(
                "INSERT INTO collection.harvest_collection (harvest_id, batch_id) VALUES ($1, $2)",
                harvest_id,
                random.choice(batches),
            )


async def _answers(
    client: httpx.AsyncClient, db: asyncpg.Connection, farmers: list[dict], task_id, per_farmer: int
) -> None:
    """Each farmer's answer history, submitted through the real API, then
    spread over the last 60 days."""
    if per_farmer <= 0:
        return
    gate = asyncio.Semaphore(PARALLEL)

    async def one_farmer(farmer: dict) -> None:
        async with gate:
            r = await client.post(
                "/api/v1/auth/login",
                json={"username": farmer["username"], "password": farmer["password"]},
            )
            r.raise_for_status()
            headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
            for _ in range(per_farmer):
                answer = {
                    "farm_id": farmer["farm_id"],
                    "weight_kg": round(random.uniform(1, 50), 1),
                }
                r = await client.post(
                    f"/api/v1/tasks/{task_id}/responses", json={"answer": answer}, headers=headers
                )
                r.raise_for_status()

    await asyncio.gather(*(one_farmer(f) for f in farmers))
    await db.execute(
        "UPDATE form.response SET submitted_at = now() - random() * interval '60 days'"
        " WHERE user_id = ANY($1::uuid[])",
        [f["user_id"] for f in farmers],
    )


async def _task(db: asyncpg.Connection) -> tuple:
    existing = await db.fetchrow(
        "SELECT t.task_id, f.form_id FROM form.task t"
        " JOIN form.task_form f ON f.task_id = t.task_id WHERE t.title = $1",
        TASK_TITLE,
    )
    if existing:
        return existing["task_id"], existing["form_id"]
    task_id = await db.fetchval(
        "INSERT INTO form.task (title, open_at)"
        " VALUES ($1, now() - interval '1 day') RETURNING task_id",
        TASK_TITLE,
    )
    form_id = await db.fetchval(
        "INSERT INTO form.task_form (task_id, title, handler, is_multiple_submit)"
        " VALUES ($1, 'ชั่งน้ำหนัก', $2, true) RETURNING form_id",
        task_id,
        HANDLER,
    )
    section_id = await db.fetchval(
        "INSERT INTO form.section (form_id, title, sort_order)"
        " VALUES ($1, 'ผลผลิต', 1) RETURNING section_id",
        form_id,
    )
    for order, (field, kind, label) in enumerate(
        [("farm_id", "VARCHAR", "ฟาร์ม"), ("weight_kg", "FLOAT", "น้ำหนัก (กก.)")]
    ):
        await db.execute(
            "INSERT INTO form.question"
            " (section_id, field_name, input_type, is_mandatory, label, sort_order)"
            " VALUES ($1, $2, $3, true, $4, $5)",
            section_id,
            field,
            kind,
            label,
            order,
        )
    return task_id, form_id


async def cleanup(dsn: str) -> None:
    seeded = json.loads(OUT.read_text(encoding="utf-8"))
    # users.json from before #333 had only farmers, under "users".
    farmers = seeded.get("farmers", seeded.get("users", []))
    groups = {**{k: seeded.get(k, []) for k in ROLE_NAMES}, "farmers": farmers}
    user_ids = [u["user_id"] for accounts in groups.values() for u in accounts]
    farm_ids = [u["farm_id"] for u in farmers]
    hub_ids = [u["hub_id"] for u in groups["hub_collectors"]]
    station_ids = [u["station_id"] for u in groups["processors"]]
    db = await asyncpg.connect(dsn)
    try:
        async with db.transaction():
            harvest_ids = (
                "SELECT harvest_id FROM collection.harvest WHERE farm_id = ANY($1::uuid[])"
            )
            for table in ("collection.harvest_collection", "collection.harvest_grade_detail"):
                await db.execute(
                    f"DELETE FROM {table} WHERE harvest_id IN ({harvest_ids})", farm_ids
                )
            await db.execute(
                "DELETE FROM collection.harvest WHERE farm_id = ANY($1::uuid[])", farm_ids
            )
            await db.execute(
                "DELETE FROM agriculture.loadtest_answers WHERE farm_id = ANY($1::uuid[])", farm_ids
            )
            await db.execute(
                "DELETE FROM form.response_correction_log WHERE corrected_by = ANY($1::uuid[])"
                " OR response_id IN (SELECT response_id FROM form.response"
                " WHERE user_id = ANY($1::uuid[]))",
                user_ids,
            )
            await db.execute("DELETE FROM form.response WHERE user_id = ANY($1::uuid[])", user_ids)
            await db.execute(
                "DELETE FROM agriculture.farmer_farm WHERE farmer_id = ANY($1::uuid[])", user_ids
            )
            await db.execute(
                "DELETE FROM agriculture.farm WHERE farm_id = ANY($1::uuid[])", farm_ids
            )
            await db.execute(
                "DELETE FROM processing.batch WHERE processing_station_id = ANY($1::uuid[])",
                station_ids,
            )
            await db.execute(
                "DELETE FROM processing.processor_processing_station"
                " WHERE processing_station_id = ANY($1::uuid[])",
                station_ids,
            )
            await db.execute(
                "DELETE FROM processing.processing_station"
                " WHERE processing_station_id = ANY($1::uuid[])",
                station_ids,
            )
            for table in (
                "processing.processor",
                "processing.hub_collector",
                "research.researcher",
                "auth.refresh_token",
                "auth.user_role",
            ):
                await db.execute(f"DELETE FROM {table} WHERE user_id = ANY($1::uuid[])", user_ids)
            await db.execute("DELETE FROM processing.hub WHERE hub_id = ANY($1::uuid[])", hub_ids)
            await db.execute(
                "DELETE FROM auth.user_account WHERE user_id = ANY($1::uuid[])", user_ids
            )
    finally:
        await db.close()
    print(
        f"removed {len(user_ids)} accounts, their farms, hubs, stations, harvests and answers"
        " (the task stays for the next run)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", default="http://127.0.0.1:8766")
    parser.add_argument(
        "--db", required=True, help="asyncpg DSN, e.g. postgresql://user:pw@host/db"
    )
    parser.add_argument("--users", type=int, default=50, help="farmers")
    parser.add_argument("--hub-collectors", type=int, default=5)
    parser.add_argument("--processors", type=int, default=5)
    parser.add_argument("--researchers", type=int, default=10)
    parser.add_argument("--admins", type=int, default=1)
    parser.add_argument("--answers", type=int, default=30, help="answer history per farmer")
    parser.add_argument(
        "--harvests", type=int, default=24, help="harvests per farm, last 12 months"
    )
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument(
        "--not-production",
        action="store_true",
        help="required for a database that isn't on this machine: you checked it's a test branch",
    )
    args = parser.parse_args()
    local = any(host in args.db for host in ("@127.0.0.1", "@localhost"))
    if not local and not args.not_production:
        sys.exit(
            "Refusing a remote database without --not-production."
            " Use a Neon test branch, never production."
        )
    counts = {
        "farmers": args.users,
        "hub_collectors": args.hub_collectors,
        "processors": args.processors,
        "researchers": args.researchers,
        "admins": args.admins,
    }
    asyncio.run(
        cleanup(args.db)
        if args.cleanup
        else seed(args.api, args.db, counts, args.answers, args.harvests)
    )


if __name__ == "__main__":
    main()
