"""The "I'm here" banner at the top of every framed page.

Drawn by :meth:`theme.base.BaseLayout.render` for a volunteer whose shift is
within :data:`CHECK_IN_LEAD` of starting (or under way) and who hasn't said
they've arrived. One tap records the check-in and the banner goes; the record is
the same one a coordinator writes from the schedule. The section re-checks every
minute so a page left open on the Event tab picks the banner up when the window
opens.
"""

import inspect
import logging
from datetime import datetime, timezone
from typing import Any, Callable

from nicegui import app, ui

from application.services import VolunteerArrivalService
from application.tenant_context import get_current_tenant_id, tenant_scope
from application.utils.timezone import format_local_time
from models import User
from theme.banner import banner
from theme.notify import notify_error
from theme.steady import steady_refreshable
from theme.timer_teardown import PageTimer

logger = logging.getLogger(__name__)

__all__ = ['arrival_changed', 'banner_title', 'on_arrival_change', 'render_shift_arrival_banner']

RECHECK_SECONDS = 60
_LISTENERS_KEY = 'shift_arrival_listeners'


def on_arrival_change(callback: Callable[[], Any]) -> None:
    """Redraw ``callback`` on this page when the viewer checks in or undoes it.

    The banner and the My Shifts card both offer "I'm here"; tapping one has to
    update the other without a reload.
    """
    app.storage.client.setdefault(_LISTENERS_KEY, []).append(callback)


async def arrival_changed() -> None:
    for callback in list(app.storage.client.get(_LISTENERS_KEY, [])):
        result = callback()
        if inspect.isawaitable(result):
            await result


def banner_title(position: str, starts_at: datetime, now: datetime) -> str:
    """'Race Proctor at 8:00 AM', or 'started at' once it has."""
    verb = 'started at' if starts_at <= now else 'at'
    return f'{position} {verb} {format_local_time(starts_at)}'


async def render_shift_arrival_banner(user: User | None) -> None:
    """Draw the banner section, which is empty while nothing is due."""
    if user is None:
        return
    service = VolunteerArrivalService()
    tenant_id = get_current_tenant_id()

    @steady_refreshable
    async def section() -> None:
        try:
            with tenant_scope(tenant_id):
                due = await service.awaiting_arrival(user)
        except Exception:
            # Chrome, not content: a failed lookup must not take the page down.
            logger.warning('Shift arrival lookup failed', exc_info=True)
            return
        if not due:
            return
        now = datetime.now(timezone.utc)
        with ui.column().classes('w-full gap-2 q-mb-md') \
                .props('role=region aria-label="Your volunteer shift"'):
            for assignment in due:
                shift = assignment.shift
                position = shift.position.name if shift.position else 'Volunteer shift'
                with banner(
                    "Let your coordinator know you're here.",
                    title=banner_title(position, shift.starts_at, now),
                    tone='live', icon='place',
                ):
                    ui.button(
                        "I'm here", icon='place',
                        on_click=lambda a=assignment.id: arrive(a),
                    ).props('unelevated no-caps color=primary')

    async def arrive(assignment_id: int) -> None:
        try:
            with tenant_scope(tenant_id):
                await service.check_in_self(assignment_id, user)
        except ValueError as e:
            notify_error(e)
            return
        ui.notify("You're checked in. Thanks!", color='positive')
        await arrival_changed()

    await section()
    on_arrival_change(section.refresh)
    PageTimer(RECHECK_SECONDS, section.refresh)
