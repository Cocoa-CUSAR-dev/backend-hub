from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

from src.tasks.schemas import ResponseOut, TaskOut, TaskSubmission


def test_task_submission_round_trip() -> None:
    submission = TaskSubmission(user_id="u-1", task_id="t-1", answer={"farm_id": "f-1"})
    assert submission.model_dump() == {
        "user_id": "u-1",
        "task_id": "t-1",
        "answer": {"farm_id": "f-1"},
    }


def test_task_out_writes_times_the_way_go_did() -> None:
    task = TaskOut(
        task_id=uuid4(),
        title="t",
        description=None,
        open_at=datetime(2026, 10, 1, 8, 30),
        close_at=None,
        task_form_id=None,
        handler=None,
        is_multiple_submit=False,
        status="NOT_STARTED",
    )

    data = task.model_dump(mode="json")

    # Go labelled every zone-less column with "Z"; the app parses that.
    assert data["open_at"] == "2026-10-01T08:30:00Z"
    assert data["close_at"] is None


def test_response_out_converts_an_aware_time_to_utc() -> None:
    bangkok = timezone(timedelta(hours=7))
    out = ResponseOut(
        response_id=uuid4(),
        task_form_id=None,
        submitted_at=datetime(2026, 10, 1, 9, tzinfo=bangkok),
        status=None,
        answer=None,
    )

    assert out.model_dump(mode="json")["submitted_at"] == "2026-10-01T02:00:00Z"
    assert out.submitted_at == datetime(2026, 10, 1, 2, tzinfo=UTC)
