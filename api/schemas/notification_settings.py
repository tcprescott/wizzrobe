"""Schemas for the community's notification settings (Admin → Notifications)."""

from typing import Optional

from pydantic import BaseModel, Field

from application.services.notification_catalog import StaffChoice
from application.services.notification_settings_service import NotificationSettingRow
from models import NotificationMode, NotificationRecipient


class NotificationSettingResponse(BaseModel):
    notice: str = Field(..., description="The message's key, e.g. `match_scheduled`.")
    group: str = Field(..., description='The area it belongs to, e.g. `Matches`.')
    section: str = Field('', description='The subheading inside its group, e.g. `Match day`; empty for a group without subheadings.')
    title: str = Field(..., description='What the message is, as the settings page names it.')
    when: str = Field(..., description='What sends it.')
    recipient: NotificationRecipient = Field(..., description='Which kind of recipient this row is about.')
    recipient_label: str = Field(..., description='The recipient as the settings page names it.')
    who: str = Field('', description='Who that recipient means for this message, when the label alone would mislead.')
    mode: NotificationMode = Field(..., description='`off` sends nothing, `silent` arrives without a ping, `ping` is a normal message.')
    default: NotificationMode = Field(..., description='What it is when nobody has changed it.')
    changed: bool = Field(..., description='Whether this community chose something other than the default.')
    staff_choice: Optional[StaffChoice] = Field(
        None,
        description=(
            'On an `on_duty`, `admins` or `staff` row, the one Staff setting the message\'s staff '
            'recipients add up to: `ping_all`, `ping_on_duty`, `silent` or `off`. Null on other rows, '
            'and on a staff row whose recipients are set to a mix no choice matches.'
        ),
    )

    @classmethod
    def from_row(cls, row: NotificationSettingRow) -> 'NotificationSettingResponse':
        return cls(
            notice=row.notice.key, group=row.notice.group, section=row.notice.section,
            title=row.notice.title,
            when=row.notice.when, recipient=row.recipient, recipient_label=row.recipient.label,
            who=row.notice.who.get(row.recipient, ''), mode=row.mode, default=row.default,
            changed=row.changed, staff_choice=row.staff_choice,
        )


class NotificationSettingUpdate(BaseModel):
    mode: NotificationMode = Field(
        ..., description='`off`, `silent` or `ping`. Choosing the default clears the override.',
    )


class StaffNotificationUpdate(BaseModel):
    choice: StaffChoice = Field(
        ...,
        description=(
            '`ping_all` pings whoever is on shift and the admins (all staff when nobody is on '
            'shift); `ping_on_duty` drops the admins but keeps that fallback; `silent` and `off` '
            'apply to every staff recipient. `ping_on_duty` needs a message with an `on_duty` row.'
        ),
    )


class NotificationSettingsReset(BaseModel):
    reset: int = Field(..., description='How many settings went back to their default.')
