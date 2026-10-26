from datetime import time
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

# A form's reminder settings and the recipient pickers (FORM-08..11). Same
# keys as web-backend's Reminder.Request.Create, Reminder.Detail,
# RoleOption and UserOption, which the web app's settings screen reads.


class _Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class RecipientIn(_Camel):
    """ROLE = everyone holding that role (id is a role_id); USER = one person."""

    type: Literal["ROLE", "USER"]
    id: UUID


class FormReminderIn(_Camel):
    """A daily reminder at this time; no recipients = everyone who still owes
    the task. There is no cadence: the reminder job only knows daily."""

    enabled: bool
    time_of_day: time
    recipients: list[RecipientIn] = []


class RecipientDetailOut(_Camel):
    type: Literal["ROLE", "USER"]
    id: UUID
    label: str  # the role name or username, for the screen


class FormReminderOut(_Camel):
    schedule_id: UUID | None
    enabled: bool
    time_of_day: time | None
    recipients: list[RecipientDetailOut]


class RoleOptionOut(_Camel):
    role_id: UUID
    role_name: str


class UserOptionOut(_Camel):
    user_id: UUID
    username: str
    roles: list[str]
