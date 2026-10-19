"""Shared pieces of the community events surfaces: chips, time lines, the disclaimer."""

from datetime import datetime, timezone

from nicegui import ui

from application.services._community_event_notifications import format_duration
from application.services.community_event_service import ends_at
from application.utils.timezone import format_local_time, format_local_time_range, to_local
from models import CommunityEvent, CommunityEventStatus
from theme.banner import banner

DISCLAIMER_TITLE = 'Not official event programming'
DISCLAIMER = (
    'These events are run by attendees, not by event staff. Listing one '
    "here doesn't mean staff endorse it; the listing is just for convenience."
)

STATUS_CHIPS: dict[CommunityEventStatus, tuple[str, str]] = {
    CommunityEventStatus.PENDING: ('Waiting for review', 'pending'),
    CommunityEventStatus.APPROVED: ('Listed', 'ok'),
    CommunityEventStatus.REJECTED: ('Not listed', 'cancelled'),
    CommunityEventStatus.CANCELLED: ('Cancelled', 'neutral'),
}


def disclaimer() -> None:
    with banner(DISCLAIMER, title=DISCLAIMER_TITLE, tone='info', icon='campaign'):
        pass


def chip(text: str, tone: str) -> None:
    with ui.element('span').classes(f'wiz-chip wiz-chip--{tone}'):
        ui.label(text)


def status_chip(event: CommunityEvent) -> None:
    chip(*STATUS_CHIPS[event.status])


def is_live(event: CommunityEvent) -> bool:
    now = datetime.now(timezone.utc)
    return event.starts_at <= now < ends_at(event)


def day_label(event: CommunityEvent) -> str:
    local = to_local(event.starts_at) or event.starts_at
    return f'{local:%A}, {local:%B} {local.day}'


def when_line(event: CommunityEvent, *, with_day: bool = False) -> str:
    """``14:00–15:30 EDT · 1h 30m``, prefixed with the day when asked."""
    line = f'{format_local_time_range(event.starts_at, ends_at(event))} · {format_duration(event.duration_minutes)}'
    return f'{day_label(event)} · {line}' if with_day else line


def meta_row(icon: str, text: str) -> None:
    with ui.row().classes('items-center gap-2 no-wrap'):
        ui.icon(icon).props('size=xs').classes('text-muted')
        ui.label(text).classes('wiz-community-event__meta')


def time_tile(event: CommunityEvent) -> None:
    """A listing card's left-hand block: the start time, and how long it runs."""
    with ui.column().classes('wiz-ce-tile'):
        ui.label(format_local_time(event.starts_at)).classes('wiz-ce-tile__main')
        ui.label(format_duration(event.duration_minutes)).classes('wiz-ce-tile__small')


def date_tile(event: CommunityEvent) -> None:
    """The detail page's block: weekday, day of the month, month."""
    local = to_local(event.starts_at) or event.starts_at
    with ui.column().classes('wiz-ce-tile wiz-ce-tile--date'):
        ui.label(f'{local:%a}').classes('wiz-ce-tile__small')
        ui.label(str(local.day)).classes('wiz-ce-tile__main')
        ui.label(f'{local:%b}').classes('wiz-ce-tile__small')
