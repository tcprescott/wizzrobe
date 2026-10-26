"""The in-app changelog at ``/changelog``.

**Anonymous-readable**, like ``/help``: it describes the app, not a community's
data, so there is nothing on it a signed-out visitor should not see. Entries come
from ``application/changelog/`` (``changelog.md`` plus the per-PR ``changes/``
files), grouped within each week by kind of change, and render through the same
safe block renderer as help, never NiceGUI's markdown element. It has no menu
item of its own: the Help index links it, and it links back.
"""

from nicegui import app, ui

from application.changelog import ChangelogEntry, ChangelogSection, all_entries
from application.services import TenantService, get_user_from_session
from middleware.auth import public_page
from theme.base import BaseLayout
from theme.empty_state import empty_panel
from theme.help.render import render_blocks
from theme.page_header import page_header

__all__ = ['create']

#: Each category's dot takes a status colour, so the groups read apart at a glance.
_CATEGORY_TONES = {
    'New': 'info',
    'Improved': 'ok',
    'Fixed': 'pending',
    'Security': 'cancelled',
}


def _section(section: ChangelogSection) -> None:
    tone = _CATEGORY_TONES.get(section.category, 'neutral')
    with ui.element('h3').classes('wiz-help-h3 wiz-changelog-category'):
        ui.element('span').classes(f'wiz-changelog-dot wiz-changelog-dot--{tone}') \
            .props('aria-hidden=true')
        ui.label(section.category).classes('wiz-help-inline')
        ui.label(str(section.count)).classes('wiz-changelog-count')
    render_blocks(section.blocks)


def _entry(entry: ChangelogEntry) -> None:
    with ui.element('div').classes('wiz-help-prose wiz-changelog-entry'):
        heading = ui.element('h2').classes('wiz-help-h2') \
            .props(f'id={entry.anchor}')
        with heading:
            ui.label(entry.title).classes('wiz-help-inline')
        ui.label(entry.period).classes('wiz-changelog-period text-caption text-grey-7')
        for section in entry.sections:
            _section(section)


def create() -> None:
    @public_page('/changelog', scope='community')
    async def changelog() -> None:
        community = await TenantService.current_community_name() or 'Wizzrobe'
        ui.page_title(f"What's new — {community}")
        user = await get_user_from_session(app.storage.user)
        await BaseLayout(user=user).render()

        with ui.column().classes('page-container-narrow w-full'):
            ui.link('← All help', '/help').classes('text-caption')
            page_header(
                "What's new",
                'The bigger changes to Wizzrobe, about a week at a time, newest first.',
            )

            entries = all_entries()
            if not entries:
                empty_panel('Nothing to show yet.', icon='new_releases')
            # Weeks as cards, two across on a wide screen: one column of prose
            # capped at a readable measure left most of a 1200px page empty.
            with ui.element('div').classes('wiz-tile-grid wiz-tile-grid--md'):
                for entry in entries:
                    _entry(entry)
