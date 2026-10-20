"""docs-and-plan#24 -- one whole, successful conversation, start to finish,
through the real webhook and a real Postgres.

Every other integration test starts from a conversation seeded part-way
through (at the summary, mid-question, paused). This one seeds only what a
real farmer has before they ever type -- a linked LINE account and a task
assigned to them -- and then drives every step by sending the event LINE
would send, taking each next button from the reply it actually got:

    "สวัสดี" -> welcome, with a เริ่ม button
    เริ่ม     -> the task list, with a button for the task
    tap task -> the first question
    answers  -> the next question, then the summary with ยืนยัน
    ยืนยัน    -> saved: one submission to Go with exactly the typed answers

Only the services outside this repo are stubbed: the LINE Messaging API,
Kotlin's form lookup (respx), Go's submit and last-answer lookups, the
intent classifier's LLM, and the diary push after saving.
"""

import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import respx
from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.conversation import intent
from src.forms.config import forms_settings
from src.line import messages
from src.line.config import line_settings
from src.tasks.schemas import TaskSubmission
from tests.integration.helpers import (
    build_form_response,
    build_postback_event,
    build_text_message_event,
    question_json,
    seed_question,
    seed_task_form,
    seed_user_with_line_identity,
    sign_signature,
)

TASK_TITLE = "บันทึกการใส่ปุ๋ย"


async def _post(client: AsyncClient, body: bytes) -> Any:
    """Delivers one webhook event and returns the single message replied."""
    signature = sign_signature(body, line_settings.LINE_CHANNEL_SECRET)
    with (
        patch(
            "src.line.service.AsyncMessagingApi.reply_message", new_callable=AsyncMock
        ) as reply_message,
        # The greeting is classified before any flow runs; no LLM here.
        patch(
            "src.line.router.intent.classify",
            new=AsyncMock(return_value=intent.IntentResult(intent=intent.Intent.GREETING)),
        ),
        # A first-time farmer has no previous answer to offer reusing.
        patch("src.line.router.fetch_last_answer", new=AsyncMock(return_value=None)),
        patch("src.line.router.generate_diary", new=AsyncMock(return_value="ไดอารี่")),
        patch("src.line.router.push_flex", new=AsyncMock()),
        patch("src.line.router.mint_sso_token", new=AsyncMock(return_value="token")),
    ):
        response = await client.post(
            "/line/webhook", content=body, headers={"X-Line-Signature": signature}
        )
    assert response.status_code == 200
    reply_message.assert_awaited_once()
    request = reply_message.await_args.args[0]
    assert len(request.messages) == 1
    return request.messages[0]


async def _say(client: AsyncClient, line_user_id: str, words: str) -> Any:
    return await _post(
        client, build_text_message_event(line_user_id=line_user_id, text_content=words)
    )


async def _tap(client: AsyncClient, line_user_id: str, data: str) -> Any:
    return await _post(client, build_postback_event(line_user_id=line_user_id, data=data))


def _text(message: Any) -> str:
    # The final "saved" ack is a FlexMessage; its altText carries the wording.
    return str(getattr(message, "text", None) or message.alt_text)


def _buttons(message: Any) -> dict[str, Any]:
    """label -> action, for the Quick Reply buttons under a message."""
    assert message.quick_reply is not None, f"no buttons under: {_text(message)!r}"
    return {item.action.label: item.action for item in message.quick_reply.items}


async def _conversation(db_session: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    rows = (
        await db_session.execute(
            text("SELECT conversation_id, status FROM chat.conversation WHERE user_id = :uid"),
            {"uid": user_id},
        )
    ).all()
    assert len(rows) == 1, f"expected exactly one conversation, got {len(rows)}"
    return {"conversation_id": rows[0][0], "status": str(rows[0][1])}


class TestHappyPath:
    async def test_greeting_to_saved_submission(
        self, db_session: AsyncSession, client: AsyncClient
    ) -> None:
        line_user_id = f"U{uuid.uuid4().hex}"
        user_id = await seed_user_with_line_identity(db_session, line_user_id=line_user_id)
        task_id, task_form_id = await seed_task_form(db_session, title=TASK_TITLE, handler="notes")
        questions = [
            ("fertilizer_name", "VARCHAR", "ใส่ปุ๋ยอะไร", 1),
            ("amount_kg", "FLOAT", "ใส่กี่กิโลกรัม", 2),
        ]
        question_ids = {}
        for field_name, input_type, label, sort_order in questions:
            question_ids[field_name] = await seed_question(
                db_session,
                task_id=task_id,
                field_name=field_name,
                input_type=input_type,
                label=label,
                sort_order=sort_order,
            )
        form_response = build_form_response(
            task_form_id=task_form_id,
            questions=[
                question_json(
                    question_id=question_ids[field_name],
                    field_name=field_name,
                    input_type=input_type,
                    label=label,
                    sort_order=sort_order,
                )
                for field_name, input_type, label, sort_order in questions
            ],
        )

        submit_task = AsyncMock()
        with respx.mock:
            respx.get(f"{forms_settings.KOTLIN_BACKEND_URL}/service/forms/{task_form_id}").mock(
                return_value=Response(200, json=form_response)
            )
            with patch("src.conversation.service.submit_task", new=submit_task):
                # 1. A greeting gets the welcome and a one-tap way in.
                welcome = await _say(client, line_user_id, "สวัสดีครับ")
                assert _text(welcome) == messages.WELCOME_BACK
                start_button = _buttons(welcome)["เริ่ม"]

                # 2. Tapping เริ่ม lists the farmer's task.
                task_list = await _say(client, line_user_id, start_button.text)
                task_button = next(
                    action for label, action in _buttons(task_list).items() if TASK_TITLE in label
                )
                assert task_button.data == f"start:{task_id}:{task_form_id}:notes"

                # 3. Picking it asks the first question.
                first_question = await _tap(client, line_user_id, task_button.data)
                assert "ใส่ปุ๋ยอะไร" in _text(first_question)

                # 4. Each answer moves to the next question, then the summary.
                second_question = await _say(client, line_user_id, "ปุ๋ยคอก")
                assert "ใส่กี่กิโลกรัม" in _text(second_question)

                summary = await _say(client, line_user_id, "12.5")
                summary_text = _text(summary)
                assert "ปุ๋ยคอก" in summary_text
                assert "12.5" in summary_text
                confirm_button = next(
                    action for label, action in _buttons(summary).items() if "ยืนยัน" in label
                )

                # Nothing has gone to Go before the farmer confirms.
                submit_task.assert_not_awaited()
                assert (await _conversation(db_session, user_id))["status"] == "active"

                # 5. Confirming saves it.
                saved = await _tap(client, line_user_id, confirm_button.data)

        assert "บันทึกข้อมูลเรียบร้อยแล้ว" in _text(saved)

        # Exactly one submission, for this farmer and task, with exactly what
        # was typed. Values go to Go as text; Go casts each one to its
        # destination column's type (form_handler.go's liveColumns).
        submit_task.assert_awaited_once()
        (submission,) = submit_task.await_args.args
        assert isinstance(submission, TaskSubmission)
        assert submission.user_id == str(user_id)
        assert submission.task_id == str(task_id)
        assert submission.answer["fertilizer_name"] == "ปุ๋ยคอก"
        assert submission.answer["amount_kg"] == "12.5"

        conversation = await _conversation(db_session, user_id)
        assert conversation["status"] == "completed"
        # Every answer kept, each marked as typed in reply to its question.
        stored = (
            await db_session.execute(
                text(
                    "SELECT q.field_name, a.answer->>'text', a.source "
                    "FROM chat.conversation_answer a "
                    "JOIN form.question q ON q.question_id = a.question_id "
                    "WHERE a.conversation_id = :cid ORDER BY q.sort_order"
                ),
                {"cid": conversation["conversation_id"]},
            )
        ).all()
        assert [tuple(row) for row in stored] == [
            ("fertilizer_name", "ปุ๋ยคอก", "guided_flow"),
            ("amount_kg", "12.5", "guided_flow"),
        ]
