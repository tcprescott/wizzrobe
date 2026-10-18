"""Match Schedule's "Not booked yet" view: open bracket matchups nobody has booked.

Challonge, start.gg and native brackets each discover matchups that are ready to
play, and only their two players could see them, on My Schedule. This lists
every one the viewer may book (``bracket_sources.list_unbooked``), grouped by
tournament like the players' own pick-a-time card, so staff can chase a booking
or make it for them. A matchup that can't be booked yet says why on the row and
offers the fix instead of Book….
"""

from typing import Awaitable, Callable, Optional

from nicegui import ui

from application.services import bracket_sources
from application.services.bracket_sources import BookableMatch, UnbookedMatch
from theme.dialog.bracket_schedule_dialog import BracketScheduleDialog
from theme.dialog.bracket_source_schedule_dialog import BracketSourceScheduleDialog
from theme.empty_state import empty_state
from theme.menu import menu_action, more_menu
from theme.notify import notify_error
from theme.tables.admin_crud import capture_render_context, current_actor, scoped_background

_STATUS = {'all': 'All', 'ready': 'Ready to book', 'blocked': 'Needs account'}


def _bracket_link(item: UnbookedMatch) -> Optional[str]:
    if item.source == 'bracket' and item.bracket_id:
        return f'/brackets/{item.bracket_id}'
    if item.source == 'startgg':
        return f'/tournament/{item.tournament.id}/startgg'
    if item.source == 'challonge':
        return item.tournament.challonge_tournament_url or item.tournament.bracket_url or None
    return None


def _note(item: UnbookedMatch) -> str:
    if item.unlinked:
        names = ' and '.join(item.unlinked)
        verb = "aren't" if len(item.unlinked) > 1 else "isn't"
        return f'{names} {verb} matched to a Wizzrobe account yet.'
    return item.window_text or 'Either player can book it from My Schedule.'


class UnbookedBoard:
    """Build with ``render`` inside the page; reload with ``refresh``.

    ``on_count`` is told how many matchups are waiting after every load, so the
    board switch can show the number. ``on_booked`` runs after a booking, for
    the booked board to pick up the new match.
    """

    def __init__(
        self,
        *,
        on_count: Callable[[int], None],
        on_booked: Callable[[], Awaitable[None]],
        can_open_native_entrants: bool = False,
    ) -> None:
        self.on_count = on_count
        self.on_booked = on_booked
        self.can_open_native_entrants = can_open_native_entrants
        self.status = 'all'
        self.search = ''
        self._items: list[UnbookedMatch] = []
        self._generation = 0
        self._render_context = capture_render_context()

    async def _load(self) -> list[UnbookedMatch]:
        actor = await current_actor()
        if actor is None:
            return []
        try:
            return await bracket_sources.list_unbooked(actor)
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return []

    def refresh(self) -> None:
        scoped_background(self._render_context, self._reload())

    async def _reload(self) -> None:
        self._generation += 1
        generation = self._generation
        items = await self._load()
        if generation != self._generation:
            return
        self._items = items
        self.on_count(len(items))
        self._rows.refresh()

    async def render(self) -> None:
        self._items = await self._load()
        self.on_count(len(self._items))
        with ui.column().classes('unbooked-board full-width gap-2'):
            with ui.row().classes('unbooked-board__filters items-center no-wrap gap-2 full-width'):
                ui.toggle(
                    _STATUS, value=self.status, on_change=lambda e: self._set_status(e.value),
                ).props('no-caps dense unelevated aria-label="Show"').classes('wiz-segmented')
                ui.input(placeholder='Search players', on_change=lambda e: self._set_search(e.value)) \
                    .props('outlined dense clearable aria-label="Search players"') \
                    .classes('unbooked-board__search col')
            self._rows()

    def _set_status(self, value: str) -> None:
        self.status = value or 'all'
        self._rows.refresh()

    def _set_search(self, value: Optional[str]) -> None:
        self.search = (value or '').strip().lower()
        self._rows.refresh()

    def _visible(self) -> list[UnbookedMatch]:
        out = self._items
        if self.status == 'ready':
            out = [i for i in out if i.ready]
        elif self.status == 'blocked':
            out = [i for i in out if not i.ready]
        if self.search:
            out = [i for i in out if self.search in i.matchup.lower()]
        return out

    @ui.refreshable_method
    def _rows(self) -> None:
        if not self._items:
            empty_state('Every open bracket matchup is booked.')
            return
        visible = self._visible()
        if not visible:
            empty_state('No matchups match these filters.')
            return
        groups: dict[tuple, list[UnbookedMatch]] = {}
        for item in visible:
            groups.setdefault((item.tournament.id, item.source), []).append(item)
        with ui.column().classes('unbooked-board__groups full-width gap-2'):
            for rows in groups.values():
                with ui.element('section').classes('wiz-pick-group unbooked-group'):
                    with ui.row().classes('wiz-pick-group__head items-center no-wrap gap-2'):
                        head = ui.label(f'{rows[0].tournament.name} · {len(rows)}').classes('col min-w-0')
                        head.tag = 'h3'
                        ui.label(rows[0].source_label).classes('wiz-chip wiz-chip--neutral')
                    for item in rows:
                        self._row(item)

    def _row(self, item: UnbookedMatch) -> None:
        with ui.element('div').classes('unbooked-row'):
            with ui.column().classes('unbooked-row__who gap-0 min-w-0'):
                with ui.row().classes('items-center no-wrap gap-2'):
                    ui.label(item.matchup).classes('unbooked-row__matchup')
                    if item.ready:
                        ui.label('Ready to book').classes('wiz-chip wiz-chip--ok unbooked-row__chip')
                    else:
                        ui.label('Needs account').classes('wiz-chip wiz-chip--pending unbooked-row__chip')
                ui.label(_note(item)).classes(
                    'unbooked-row__note' + (' unbooked-row__note--blocked' if not item.ready else '')
                )
            ui.label(item.round_label).classes('unbooked-row__round')
            with ui.row().classes('unbooked-row__actions items-center no-wrap gap-1'):
                if item.ready:
                    ui.button('Book…', on_click=lambda _=None, i=item: self._book(i)) \
                        .props('color=primary no-caps')
                elif self._can_fix(item):
                    ui.button('Fix entrant', on_click=lambda _=None, i=item: self._fix(i)) \
                        .props('outline color=primary no-caps')
                link = _bracket_link(item)
                fixable = self._can_fix(item)
                if link or (item.ready and fixable):
                    with more_menu(item.matchup):
                        if link:
                            external = not link.startswith('/')
                            menu_action(
                                'open_in_new' if external else 'account_tree', 'Open bracket',
                                lambda _=None, url=link, ext=external: ui.navigate.to(url, new_tab=ext),
                            )
                        if item.ready and fixable:
                            menu_action('manage_accounts', 'Match entrants…',
                                        lambda _=None, i=item: self._fix(i))

    def _can_fix(self, item: UnbookedMatch) -> bool:
        return item.source in ('challonge', 'startgg') or self.can_open_native_entrants

    async def _after_booking(self) -> None:
        self.refresh()
        await self.on_booked()

    async def _book(self, item: UnbookedMatch) -> None:
        actor = await current_actor()
        if actor is None:
            ui.notify('Log in again to book this.', color='warning')
            return
        player_ids = [pid for pid in item.player_ids if pid is not None]
        if item.source == 'bracket':
            await BracketScheduleDialog(
                item.key, actor,
                matchup_label=item.matchup,
                game_number=item.game_number,
                best_of=item.best_of,
                tournament_name=item.tournament.name,
                round_label=item.round_label,
                tournament_id=item.tournament.id,
                player_ids=player_ids,
                on_submit=self._after_booking,
            ).open()
            return
        bookable = BookableMatch(
            source=item.source, source_label=item.source_label, key=item.key,
            tournament=item.tournament, round_label=item.round_label,
            opponent_name=item.matchup, player_ids=player_ids, window_text=item.window_text,
        )
        await BracketSourceScheduleDialog(
            bookable, actor, on_submit=self._after_booking, matchup_label=item.matchup,
        ).open()

    async def _fix(self, item: UnbookedMatch) -> None:
        if item.source == 'challonge':
            from theme.dialog.challonge_participants_dialog import ChallongeParticipantsDialog
            await ChallongeParticipantsDialog(item.tournament, on_change=self._reload).open()
        elif item.source == 'startgg':
            from theme.dialog.startgg_entrants_dialog import StartggEntrantsDialog
            await StartggEntrantsDialog(item.tournament, on_change=self._reload).open()
        elif self.can_open_native_entrants:
            from application.utils.app_links import admin_url
            ui.navigate.to(admin_url('brackets'))

