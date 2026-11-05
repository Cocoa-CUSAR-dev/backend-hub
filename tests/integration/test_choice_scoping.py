"""US2-13 (docs-and-plan#242) end to end, against a real Postgres: a farmer
sees only their own farms, and after picking one, only that farm's plots --
and typing the name of a plot they weren't offered doesn't get it stored.

Kotlin is mocked with exactly what web-backend now sends: the GLOBAL farm and
plot lists (every farm in the system), the plot question marked
dependsOn="farm_id", and each plot choice carrying its farm as parentId.
Narrowing those is the chatbot's job, which is what's checked here.
"""

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import respx
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from tests.integration.helpers import (
    build_form_response,
    build_postback_event,
    build_text_message_event,
    question_json,
    seed_conversation,
    seed_farm_with_plots,
    seed_question,
    seed_task_form,
    seed_user_with_line_identity,
    serve_form,
    sign_signature,
)


async def _post(client: AsyncClient, body: bytes) -> AsyncMock:
    signature = sign_signature(body, settings.LINE_CHANNEL_SECRET)
    with patch(
        "src.line.client.AsyncMessagingApi.reply_message", new_callable=AsyncMock
    ) as reply_message:
        response = await client.post(
            "/line/webhook", content=body, headers={"X-Line-Signature": signature}
        )
    assert response.status_code == 200
    return reply_message


def _quick_reply_labels(reply_message: AsyncMock) -> list[str]:
    message = reply_message.await_args.args[0].messages[0]
    return [item.action.label for item in message.quick_reply.items]


class _Setup:
    line_user_id: str
    task_id: uuid.UUID
    task_form_id: uuid.UUID
    farm_q: uuid.UUID
    plot_q: uuid.UUID
    form_response: dict[str, Any]
    farm_a: uuid.UUID
    plots_a: list[uuid.UUID]
    plot_b: uuid.UUID


async def _seed(db_session: AsyncSession) -> _Setup:
    """Farm A (linked to this farmer, plots A1/A2) and farm B (someone
    else's, plot B1). A non-multi-submit farm_activity form asking the farm,
    then the plot -- ordinary Quick Reply questions, not #76's picker."""
    s = _Setup()
    s.line_user_id = f"U{uuid.uuid4().hex}"
    user_id = await seed_user_with_line_identity(db_session, line_user_id=s.line_user_id)
    s.farm_a, s.plots_a = await seed_farm_with_plots(
        db_session, farmer_id=user_id, plot_names=["แปลง A1", "แปลง A2"], farm_name="ไร่ A"
    )
    farm_b = uuid.uuid4()
    s.plot_b = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO agriculture.farm (farm_id, farm_name) VALUES (:f, 'ไร่ B')"),
        {"f": farm_b},
    )
    await db_session.execute(
        text(
            "INSERT INTO agriculture.plot (plot_id, farm_id, plot_name) VALUES (:p, :f, 'แปลง B1')"
        ),
        {"p": s.plot_b, "f": farm_b},
    )
    await db_session.commit()

    s.task_id, s.task_form_id = await seed_task_form(db_session, handler="farm_activity")
    s.farm_q = await seed_question(
        db_session, task_id=s.task_id, field_name="farm_id", input_type="OPTION", sort_order=1
    )
    s.plot_q = await seed_question(
        db_session, task_id=s.task_id, field_name="plot_id", input_type="OPTION", sort_order=2
    )

    plot_question = question_json(
        question_id=s.plot_q,
        field_name="plot_id",
        input_type="OPTION",
        label="แปลง",
        sort_order=2,
        choices=[
            {"id": str(s.plots_a[0]), "name": "แปลง A1", "parentId": str(s.farm_a)},
            {"id": str(s.plots_a[1]), "name": "แปลง A2", "parentId": str(s.farm_a)},
            {"id": str(s.plot_b), "name": "แปลง B1", "parentId": str(farm_b)},
        ],
    )
    plot_question["dependsOn"] = "farm_id"
    s.form_response = build_form_response(
        task_form_id=s.task_form_id,
        questions=[
            question_json(
                question_id=s.farm_q,
                field_name="farm_id",
                input_type="OPTION",
                label="ฟาร์ม",
                sort_order=1,
                choices=[
                    {"id": str(s.farm_a), "name": "ไร่ A"},
                    {"id": str(farm_b), "name": "ไร่ B"},
                ],
            ),
            plot_question,
        ],
    )
    await seed_conversation(
        db_session,
        user_id=user_id,
        task_id=s.task_id,
        task_form_id=s.task_form_id,
        current_question_id=s.farm_q,
        status="paused",
    )
    return s


async def _stored(db_session: AsyncSession, question_id: uuid.UUID) -> Any:
    row = await db_session.execute(
        text("SELECT answer FROM chat.conversation_answer WHERE question_id = :q"),
        {"q": question_id},
    )
    value = row.scalar_one_or_none()
    return json.loads(value) if isinstance(value, str) else value


class TestFarmAndPlotChoicesAreScoped:
    async def test_own_farms_only_then_only_that_farms_plots(
        self, db_session: AsyncSession, client: AsyncClient
    ) -> None:
        s = await _seed(db_session)

        with patch.object(settings, "PLOT_SCOPE_MODE", "farmer"), respx.mock:
            serve_form(s.task_form_id, s.form_response)
            farm_question = await _post(
                client,
                build_postback_event(
                    line_user_id=s.line_user_id,
                    data=f"start:{s.task_id}:{s.task_form_id}:farm_activity",
                ),
            )
            plot_question = await _post(
                client, build_text_message_event(line_user_id=s.line_user_id, text_content="ไร่ A")
            )
            another_farms_plot = await _post(
                client,
                build_text_message_event(line_user_id=s.line_user_id, text_content="แปลง B1"),
            )
            await _post(
                client,
                build_text_message_event(line_user_id=s.line_user_id, text_content="แปลง A1"),
            )

        farm_labels = _quick_reply_labels(farm_question)
        assert "ไร่ A" in farm_labels
        assert "ไร่ B" not in farm_labels, "another farmer's farm must not be offered"

        plot_labels = _quick_reply_labels(plot_question)
        assert {"แปลง A1", "แปลง A2"} <= set(plot_labels)
        assert "แปลง B1" not in plot_labels, "a plot of another farm must not be offered"

        # Typing a plot that wasn't offered is re-asked, never stored.
        assert (
            "กรุณาเลือกคำตอบจากตัวเลือกที่กำหนด" in another_farms_plot.await_args.args[0].messages[0].text
        )
        stored = await _stored(db_session, s.plot_q)
        assert stored["value"] == str(s.plots_a[0]), "the offered plot is what gets stored"

    async def test_mode_all_offers_every_farm(
        self, db_session: AsyncSession, client: AsyncClient
    ) -> None:
        """PLOT_SCOPE_MODE=all is the dev switch: accounts not linked to any
        farm still see something to pick."""
        s = await _seed(db_session)

        with patch.object(settings, "PLOT_SCOPE_MODE", "all"), respx.mock:
            serve_form(s.task_form_id, s.form_response)
            farm_question = await _post(
                client,
                build_postback_event(
                    line_user_id=s.line_user_id,
                    data=f"start:{s.task_id}:{s.task_form_id}:farm_activity",
                ),
            )

        assert {"ไร่ A", "ไร่ B"} <= set(_quick_reply_labels(farm_question))
