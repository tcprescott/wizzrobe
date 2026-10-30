"""The archived-event banner and the read-only look that comes with it.

Thomas picked layout A (2026-10-26): a banner under the page title says the
event is archived and everything is read-only, staff get Unarchive… in it, and
the page's write controls are hidden. Hiding is the courtesy; the refusal is the
ORM guard in ``application/event_writes.py``, so a control this misses answers
with the archived message instead of changing anything.

``BaseLayout`` decides which page or tab shows one event (``half`` on a tab,
the page scope otherwise) and binds :func:`read_only_scope` around it.
``page_header`` then draws the banner under the title, and the container gets
``wiz-read-only``, under which styles.css hides every row's ⋮ (``More for …``)
and anything marked ``wiz-write`` (a toolbar's main button or Actions menu).
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable, Iterator, Optional

from nicegui import ui

from application.services.event_service import EventRef
from models import User
from theme.banner import banner

if TYPE_CHECKING:
    from theme.event_switcher import EventSwitcherState

WRITE_CLASS = 'wiz-write'
READ_ONLY_CLASS = 'wiz-read-only'


@dataclass(frozen=True)
class ArchivedView:
    event: EventRef
    actor: Optional[User] = None


_view: ContextVar[Optional[ArchivedView]] = ContextVar('archived_view', default=None)
_shown: ContextVar[bool] = ContextVar('archived_banner_shown', default=False)


def archived_view(events: 'EventSwitcherState', user: Optional[User]) -> Optional[ArchivedView]:
    """The page's event when it is archived, with the viewer as actor when they may unarchive it."""
    current = events.current
    if current is None or not current.is_archived:
        return None
    return ArchivedView(current, actor=user if events.manages else None)


def page_shows_event() -> bool:
    """A page without tabs shows one event unless its scope is the community's."""
    from middleware.event_routing import PAGE_SCOPE_KEY, page_request
    request = page_request()
    return request is None or request.scope.get(PAGE_SCOPE_KEY, 'event') != 'community'


def tab_shows_event(tab: dict) -> bool:
    """A tab shows the page's event unless its ``half`` says it's the community's or the viewer's own."""
    return tab.get('half', 'event') == 'event'


def watch_event_status(current: Optional[EventRef]) -> None:
    """Reload the page when its event is archived or unarchived, into or out of read-only."""
    if current is None:
        return
    from application.events import event_status_live
    from theme.realtime import register_view

    async def on_change(event_id: int) -> None:
        if event_id == current.id:
            ui.navigate.reload()

    register_view(on_change, channel=event_status_live)


def current_archived_view() -> Optional[ArchivedView]:
    return _view.get()


@contextmanager
def read_only_scope(view: Optional[ArchivedView]) -> Iterator[None]:
    """Bind the archived view (or none) for the content built in the block."""
    view_token = _view.set(view)
    shown_token = _shown.set(False)
    try:
        yield
    finally:
        _shown.reset(shown_token)
        _view.reset(view_token)


def bind_read_only(view: Optional[ArchivedView]) -> None:
    """Bind for the rest of the page build: a page without tabs builds after ``render`` returns."""
    _view.set(view)
    _shown.set(False)


def banner_shown() -> bool:
    return _shown.get()


def banner_message(event: EventRef) -> str:
    if event.pii_purged:
        return ('Everything here is read-only. Emergency contacts and accommodation requests '
                'were deleted 30 days after it was archived.')
    return 'Everything here is read-only.'


def render_archived_banner() -> Optional[ui.element]:
    """Draw the banner for the bound archived event, once; None when there's nothing to say."""
    view = _view.get()
    if view is None or _shown.get():
        return None
    _shown.set(True)
    event = view.event
    with banner(banner_message(event), title=f'{event.name} is archived', tone='warning',
                icon='inventory_2') as actions:
        if view.actor is not None:
            actor = view.actor

            async def reload() -> None:
                ui.navigate.reload()

            ui.button('Unarchive…', on_click=lambda: confirm_unarchive(actor, event, reload)) \
                .props('outline no-caps')
    root = actions.parent_slot.parent if actions.parent_slot else None
    return root


def confirm_unarchive(actor: User, event: EventRef, on_done: Callable[[], Awaitable[None]]) -> None:
    """Ask, then put ``event`` back to live; ``on_done`` runs after it is."""
    from application.services import EventService
    from theme.dialog.confirmation_dialog import ConfirmationDialog
    from theme.notify import notify_error

    async def run() -> None:
        try:
            await EventService.unarchive(actor, event.id)
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        ui.notify(f'{event.name} is live again', color='positive')
        await on_done()

    ConfirmationDialog(
        f'{event.name} goes back to live, and its workers start again.',
        title=f'Unarchive {event.name}?', confirm_text='Unarchive', tone='primary',
        on_confirm=run,
    ).open()
