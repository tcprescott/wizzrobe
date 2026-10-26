"""The "Past events" line that ends a personal page.

Personal pages follow a person across every live event, so an archived event's
rows drop off them. This line is the way back: one link per past event, to
``path`` in that event.
"""

from nicegui import ui

from application.services import EventService
from middleware.event_routing import event_href, page_request
from models import User


async def render_past_events(user: User | None, path: str) -> None:
    """Nothing when the community has no archived event, or off a page request."""
    request = page_request()
    past = [e for e in await EventService.visible_events(user) if e.is_archived]
    if request is None or not past:
        return
    with ui.row().classes('wiz-past-events items-center no-wrap gap-2'):
        ui.icon('history', size='xs')
        with ui.row().classes('items-center gap-1'):
            ui.label('Past events:')
            for i, event in enumerate(past):
                if i:
                    ui.label('·')
                ui.link(event.name, event_href(request, event.slug, path))
