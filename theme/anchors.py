"""Fragment links (``#…``) to a section, a match or a shift on any page.

Three kinds of target, each addressable by a link built here:

* a panel or nested section heading: ``#<title as a slug>`` (``section_panel``
  and nested ``page_header`` give themselves the id);
* a match row or card on any match board: ``#match-<id>``;
* a shift block on an hour grid: ``#shift-<id>``.

Rows and blocks carry ``data-wiz-row`` / ``data-wiz-shift`` rather than an
``id``, because the same match can sit on two boards of one page and an id must
be unique. ``static/js/anchors.js`` resolves the fragment, waiting for the
target to exist: most of a page arrives over the socket after the browser has
already given up on its own scroll-to-fragment.
"""

import re

from nicegui import ui

from theme.menu import menu_action

__all__ = ['anchor_id', 'copy_link_button', 'copy_link_item', 'match_anchor', 'shift_anchor']


def anchor_id(title: str) -> str:
    """``"Your matches"`` → ``your-matches``: the id a titled section answers to."""
    return re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-') or 'section'


def match_anchor(match_id: int) -> str:
    return f'match-{match_id}'


def shift_anchor(shift_id: int) -> str:
    return f'shift-{shift_id}'


def copy_link_button(title: str) -> ui.button:
    """The link icon after a section heading: copies this page's address at the
    section. Shown when the heading is pointed at or focused, and faintly always
    on a touch screen, which has no hover (styles.css ``.wiz-copy-link``).

    The copy happens in ``static/js/anchors.js``, inside the click, because
    Safari refuses a clipboard write that arrives later over the socket. It
    links to the nearest id above it, so a caller that renames the panel's id
    still gets a working link.
    """
    button = ui.button(icon='link', color=None).props('flat round dense').classes('wiz-copy-link')
    button.props['aria-label'] = f'Copy link to {title}'
    button.props['data-wiz-copy-link'] = ''
    button.tooltip('Copy link')
    return button


def copy_link_item(fragment: str) -> ui.menu_item:
    """A ⋮ menu's "Copy link" item, for a row that ``#fragment`` lands on."""
    item = menu_action('link', 'Copy link', None)
    item.props['data-wiz-copy-link'] = fragment
    return item
