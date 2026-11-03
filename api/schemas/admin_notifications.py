"""Schemas for the app's Admin → Notifications screen: the settings grid as cards."""

from typing import List, Optional

from pydantic import BaseModel, Field

from application.services.notification_catalog import StaffChoice
from application.services.notification_settings_service import (
    NotificationCard,
    NotificationCell,
    NotificationMessage,
)
from models import NotificationMode


class StaffChoiceOptionResponse(BaseModel):
    choice: StaffChoice
    label: str = Field(..., description='The choice as the Staff dropdown names it for this message.')
    help: str = Field(..., description='One line on who that choice reaches.')


class NotificationCellResponse(BaseModel):
    column: str = Field(
        ...,
        description=(
            'The recipient column: `players`, `crew`, `watchers`, `followers`, `volunteers`, '
            '`members`, or `staff_alerts` for the one Staff setting that folds on shift, admins '
            'and all staff together.'
        ),
    )
    label: str = Field(..., description='The column as the screen names it.')
    who: str = Field('', description='Who it means for this message, when the label alone would mislead.')
    mode: NotificationMode = Field(
        ..., description='What it is now. On the Staff column, the mode its choice sends with (`silent` for a custom mix).',
    )
    default: NotificationMode
    changed: bool = Field(..., description='Whether the community chose something other than the default.')
    staff_choice: Optional[StaffChoice] = Field(
        None, description='Staff column only: the current choice, null for a custom mix.',
    )
    staff_label: str = Field('', description='Staff column only: the current choice as the screen names it (`Custom` for a mix).')
    staff_default: Optional[StaffChoice] = Field(None, description='Staff column only: the default choice.')
    staff_options: List[StaffChoiceOptionResponse] = Field(
        default_factory=list, description='Staff column only: the choices this message offers, in order.',
    )
    staff_mix: str = Field('', description='Staff column only: each staff recipient and its mode, for a custom mix.')

    @classmethod
    def from_cell(cls, cell: NotificationCell) -> 'NotificationCellResponse':
        return cls(
            column=cell.column, label=cell.label, who=cell.who, mode=cell.mode, default=cell.default,
            changed=cell.changed, staff_choice=cell.staff_choice, staff_label=cell.staff_label,
            staff_default=cell.staff_default,
            staff_options=[
                StaffChoiceOptionResponse(choice=o.choice, label=o.label, help=o.help)
                for o in cell.staff_options
            ],
            staff_mix=cell.staff_mix,
        )


class NotificationMessageResponse(BaseModel):
    notice: str = Field(..., description="The message's key, e.g. `match_scheduled`.")
    title: str
    when: str = Field(..., description='What sends it.')
    changed: bool = Field(..., description='Whether any of its cells differs from the default.')
    cells: List[NotificationCellResponse]

    @classmethod
    def from_message(cls, message: NotificationMessage) -> 'NotificationMessageResponse':
        return cls(
            notice=message.key, title=message.title, when=message.when, changed=message.changed,
            cells=[NotificationCellResponse.from_cell(c) for c in message.cells],
        )


class NotificationCardResponse(BaseModel):
    group: str = Field(..., description='The area, e.g. `Matches`.')
    section: str = Field('', description='The subheading inside it, e.g. `Match day`; empty when the group has none.')
    columns: List[str] = Field(..., description='The recipient columns any of its messages use, in display order.')
    messages: List[NotificationMessageResponse]

    @classmethod
    def from_card(cls, card: NotificationCard) -> 'NotificationCardResponse':
        return cls(
            group=card.group, section=card.section, columns=list(card.columns),
            messages=[NotificationMessageResponse.from_message(m) for m in card.messages],
        )


class AdminNotificationsResponse(BaseModel):
    cards: List[NotificationCardResponse]
    changed: int = Field(..., description='How many cells differ from the default, counting a Staff column once.')
