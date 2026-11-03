"""Schemas for your own notification endpoints: tournaments you follow and every message."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from application.services.notification_preference_service import MyNotificationRow, MyNotifications
from models import NotificationMode, NotificationPreset, NotificationRecipient

_LEVEL_DESC = (
    "Which of the tournament's matches you hear about. One of: none (nothing), streamed "
    "(matches assigned to a stream stage), streamed_and_candidates (those plus matches "
    "marked as possible streams), all (every scheduled match)."
)


class NotificationPreferenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="The preference's ID.")
    tournament_id: int = Field(..., description="The tournament this preference follows.")
    match_notifications: str = Field(..., description=_LEVEL_DESC)
    created_at: datetime = Field(..., description="When the preference was first set (UTC).")
    updated_at: datetime = Field(..., description="When the preference was last changed (UTC).")


class NotificationPreferenceUpdate(BaseModel):
    tournament_id: int = Field(
        ..., description="The tournament to follow. You don't need to be signed up for it.",
    )
    match_notifications: str = Field(..., description=_LEVEL_DESC)


class MyNotificationResponse(BaseModel):
    notice: str = Field(..., description="The message's key, e.g. `match_scheduled`.")
    recipient: NotificationRecipient = Field(..., description='Which of your roles this row is about: `players`, `crew`, `watchers`…')
    section: str = Field(..., description='Where Profile → Notifications lists it, e.g. `Your matches`.')
    title: str = Field(..., description='What the message is.')
    when: str = Field(..., description='What sends it.')
    community: NotificationMode = Field(..., description="How your community sends it. Your choice can't be louder than this.")
    preset: NotificationMode = Field(..., description='What your preset asks for.')
    override: Optional[NotificationMode] = Field(None, description='What you chose for this one message, if anything.')
    mode: NotificationMode = Field(..., description='How it reaches you: the quieter of `community` and your choice.')
    changed: bool = Field(..., description='Whether you chose something other than your preset.')
    opt_in: bool = Field(..., description='Off in every preset; you only get it if you turned it on.')

    @classmethod
    def from_row(cls, row: MyNotificationRow) -> 'MyNotificationResponse':
        return cls(
            notice=row.target.key, recipient=row.target.recipient, section=row.section,
            title=row.notice.title, when=row.notice.when, community=row.community,
            preset=row.preset, override=row.override, mode=row.effective, changed=row.changed,
            opt_in=row.opt_in,
        )


class MyNotificationsResponse(BaseModel):
    preset: NotificationPreset = Field(
        ..., description='Your starting point: `everything`, `needs_me` (ping only when you have '
        'something to do) or `quiet` (ping only what can\'t wait).',
    )
    changed: int = Field(..., description='How many messages you changed from the preset.')
    messages: List[MyNotificationResponse]

    @classmethod
    def from_mine(cls, mine: MyNotifications) -> 'MyNotificationsResponse':
        return cls(
            preset=mine.preset, changed=mine.changed,
            messages=[MyNotificationResponse.from_row(r) for r in mine.rows],
        )


class MyNotificationPresetUpdate(BaseModel):
    preset: NotificationPreset = Field(..., description='`everything`, `needs_me` or `quiet`. Your per-message choices stay.')


class MyNotificationUpdate(BaseModel):
    mode: Optional[NotificationMode] = Field(
        ..., description='`off`, `silent` or `ping`; `null` goes back to your preset.',
    )
