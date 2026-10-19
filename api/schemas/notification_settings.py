"""Schemas for the community's notification settings (Admin → Notifications)."""

from pydantic import BaseModel, Field

from application.services.notification_settings_service import NotificationSettingRow
from models import NotificationMode, NotificationRecipient


class NotificationSettingResponse(BaseModel):
    notice: str = Field(..., description="The message's key, e.g. `match_scheduled`.")
    group: str = Field(..., description='The area it belongs to, e.g. `Matches`.')
    title: str = Field(..., description='What the message is, as the settings page names it.')
    when: str = Field(..., description='What sends it.')
    recipient: NotificationRecipient = Field(..., description='Which kind of recipient this row is about.')
    recipient_label: str = Field(..., description='The recipient as the settings page names it.')
    who: str = Field('', description='Who that recipient means for this message, when the label alone would mislead.')
    mode: NotificationMode = Field(..., description='`off` sends nothing, `silent` arrives without a ping, `ping` is a normal message.')
    default: NotificationMode = Field(..., description='What it is when nobody has changed it.')
    changed: bool = Field(..., description='Whether this community chose something other than the default.')

    @classmethod
    def from_row(cls, row: NotificationSettingRow) -> 'NotificationSettingResponse':
        return cls(
            notice=row.notice.key, group=row.notice.group, title=row.notice.title,
            when=row.notice.when, recipient=row.recipient, recipient_label=row.recipient.label,
            who=row.notice.who.get(row.recipient, ''), mode=row.mode, default=row.default,
            changed=row.changed,
        )


class NotificationSettingUpdate(BaseModel):
    mode: NotificationMode = Field(
        ..., description='`off`, `silent` or `ping`. Choosing the default clears the override.',
    )


class NotificationSettingsReset(BaseModel):
    reset: int = Field(..., description='How many settings went back to their default.')
