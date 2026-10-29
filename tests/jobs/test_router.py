from unittest.mock import AsyncMock, patch

from httpx import AsyncClient

# matches tests/conftest.py's CRON_SECRET
_SECRET = "test-cron-secret"


async def test_missing_secret_is_rejected(client: AsyncClient) -> None:
    resp = await client.get("/internal/cron/reminders")
    assert resp.status_code == 401


async def test_wrong_secret_is_rejected(client: AsyncClient) -> None:
    resp = await client.get(
        "/internal/cron/reminders", headers={"Authorization": "Bearer not-the-secret"}
    )
    assert resp.status_code == 401


async def test_missing_bearer_prefix_is_rejected(client: AsyncClient) -> None:
    # The header must be exactly "Bearer <secret>" -- Vercel Cron's own
    # header shape -- not the bare secret.
    resp = await client.get("/internal/cron/reminders", headers={"Authorization": _SECRET})
    assert resp.status_code == 401


async def test_correct_secret_runs_the_reminder_job(client: AsyncClient) -> None:
    with patch("src.jobs.router.check_and_send_reminders", new=AsyncMock()) as run_reminders:
        resp = await client.get(
            "/internal/cron/reminders", headers={"Authorization": f"Bearer {_SECRET}"}
        )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    run_reminders.assert_awaited_once()


async def test_correct_secret_runs_the_pause_idle_job(client: AsyncClient) -> None:
    with patch("src.jobs.router.pause_idle_conversations", new=AsyncMock()) as run_pause:
        resp = await client.get(
            "/internal/cron/pause-idle-conversations",
            headers={"Authorization": f"Bearer {_SECRET}"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    run_pause.assert_awaited_once()


async def test_the_recurrence_job_answers_its_counts_and_500_when_a_round_failed(
    client: AsyncClient,
) -> None:
    from src.tasks.recurrence import RunResult

    auth = {"Authorization": f"Bearer {_SECRET}"}
    with patch(
        "src.jobs.router.open_due_rounds", new=AsyncMock(return_value=RunResult(created=2))
    ) as run:
        ok = await client.post("/internal/cron/recurrence", headers=auth)
        by_get = await client.get("/internal/cron/recurrence", headers=auth)
    with patch("src.jobs.router.open_due_rounds", new=AsyncMock(return_value=RunResult(failed=1))):
        failed = await client.post("/internal/cron/recurrence", headers=auth)

    assert (ok.status_code, by_get.status_code, failed.status_code) == (200, 200, 500)
    assert ok.json() == {
        "created": 2,
        "alreadyExisted": 0,
        "skippedClosed": 0,
        "skippedNoRecipients": 0,
        "failed": 0,
    }
    assert failed.json()["failed"] == 1
    assert run.await_count == 2
    assert (await client.post("/internal/cron/recurrence")).status_code == 401
