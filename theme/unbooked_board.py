"""Match Schedule's "Not booked yet" view: open bracket matchups nobody has booked.

Challonge, start.gg and native brackets each discover matchups that are ready to
play, and only their two players could see them, on My Schedule. This lists
every one the viewer may book (``bracket_sources.list_unbooked``), grouped by
tournament like the players' own pick-a-time card, so staff can chase a booking
or make it for them. A matchup that can't be booked yet says why on the row and
offers the fix instead of Book….
"""

import asyncio
from typing import Any, Awaitable, Callable, Optional

from nicegui import ui

from application.services import BracketService, UserService, bracket_sources
from application.services.bracket_sources import BookableMatch, UnbookedMatch
from application.utils.timezone import format_local_display
from models import Match
from theme.dialog._helpers import dialog_actions, form_dialog, submit_on_enter
from theme.dialog.bracket_schedule_dialog import BracketScheduleDialog
from theme.dialog.bracket_source_schedule_dialog import BracketSourceScheduleDialog
from theme.empty_state import empty_state
from theme.menu import lazy_more_menu, menu_action, menu_link
from theme.notify import notify_error
from theme.steady import steady_refreshable
from theme.tables.admin_crud import capture_render_context, current_actor, scoped_background
from theme.url_state import url_param, url_state

_STATUS = {'all': 'All', 'ready': 'Ready to book', 'blocked': 'Needs account'}

#: Rows built between yields to the event loop. A few hundred rows built in one
#: pass blocked every user for 2s (SGLMAN-1W).
_ROWS_PER_SLICE = 25


def _bracket_link(item: UnbookedMatch) -> Optional[str]:
    if item.source == 'bracket' and item.bracket_id:
        return f'/brackets/{item.bracket_id}'
    if item.source == 'startgg':
        return f'/tournament/{item.tournament.id}/startgg'
    if item.source == 'challonge':
        return item.tournament.challonge_tournament_url or item.tournament.bracket_url or None
    return None


def _when(match: Match) -> str:
    return format_local_display(match.scheduled_at) if match.scheduled_at else 'No time set'


def _suggestion(item: UnbookedMatch) -> str:
    found = item.same_player_matches
    if len(found) == 1:
        return f'Looks like {_when(found[0])}, already on the schedule with the same players.'
    return f'{len(found)} matches with these players are already on the schedule.'


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
        await self._rows.refresh()

    async def render(self) -> None:
        self._items = await self._load()
        self.on_count(len(self._items))
        with ui.column().classes('unbooked-board full-width gap-2'):
            with ui.row().classes('unbooked-board__filters items-center no-wrap gap-2 full-width'):
                asked = url_state().requested('unbooked')
                if asked in _STATUS:
                    self.status = asked
                status = ui.toggle(
                    _STATUS, value=self.status, on_change=lambda e: self._set_status(e.value),
                ).props('no-caps dense unelevated aria-label="Show"').classes('wiz-segmented')
                self._status_param = url_param(
                    'unbooked', status, default='all', value=self.status, history='replace',
                    on_restore=lambda v: self._restore_status(status, v),
                )
                ui.input(placeholder='Search players', on_change=lambda e: self._set_search(e.value)) \
                    .props('outlined dense clearable aria-label="Search players"') \
                    .classes('unbooked-board__search col')
            await self._rows()

    @staticmethod
    def _restore_status(status: ui.toggle, value: str) -> None:
        status.set_value(value if value in _STATUS else 'all')

    def _set_status(self, value: str) -> None:
        self.status = value or 'all'
        self._rows.refresh()
        self._status_param.set(self.status)

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

    @steady_refreshable
    async def _rows(self) -> None:
        if not self._items:
            empty_state(
                'Nothing waiting to be booked. A matchup shows up here once its bracket '
                'has started and synced and both players are known.'
            )
            return
        visible = self._visible()
        if not visible:
            empty_state('No matchups match these filters.')
            return
        groups: dict[tuple, list[UnbookedMatch]] = {}
        for item in visible:
            groups.setdefault((item.tournament.id, item.source), []).append(item)
        built = 0
        with ui.column().classes('unbooked-board__groups full-width gap-2'):
            for rows in groups.values():
                with ui.element('section').classes('wiz-pick-group unbooked-group'):
                    with ui.row().classes('wiz-pick-group__head items-center no-wrap gap-2'):
                        ui.label(f'{rows[0].tournament.name} · {len(rows)}').classes('col min-w-0')
                        ui.label(rows[0].source_label).classes('wiz-chip wiz-chip--neutral')
                    for item in rows:
                        self._row(item)
                        built += 1
                        if built % _ROWS_PER_SLICE == 0:
                            await asyncio.sleep(0)

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
                if item.same_player_matches:
                    with ui.row().classes('unbooked-row__suggestion items-center no-wrap gap-2'):
                        ui.icon('link').classes('unbooked-row__suggestion-icon')
                        ui.label(_suggestion(item)).classes('col min-w-0')
                        ui.button('Link', on_click=lambda _=None, i=item: self._link(i)) \
                            .props('color=primary no-caps dense').classes('q-px-sm')
            ui.label(item.round_label).classes('unbooked-row__round')
            suggested = bool(item.same_player_matches)
            with ui.row().classes('unbooked-row__actions items-center no-wrap gap-1'):
                if item.ready and not suggested:
                    ui.button('Book…', on_click=lambda _=None, i=item: self._book(i)) \
                        .props('color=primary no-caps')
                elif not item.ready and self._can_fix(item):
                    ui.button('Fix entrant', on_click=lambda _=None, i=item: self._fix(i)) \
                        .props('outline color=primary no-caps')
                if _bracket_link(item) or item.ready:
                    lazy_more_menu(item.matchup, lambda i=item: self._menu_items(i))

    def _menu_items(self, item: UnbookedMatch) -> None:
        suggested = bool(item.same_player_matches)
        link = _bracket_link(item)
        if suggested:
            menu_action('event', 'Book a new match…', lambda _=None, i=item: self._book(i))
        if item.ready:
            menu_action('link', 'Link an existing match…', lambda _=None, i=item: self._link(i))
        if link:
            external = not link.startswith('/')
            menu_link('open_in_new' if external else 'account_tree', 'Open bracket',
                      link, new_tab=external)
        if item.ready and self._can_fix(item):
            menu_action('manage_accounts', 'Match entrants…', lambda _=None, i=item: self._fix(i))

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

    async def _link(self, item: UnbookedMatch) -> None:
        """Pick the hand-made match that is really this matchup's, and record it."""
        found = item.same_player_matches
        with form_dialog('Link an existing match', card_classes='dialog-card dialog-card--sm') as dialog:
            with ui.column().classes('q-pa-md gap-2 full-width'):
                ui.label(
                    f'{item.matchup} · {item.round_label}. Pick the match that was already '
                    'scheduled for it. Its result will count for this matchup.'
                ).classes('text-caption text-grey-7')
                if not found:
                    ui.label(
                        'No unconfirmed match with these two players is on the schedule. '
                        "Fix a match's players with Edit match first, or book a new one."
                    ).classes('unbooked-link__empty')
                    picker = None
                else:
                    options = {
                        m.id: f"{_when(m)} · {m.stage.name if m.stage else 'No stage'}"
                        for m in found
                    }
                    picker = ui.radio(options, value=found[0].id).props('dense').classes('unbooked-link__options')

            async def save() -> None:
                if picker is None or picker.value is None:
                    dialog.close()
                    return
                actor = await current_actor()
                if actor is None:
                    ui.notify('Log in again to link this.', color='warning')
                    return
                try:
                    await bracket_sources.link_existing(item.source, item.key, int(picker.value), actor)
                except (ValueError, PermissionError) as e:
                    with dialog:
                        notify_error(e)
                    return
                ui.notify(f'Linked {item.matchup} to its match.', color='positive')
                dialog.close()
                await self._after_booking()

            if picker is not None:
                with dialog_actions().classes('justify-end'):
                    ui.button('Cancel', on_click=dialog.close).props('flat')
                    ui.button('Link match', icon='link', on_click=save).props('color=primary')
                submit_on_enter(dialog, save)
        dialog.open()

    async def _fix(self, item: UnbookedMatch) -> None:
        if item.source == 'challonge':
            from theme.dialog.challonge_participants_dialog import ChallongeParticipantsDialog
            await ChallongeParticipantsDialog(item.tournament, on_change=self._reload).open()
        elif item.source == 'startgg':
            from theme.dialog.startgg_entrants_dialog import StartggEntrantsDialog
            await StartggEntrantsDialog(item.tournament, on_change=self._reload).open()
        elif self.can_open_native_entrants:
            await self._link_native_entrants(item)

    async def _link_native_entrants(self, item: UnbookedMatch) -> None:
        """Link a native matchup's unlinked entrants to members, as Brackets › Manage does."""
        people = await UserService().get_community_people()
        names = {u.id: u.preferred_name or u.username for u in people}
        with form_dialog(f'Match entrants — {item.matchup}', card_classes='dialog-card dialog-card--sm') as dialog:
            pickers: list[tuple[int, Any]] = []
            with ui.column().classes('q-pa-md gap-2 full-width'):
                ui.label(
                    'Pick the member each entrant is. Once both are linked, the matchup can be booked.'
                ).classes('text-caption text-grey-7')
                for entrant_id, name in item.unlinked_entrants:
                    picker = ui.select(names, label=name, with_input=True) \
                        .props('outlined dense clearable').classes('full-width')
                    pickers.append((entrant_id, picker))

            async def save() -> None:
                actor = await current_actor()
                chosen = [(eid, p.value) for eid, p in pickers if p.value]
                if not chosen:
                    ui.notify('Pick a member first.', color='warning')
                    return
                try:
                    for entrant_id, user_id in chosen:
                        await BracketService().set_entrant_user(actor, entrant_id, int(user_id))
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                ui.notify('Entrants linked.', color='positive')
                dialog.close()
                await self._reload()

            with dialog_actions().classes('justify-end'):
                ui.button('Cancel', on_click=dialog.close).props('flat')
                ui.button('Save', icon='link', on_click=save).props('color=primary')
            submit_on_enter(dialog, save)
        dialog.open()

