"""The About section: ``/about``, ``/about/privacy`` and ``/about/notices``.

**Anonymous-readable**, like ``/help`` and ``/changelog``: it describes the app,
not a community's data. ``/about`` is a hub (what Wizzrobe is, its licence and
build, the "built with Claude" note) with tiles out to the privacy policy and the
third-party notices, which each have a page of their own so either can be linked
on its own. The Help index is the way in; there is no menu item.
"""

from nicegui import app, ui

from application.about import PRIVACY_UPDATED, Notice, all_notices, find_notice, privacy_blocks
from application.content import parse_blocks
from application.services import TenantService, get_user_from_session
from application.utils.build_info import build_info
from middleware.auth import public_page
from theme.a11y import make_activatable
from theme.banner import banner
from theme.base import BaseLayout
from theme.dialog._helpers import form_dialog
from theme.help.render import render_blocks
from theme.page_header import page_header

__all__ = ['create', 'link_card']

_INTRO = (
    'Wizzrobe runs the schedule for speedrun and randomizer events: matches, brackets, crew, '
    'volunteers, equipment and check-in, with Discord sign-in and notifications. Each community '
    'gets its own space, run by its own staff.\n\n'
    "It's free software under the [GNU General Public License v3](https://www.gnu.org/licenses/gpl-3.0.html), "
    'and the source is on [GitHub](https://github.com/tcprescott/wizzrobe).'
)


def link_card(icon: str, title: str, summary: str, target: str) -> None:
    """A tile that opens ``target``: the Help index's way into About, and About's tiles."""
    with make_activatable(ui.card().classes('wiz-help-card cursor-pointer')
                          .on('click', lambda: ui.navigate.to(target)), role='link'):
        with ui.row().classes('items-center no-wrap gap-3'):
            ui.icon(icon).props('size=sm').classes('text-primary')
            with ui.column().classes('gap-0 col min-w-0'):
                ui.label(title).classes('text-weight-bold')
                ui.label(summary).classes('text-caption text-muted')


async def _frame(*title: str) -> None:
    community = await TenantService.current_community_name() or 'Wizzrobe'
    ui.page_title(' — '.join((*title, community)))
    user = await get_user_from_session(app.storage.user)
    await BaseLayout(user=user).render()


def _build_line() -> None:
    info = build_info()
    with ui.row().classes('wiz-about-build items-center gap-4'):
        ui.label(f'Version {info.version}')
        if info.commit_short:
            ui.link(f'Commit {info.commit_short}', info.source_url, new_tab=True)


def _notice_dialog(notice: Notice) -> None:
    with form_dialog(notice.name, card_classes='dialog-card dialog-card--lg') as dialog:
        with ui.column().classes('gap-2 w-full'):
            with ui.row().classes('items-center gap-2'):
                if notice.version:
                    ui.label(notice.version).classes('wiz-about-version')
                ui.label(notice.license).classes('wiz-about-licence')
                if notice.url:
                    ui.link('Project page', notice.url, new_tab=True)
            text = ui.label(notice.reading_text).classes('wiz-about-licence-text w-full') \
                .props('tabindex=0 aria-label="Licence text"')
            text.tag = 'pre'
    dialog.open()


def _open_notice(key: str) -> None:
    notice = find_notice(key)
    if notice is not None:
        _notice_dialog(notice)


def _notice_row(notice: Notice) -> None:
    with make_activatable(ui.element('div').classes('wiz-about-row')
                          .on('click', lambda key=notice.key: _open_notice(key))):
        ui.label(notice.name).classes('wiz-about-name')
        if notice.version:
            ui.label(notice.version).classes('wiz-about-version')
        ui.space()
        ui.label(notice.license).classes('wiz-about-licence')


def create() -> None:
    @public_page('/about', scope='community')
    async def about() -> None:
        await _frame('About')
        with ui.column().classes('page-container-narrow w-full'):
            page_header('About Wizzrobe', 'The app behind this community\'s schedule.')
            with ui.card().classes('w-full wiz-about-intro'):
                with ui.element('div').classes('wiz-help-prose'):
                    render_blocks(parse_blocks(_INTRO))
                with banner(
                    "Much of Wizzrobe's code was written with Claude, an AI model made by "
                    'Anthropic. A person reviews every change before it ships, but if something '
                    "looks wrong, tell us through Send Feedback or your community's staff.",
                    title='Built with Claude', tone='info', icon='smart_toy',
                ):
                    pass
                _build_line()

            with ui.element('div').classes('wiz-tile-grid wiz-tile-grid--md w-full'):
                link_card('privacy_tip', 'Privacy policy',
                          'What we keep when you sign in and use the app, who sees it, and how to have '
                          'it removed.', '/about/privacy')
                count = sum(len(group.notices) for group in all_notices())
                link_card('description', 'Third-party notices',
                          f'The {count} open-source libraries, fonts and icons Wizzrobe ships, with their '
                          'licences.', '/about/notices')
                link_card('new_releases', "What's new", 'The bigger changes, a week at a time.',
                          '/changelog')
                link_card('help', 'Help', 'Guides for players, crew, volunteers and staff.', '/help')

    @public_page('/about/privacy', scope='community')
    async def privacy() -> None:
        await _frame('Privacy policy')
        with ui.column().classes('page-container-narrow w-full'):
            ui.link('← About', '/about').classes('text-caption')
            page_header('Privacy policy', f'Last updated {PRIVACY_UPDATED:%B} {PRIVACY_UPDATED.day}, {PRIVACY_UPDATED.year}')
            with ui.element('div').classes('wiz-help-prose'):
                render_blocks(privacy_blocks())

    @public_page('/about/notices', scope='community')
    async def notices() -> None:
        await _frame('Third-party notices')
        with ui.column().classes('page-container-narrow w-full'):
            ui.link('← About', '/about').classes('text-caption')
            page_header(
                'Third-party notices',
                'Wizzrobe ships the open-source software below, and each keeps its own licence. '
                'Pick one to read its licence and copyright notice.',
            )
            for group in all_notices():
                with ui.card().classes('w-full wiz-about-group'):
                    with ui.row().classes('items-baseline gap-2'):
                        heading = ui.label(group.heading).classes('section-title')
                        heading.tag = 'h2'
                        ui.label(str(len(group.notices))).classes('text-muted')
                    ui.label(group.blurb).classes('text-caption text-muted')
                    with ui.element('div').classes('wiz-about-list w-full'):
                        for notice in group.notices:
                            _notice_row(notice)
