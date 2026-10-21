"""A titled block on a page that stacks several of them.

My Schedule puts four independent things on one scroll — the matches you are
playing, the crew slots you signed up for, the hours you told staff you can play,
and the gear you are still holding. Rendered as bare headings joined by hairline
separators they read as one continuous document with no obvious boundary, while
each section's own ``page-container`` + ``header-row`` + ``separator-spacing``
stacked roughly four ems of dead space between a title and the content under it.
The result was ~3,500px of desktop scroll in which nothing looked like a thing.

A panel gives each block one visible edge, one compact header, and one padding
rule. ``aside`` is the header's right-hand slot for the section's own controls
(a scope switch, a column-preferences gear) so they stop floating above the
content they belong to.
"""

from typing import NamedTuple, Optional, Sequence

from nicegui import ui

from models import User
from theme.anchors import anchor_id
from theme.help import HelpTopic, help_button, resolve_help

__all__ = ['SectionPanel', 'section_panel']


class SectionPanel(NamedTuple):
    """The three slots a caller fills in."""

    panel: ui.element
    #: Header's right-hand slot, for this section's own controls.
    aside: ui.row
    #: The content.
    body: ui.column


async def section_panel(
    title: str,
    *,
    icon: str,
    subtitle: str = '',
    help_topics: Sequence[HelpTopic] = (),
    user: Optional[User] = None,
) -> SectionPanel:
    """Render one section's chrome and hand back its slots.

    ``help_topics`` become the header's one help button, at the far end of the
    title row. Six labelled chips beside "Your matches" wrapped to two rows on a
    phone; one "?" opening a list of the six does not.

    Pass ``user`` where the caller already has one: role-gated topics need it,
    and without it each one looks the viewer up again.
    """
    topics = await resolve_help(help_topics, user=user)
    # Every panel is linkable as ``#<its title>`` (theme/anchors.py); a caller
    # that needs a fixed id sets its own on ``panel`` afterwards.
    panel = ui.element('section').classes('wiz-panel').props(f'id={anchor_id(title)}')
    with panel:
        with ui.element('div').classes('wiz-panel__head'):
            with ui.row().classes('wiz-panel__head-row items-center no-wrap'):
                ui.icon(icon).classes('wiz-panel__icon')
                ui.label(title).classes('wiz-panel__title')
                aside = ui.row().classes('wiz-panel__aside items-center')
                help_button(topics)
            if subtitle:
                ui.label(subtitle).classes('wiz-panel__sub')
        body = ui.column().classes('wiz-panel__body')
    return SectionPanel(panel=panel, aside=aside, body=body)
