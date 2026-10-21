"""The one way a page or tab opens: title, optional help/actions, optional lede.

Before this, admin tabs opened with a title row and a divider, home tabs with
no title, and the check-in desk with a bare label, so the same app looked built
by three people. The look is ``.wiz-page-header`` in styles.css.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional

from nicegui import ui

from theme.anchors import anchor_id

# Set while a page is rendered as one section of a bigger page (a Settings
# pane holds Appearance and Timezone). The page still calls page_header() the
# same way; it just comes out a level down, so the pane keeps a single title.
_nested: ContextVar[bool] = ContextVar('page_header_nested', default=False)
# Set when a page_header() renders an h1, so a tab that never calls it can be
# given a hidden one (see heading_fallback).
_titled: ContextVar[bool] = ContextVar('page_header_titled', default=False)


@contextmanager
def nested_page_headers() -> Iterator[None]:
    """Render every ``page_header`` inside the block as a section heading."""
    token = _nested.set(True)
    try:
        yield
    finally:
        _nested.reset(token)


def page_title(text: str) -> ui.label:
    """A bare page title, for a page that builds its own header row."""
    heading = ui.label(text).classes('page-title')
    heading.tag = 'h1'
    _titled.set(True)
    return heading


def page_header(title: str, subtitle: Optional[str] = None) -> ui.row:
    """Render the header and return its title row.

    The row is a container: build the page's one ``help_icon``, a status chip or
    right-aligned actions inside ``with page_header(...):`` and they sit on the
    title's line. Give an action group ``ui.space()`` before it to push it to the
    right edge; the help button always moves itself to the very end.
    """
    nested = _nested.get()
    with ui.column().classes('wiz-page-header' + (' wiz-page-header--nested' if nested else '')):
        with ui.row().classes('wiz-page-header__row') as row:
            heading = ui.label(title).classes('section-title' if nested else 'page-title')
            # One h1 per page names it for screen-reader heading navigation.
            heading.tag = 'h2' if nested else 'h1'
            if nested:
                heading.props(f'id={anchor_id(title)}')
            if not nested:
                _titled.set(True)
        if subtitle:
            ui.label(subtitle).classes('wiz-page-header__sub')
    return row


@contextmanager
def heading_fallback(title: str) -> Iterator[None]:
    """Give the content built in the block an h1, hidden, if it brings none.

    Some tabs (the home Event board) open on their controls rather than a
    title; screen-reader users navigate by headings and found none.
    """
    fallback = ui.label(title).classes('wiz-sr-only')
    fallback.tag = 'h1'
    token = _titled.set(False)
    try:
        yield
    finally:
        # A build cancelled because its page went away has already taken the
        # label with it, and deleting it again raises over the cancellation.
        if _titled.get() and not fallback.is_deleted:
            fallback.delete()
        _titled.reset(token)
