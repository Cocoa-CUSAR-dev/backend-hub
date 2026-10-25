from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

# Same keys as web-backend's Diary.Entity and Diary.AnswerField, which the web
# app's history page reads today.


class _CamelOut(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class AnswerFieldOut(_CamelOut):
    """One answered field of a day, as the farmer submitted it (an OPTION
    answer is still its id here; the diary is where names appear)."""

    field_name: str
    label: str
    input_type: str
    raw_value: str


class DiaryOut(_CamelOut):
    user_id: UUID
    entry_date: date
    diary_text: str
    updated_at: datetime
