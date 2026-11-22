"""Seed (and later remove) the data the load test needs (docs-and-plan#322).

- N farmers, signed up through the real `POST /api/v1/auth/register`, each
  with the farmer role and a farm of their own.
- One open task for everyone, with a two-question form (farm, weight) whose
  answers land in `agriculture.loadtest_answers`, so submits go through the
  real validation and dissection.

Run it against a local or staging database only, never production: it
creates a table and writes rows.

    python loadtest/seed.py --api http://127.0.0.1:8766 \
        --db postgresql://postgres@127.0.0.1:5432/hub_flow --users 50

writes `loadtest/users.json` for the locustfile, and

    python loadtest/seed.py --db ... --cleanup

removes everything it made (the users, their farms and answers, the task).
"""

import argparse
import asyncio
import json
import secrets
import sys
from pathlib import Path

import asyncpg
import httpx

OUT = Path(__file__).with_name("users.json")
TASK_TITLE = "loadtest: ชั่งน้ำหนักผลโกโก้"
HANDLER = "loadtest_answers"


async def seed(api: str, dsn: str, n_users: int) -> None:
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
        task_id = await _task(db)

        users = []
        async with httpx.AsyncClient(base_url=api, timeout=30) as client:
            for i in range(n_users):
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
                    "INSERT INTO auth.user_role SELECT $1, role_id FROM auth.role"
                    " WHERE role_name = 'farmer'"
                    " ON CONFLICT DO NOTHING",
                    user_id,
                )
                farm_id = await db.fetchval(
                    "INSERT INTO agriculture.farm (farm_name) VALUES ($1) RETURNING farm_id",
                    f"loadtest {i}",
                )
                await db.execute(
                    "INSERT INTO agriculture.farmer_farm (farmer_id, farm_id) VALUES ($1, $2)",
                    user_id,
                    farm_id,
                )
                users.append(
                    {
                        "username": username,
                        "password": password,
                        "user_id": str(user_id),
                        "farm_id": str(farm_id),
                    }
                )
    finally:
        await db.close()

    OUT.write_text(
        json.dumps({"task_id": str(task_id), "users": users}, indent=2), encoding="utf-8"
    )
    print(f"seeded {len(users)} farmers and task {task_id} -> {OUT}")


async def _task(db: asyncpg.Connection) -> object:
    existing = await db.fetchval("SELECT task_id FROM form.task WHERE title = $1", TASK_TITLE)
    if existing:
        return existing
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
    return task_id


async def cleanup(dsn: str) -> None:
    seeded = json.loads(OUT.read_text(encoding="utf-8"))
    user_ids = [u["user_id"] for u in seeded["users"]]
    farm_ids = [u["farm_id"] for u in seeded["users"]]
    db = await asyncpg.connect(dsn)
    try:
        async with db.transaction():
            await db.execute(
                "DELETE FROM agriculture.loadtest_answers WHERE farm_id = ANY($1::uuid[])", farm_ids
            )
            await db.execute("DELETE FROM form.response WHERE user_id = ANY($1::uuid[])", user_ids)
            await db.execute(
                "DELETE FROM agriculture.farmer_farm WHERE farmer_id = ANY($1::uuid[])", user_ids
            )
            await db.execute(
                "DELETE FROM agriculture.farm WHERE farm_id = ANY($1::uuid[])", farm_ids
            )
            for table in ("auth.refresh_token", "auth.user_role"):
                await db.execute(f"DELETE FROM {table} WHERE user_id = ANY($1::uuid[])", user_ids)
            await db.execute(
                "DELETE FROM auth.user_account WHERE user_id = ANY($1::uuid[])", user_ids
            )
    finally:
        await db.close()
    print(
        f"removed {len(user_ids)} farmers, their farms and answers"
        " (the task stays for the next run)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", default="http://127.0.0.1:8766")
    parser.add_argument(
        "--db", required=True, help="asyncpg DSN, e.g. postgresql://user:pw@host/db"
    )
    parser.add_argument("--users", type=int, default=50)
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
    asyncio.run(cleanup(args.db) if args.cleanup else seed(args.api, args.db, args.users))


if __name__ == "__main__":
    main()
