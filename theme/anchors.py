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

__all__ = ['anchor_id', 'match_anchor', 'shift_anchor']


def anchor_id(title: str) -> str:
    """``"Your matches"`` → ``your-matches``: the id a titled section answers to."""
    return re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-') or 'section'


def match_anchor(match_id: int) -> str:
    return f'match-{match_id}'


def shift_anchor(shift_id: int) -> str:
    return f'shift-{shift_id}'
