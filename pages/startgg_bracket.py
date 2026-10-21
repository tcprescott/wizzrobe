"""The start.gg bracket page: a linked event's pools and bracket, read-only.

**Anonymous-readable** like the native bracket pages: player names are public,
and nothing here changes anything. One tab per start.gg phase (layout A): a
round-robin phase is a grid of pool cards, each with standings and a Results
fold; an elimination phase is drawn as round columns that scroll sideways on a
phone. The signed-in viewer's own rows are marked, and a match of theirs that is
ready to book links to the booking picker on My Schedule.
"""

from typing import Any, Dict, List, Optional

from nicegui import app, ui

from application.services import StartggService, TenantService, get_user_from_session
from application.utils.app_links import player_startgg_schedule_url
from application.utils.route_params import parse_route_id
from application.utils.timezone import format_local_display
from middleware.auth import public_page
from models import FeatureFlag, StartggSet, StartggSetState
from theme.assets import asset_url
from theme.base import BaseLayout
from theme.empty_state import empty_panel
from theme.error_page import render_not_found
from theme.page_header import page_header
from theme.url_state import bind_url


def _name(entrant, viewer_id: Optional[int]) -> str:
    if entrant is None:
        return ''
    if viewer_id is not None and entrant.user is not None and entrant.user.id == viewer_id:
        return f'{entrant.name} (you)'
    return entrant.name


def _is_mine(row: StartggSet, viewer_id: Optional[int]) -> bool:
    return viewer_id is not None and any(
        e is not None and e.user is not None and e.user.id == viewer_id
        for e in (row.entrant1, row.entrant2)
    )


def _slot(row: StartggSet, slot: int, viewer_id: Optional[int]) -> None:
    entrant = row.entrant1 if slot == 1 else row.entrant2
    score = row.score1 if slot == 1 else row.score2
    won = row.state == StartggSetState.COMPLETE and entrant is not None and row.winner_id == entrant.id  # type: ignore[attr-defined]
    with ui.row().classes('sgg-slot no-wrap items-center' + (' is-winner' if won else '')):
        label = _name(entrant, viewer_id) or StartggService.entrant_label(row, slot)
        ui.label(label).classes('sgg-slot__name' + ('' if entrant else ' sgg-slot__name--tbd'))
        if row.state == StartggSetState.COMPLETE and score is not None:
            ui.label(str(score)).classes('sgg-slot__score')


def _status(row: StartggSet, bookable: bool, viewer_id: Optional[int]) -> None:
    if row.state == StartggSetState.COMPLETE:
        ui.label('Final').classes('wiz-chip wiz-chip--ok')
    elif row.match_id is not None:  # type: ignore[attr-defined]
        when = getattr(row.match, 'scheduled_at', None)
        ui.label(f'Booked {format_local_display(when)}' if when else 'Booked').classes('wiz-chip wiz-chip--live')
    elif row.state == StartggSetState.OPEN and bookable:
        if _is_mine(row, viewer_id):
            ui.link('Pick a time', player_startgg_schedule_url(row.id)).classes('sgg-book')
        elif all(e is not None and e.user is not None for e in (row.entrant1, row.entrant2)):
            ui.label('Ready to book').classes('wiz-chip wiz-chip--pending')


def _set_card(row: StartggSet, bookable: bool, viewer_id: Optional[int]) -> None:
    classes = 'sgg-set' + (' is-mine' if _is_mine(row, viewer_id) else '')
    with ui.element('div').classes(classes):
        with ui.row().classes('sgg-set__head items-center no-wrap'):
            if row.identifier:
                ui.label(row.identifier).classes('sgg-set__id')
            ui.space()
            _status(row, bookable, viewer_id)
        _slot(row, 1, viewer_id)
        _slot(row, 2, viewer_id)


def _cell(tag: str, text: str, cls: str = '') -> None:
    cell = ui.element(tag).classes(cls)
    if tag == 'th':
        cell.props('scope=col')
    with cell:
        ui.label(text)


def _render_pool_grid(pools: List[Dict[str, Any]], bookable: bool, viewer_id: Optional[int]) -> None:
    with ui.element('div').classes('sgg-pool-grid'):
        for view in pools:
            pool = view['pool']
            with ui.card().classes('sgg-pool'):
                with ui.row().classes('items-center w-full no-wrap'):
                    title = ui.label(f'Pool {pool.identifier}').classes('section-title')
                    title.tag = 'h3'
                    ui.space()
                    if pool.complete:
                        ui.label('Done').classes('wiz-chip wiz-chip--ok')
                    else:
                        ui.label(f"{view['played']} of {view['total']} played").classes('text-caption text-muted')
                if not view['standings']:
                    ui.label('No standings yet.').classes('text-caption text-muted')
                else:
                    with ui.element('table').classes('sgg-standings'):
                        with ui.element('thead'), ui.element('tr'):
                            _cell('th', '#', 'n')
                            _cell('th', 'Player')
                            _cell('th', 'W–L', 'r')
                        with ui.element('tbody'):
                            for standing in view['standings']:
                                entrant = standing['entrant']
                                mine = viewer_id is not None and entrant.user is not None and entrant.user.id == viewer_id
                                with ui.element('tr').classes('is-mine' if mine else ''):
                                    _cell('td', str(standing['placement'] or ''), 'n')
                                    _cell('td', _name(entrant, viewer_id))
                                    _cell('td', f"{standing['wins']}–{standing['losses']}", 'r')
                if view['sets']:
                    with ui.expansion('Results').classes('w-full sgg-results').props('dense'):
                        for row in view['sets']:
                            _set_card(row, bookable, viewer_id)


def _render_rounds(pools: List[Dict[str, Any]], bookable: bool, viewer_id: Optional[int]) -> None:
    for view in pools:
        if len(pools) > 1:
            ui.label(f"Pool {view['pool'].identifier}").classes('section-title')
        rounds: Dict[int, List[StartggSet]] = {}
        for row in view['sets']:
            rounds.setdefault(row.round, []).append(row)
        winners = sorted(r for r in rounds if r > 0)
        losers = sorted((r for r in rounds if r < 0), reverse=True)
        for title, order in (('', winners), ('Losers bracket', losers)):
            if not order:
                continue
            if title:
                ui.label(title).classes('section-title')
            with ui.element('div').classes('sgg-rounds').props('tabindex=0 aria-label="Bracket rounds"'):
                for number in order:
                    sets = rounds[number]
                    with ui.column().classes('sgg-round'):
                        ui.label(sets[0].round_text or f'Round {abs(number)}').classes('sgg-round__title')
                        for row in sorted(sets, key=lambda s: (s.identifier or '', s.id)):
                            _set_card(row, bookable, viewer_id)


def _is_round_robin(phase) -> bool:
    return (phase.bracket_type or '').upper() == 'ROUND_ROBIN'


def create() -> None:
    @public_page('/tournament/{tournament_id}/startgg', feature=FeatureFlag.STARTGG)
    async def startgg_bracket(tournament_id: str, phase: Optional[str] = None) -> None:
        user = await get_user_from_session(app.storage.user)
        tid = parse_route_id(tournament_id)
        try:
            view = await StartggService().get_event_view(tid) if tid is not None else None
        except ValueError:
            view = None
        if view is None:
            await render_not_found(user=user)
            return

        tournament = view['tournament']
        community = await TenantService.current_community_name() or 'Wizzrobe'
        ui.page_title(f'{tournament.name}: Bracket · {community}')
        ui.add_head_html(f'<link rel="stylesheet" href="{asset_url("css/startgg.css")}">')
        await BaseLayout(user=user).render()
        viewer_id = user.id if user is not None else None

        synced = view['last_synced_at']
        subtitle = (
            f'From start.gg, updated {format_local_display(synced)}' if synced else 'From start.gg'
        )
        with ui.column().classes('page-container q-mt-md'):
            with page_header(f'{tournament.name}: Bracket', subtitle):
                ui.space()
                ui.button(
                    'Open on start.gg', icon='open_in_new',
                    on_click=lambda: ui.navigate.to(view['event_url'], new_tab=True),
                ).props('flat color=primary no-caps')

            phases = [p for p in view['phases'] if p['pools']]
            if not phases:
                empty_panel('start.gg has no pools or bracket for this event yet.', icon='account_tree')
                return

            default = next((p for p in phases if p['bookable']), phases[-1])
            selected = next((p for p in phases if p['phase'].phase_id == phase), default)
            with ui.tabs(value=selected['phase'].phase_id).classes('sgg-tabs') \
                    .props('align=left no-caps inline-label outside-arrows mobile-arrows') as tabs:
                for p in phases:
                    ui.tab(p['phase'].phase_id, label=p['phase'].name)
            with ui.tab_panels(tabs, value=selected['phase'].phase_id).classes('w-full sgg-panels'):
                for p in phases:
                    with ui.tab_panel(p['phase'].phase_id):
                        if _is_round_robin(p['phase']):
                            _render_pool_grid(p['pools'], p['bookable'], viewer_id)
                        else:
                            _render_rounds(p['pools'], p['bookable'], viewer_id)
            names = {str(p['phase'].phase_id): p['phase'].name for p in phases}
            bind_url('phase', tabs, default=default['phase'].phase_id, choices=names, label=names.get)
