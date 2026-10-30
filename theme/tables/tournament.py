from nicegui import app, ui
from tortoise.functions import Count

from application.content import parse_blocks
from application.services import ChallongeService, bracket_sources, get_user_from_session
from application.tenant_context import get_current_tenant_id
from application.utils.safe_url import http_url_or_empty
from application.utils.timezone import format_local_display, format_local_short, timezone_label
from application.utils.tournament_signup import SignupWindow, signup_window_state
from theme.busy import busy
from theme.dialog import TournamentDialog
from theme.dialog.challonge_participants_dialog import ChallongeParticipantsDialog
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.dialog.link_bracket_dialog import open_link_bracket_dialog
from theme.dialog.round_schedule_dialog import RoundScheduleDialog
from theme.dialog.startgg_entrants_dialog import StartggEntrantsDialog
from theme.dialog.tournament_edit_dialog import confirm_tournament_delete
from theme.dialog.tournament_players_dialog import TournamentPlayersDialog
from theme.empty_state import no_data_slot
from theme.menu import SlotHeader, SlotItem, menu_action, slot_more_menu
from theme.notify import notify_error
from theme.tables.admin_crud import capture_render_context, refresh_button, scoped_background
from theme.tables.export import csv_export_menu_item
from theme.tables.mobile_grid import apply_column_visibility
from theme.tables.preferences import (
    customize_table,
    preferences_button,
    search_input,
    sticky_header,
)

_SIGNUP_LABELS = {
    SignupWindow.UPCOMING: 'Not open yet',
    SignupWindow.OPEN: 'Open',
    SignupWindow.CLOSED: 'Closed',
}

_SIGNUP_TONES = {
    SignupWindow.UPCOMING: 'pending',
    SignupWindow.OPEN: 'live',
    SignupWindow.CLOSED: 'neutral',
}


def _signup_label(tournament) -> str:
    """The signup column's cell, with the boundary that produced it.

    Bare "Open" leaves staff unsure whether a window is set at all, so a
    tournament with a bound names the date it moves on.
    """
    state = signup_window_state(tournament.signups_open_at, tournament.signups_close_at)
    label = _SIGNUP_LABELS[state]
    boundary = (
        tournament.signups_open_at if state is SignupWindow.UPCOMING
        else tournament.signups_close_at
    )
    return f'{label} — {format_local_display(boundary)}' if boundary else label


def _signup_detail(tournament) -> str:
    """The line beside the signup chip: when the window next moves, or that it won't.

    Short stamps: the board states its zone once, in the caption over the rows.
    """
    state = signup_window_state(tournament.signups_open_at, tournament.signups_close_at)
    if state is SignupWindow.UPCOMING:
        return f'Opens {format_local_short(tournament.signups_open_at)}'
    if state is SignupWindow.CLOSED:
        return f'Closed {format_local_short(tournament.signups_close_at)}'
    if tournament.signups_close_at:
        return f'Closes {format_local_short(tournament.signups_close_at)}'
    return 'No close date'


def _match_length(tournament) -> str:
    """Average and longest match as one cell: ``90 min (max 120)``."""
    avg, longest = tournament.average_match_duration, tournament.max_match_duration
    if avg and longest:
        return f'{avg} min (max {longest})'
    if avg:
        return f'{avg} min'
    if longest:
        return f'max {longest} min'
    return ''


def _description_preview(text: str | None) -> str:
    """The description as one line of plain text, for a table cell."""
    if not text:
        return ''
    lines = []
    for block in parse_blocks(text):
        for spans in (block.spans, *block.items):
            line = ''.join(span.text for span in spans).strip()
            if line:
                lines.append(line)
    return ' '.join(lines)


def _bracket_site(t, live_sites) -> str:
    """The key of the live bracket site this tournament follows, or ''."""
    return next((s.key for s in live_sites if s.linked(t)), '')


def _bracket_fields(t, live_sites, can_sync=False, can_link=False) -> dict:
    """The Bracket column and the row ⋮'s bracket items for one tournament.

    ``can_sync`` covers Sync, the Challonge stage step and Entrants, which a
    tournament admin may run on their own rows; ``can_link`` (staff only)
    covers linking and unlinking.
    """
    site = _bracket_site(t, live_sites)
    label = next((s.label for s in live_sites if s.key == site), '')
    stage = ''
    synced_at = None
    if site == 'challonge':
        stage = ChallongeService.stage_label_for(t) or ''
        synced_at = t.challonge_last_synced_at
    elif site == 'startgg':
        synced_at = t.startgg_last_synced_at
    action = next(iter(ChallongeService.stage_actions_for(t)), None) if site == 'challonge' else None
    return {
        'bracket_site': site,
        'bracket_label': label,
        'bracket_stage': stage,
        'bracket_synced': f'Synced {format_local_short(synced_at)}' if synced_at else 'Never synced',
        'bracket_manage': bool(site) and can_sync,
        'bracket_unlinkable': bool(site) and can_link,
        'bracket_linkable': not site and can_link and bool(live_sites),
        'stage_action': action.key if action else '',
        'stage_action_label': action.label if action else '',
    }


def _tournament_row(t, live_sites=(), can_sync=False, can_link=False, unmatched=None) -> dict:
    state = signup_window_state(t.signups_open_at, t.signups_close_at)
    n = (unmatched or {}).get(t.id, 0)
    return {
        'unmatched_label': f'{n} unmatched' if n else '',
        **_bracket_fields(t, live_sites, can_sync, can_link),
        'round_schedule': bool(getattr(t, 'challonge_tournament_id', None) or getattr(t, 'startgg_event_slug', None)),
        'id': t.id,
        'name': t.name,
        'tournament_format': t.tournament_format or '',
        'description': _description_preview(t.description),
        'seed_generator': t.seed_generator or '',
        'is_active': t.is_active,
        'is_test': t.is_test,
        'players_per_match': t.players_per_match,
        'player_count': t.player_count,
        'average_match_duration': t.average_match_duration,
        'max_match_duration': t.max_match_duration,
        'match_length': _match_length(t),
        'signups': _signup_label(t),
        'signup_state': _SIGNUP_LABELS[state],
        'signup_tone': _SIGNUP_TONES[state],
        'signup_detail': _signup_detail(t),
        'bracket_url': http_url_or_empty(t.bracket_url),
        'rules_url': http_url_or_empty(t.rules_url),
    }


_NAME_CELL = '''
    <a href="#" class="table-link" @click.prevent="$parent.$emit('edit_tournament', props.row)">{{ props.row.name }}</a>
    <span v-if="!props.row.is_active" class="wiz-chip wiz-chip--neutral q-ml-sm">Inactive</span>
    <span v-if="props.row.is_test" class="wiz-chip wiz-chip--pending q-ml-sm">Test</span>
    <div class="tournament-table__sub">{{ props.row.tournament_format || 'No format set' }}</div>
'''

_SIGNUPS_CELL = '''
    <span :class="'wiz-chip wiz-chip--' + props.row.signup_tone">{{ props.row.signup_state }}</span>
    <div class="tournament-table__sub tournament-table__stamp">{{ props.row.signup_detail }}</div>
'''

_SIGNUPS_LINE = '''
    <span :class="'wiz-chip wiz-chip--' + props.row.signup_tone">{{ props.row.signup_state }}</span>
    <span class="tournament-table__sub q-ml-xs">{{ props.row.signup_detail }}</span>
'''

_SEED_CELL = '''
    <span v-if="props.row.seed_generator" class="tournament-table__mono">{{ props.row.seed_generator }}</span>
    <span v-else class="tournament-table__unset">Not set</span>
'''

_LENGTH_CELL = '''
    <template v-if="props.row.average_match_duration">
        {{ props.row.average_match_duration }} min
        <div v-if="props.row.max_match_duration" class="tournament-table__sub">max {{ props.row.max_match_duration }} min</div>
    </template>
    <span v-else-if="props.row.max_match_duration">max {{ props.row.max_match_duration }} min</span>
    <span v-else class="tournament-table__unset">Not set</span>
'''

_BRACKET_CELL = '''
    <template v-if="props.row.bracket_site">
        <span class="wiz-chip wiz-chip--neutral">{{ props.row.bracket_label }}</span>
        <span v-if="props.row.bracket_stage" class="q-ml-xs">{{ props.row.bracket_stage }}</span>
        <span v-if="props.row.unmatched_label" class="wiz-chip wiz-chip--pending q-ml-xs">{{ props.row.unmatched_label }}</span>
        <div class="tournament-table__sub tournament-table__stamp">{{ props.row.bracket_synced }}</div>
    </template>
    <span v-else class="tournament-table__unset">Not linked</span>
'''

_PLAYERS_CELL = '''
    <a href="#" class="table-link" :aria-label="props.row.player_count + ' players in ' + props.row.name"
       @click.prevent="$parent.$emit('show_players', props.row)">{{ props.row.player_count }}</a>
'''

_FACTS_LINE = '''
    <a href="#" class="table-link" :aria-label="props.row.player_count + ' players in ' + props.row.name"
       @click.prevent="$parent.$emit('show_players', props.row)">{{ props.row.player_count }} {{ props.row.player_count === 1 ? 'player' : 'players' }}</a>
    <span class="tournament-grid-card__dot">·</span>
    <span v-if="props.row.seed_generator" class="tournament-table__mono">{{ props.row.seed_generator }}</span>
    <span v-else class="tournament-table__unset">No seed</span>
    <span class="tournament-grid-card__dot">·</span>
    <span v-if="props.row.match_length">{{ props.row.match_length }}</span>
    <span v-else class="tournament-table__unset">No match length</span>
'''

# Sort match length by the average minutes, not the cell's text ("100 min"
# sorts before "60 min" alphabetically). Unset lengths sink to the bottom.
_LENGTH_SORT = (
    '(a, b, rowA, rowB) => '
    '(rowA.average_match_duration ?? -1) - (rowB.average_match_duration ?? -1)'
)


def tournament_columns() -> list[dict]:
    """The Tournaments board's columns: seven visible, the rest offered in the gear.

    The description, the active flag and players-per-match are still here for
    anyone who wants them back, but hidden by default: the description was the
    column that made every row three lines tall and pushed the table sideways.
    """
    return [
        {'name': 'id', 'label': 'ID', 'field': 'id', 'hidden': True},
        {'name': 'name', 'label': 'Tournament', 'field': 'name', 'sortable': True,
         'align': 'left', 'classes': 'tournament-table__name-cell'},
        # Not sortable: the cell is a state plus the date it moves on, and sorting
        # that string is neither chronological nor state order.
        {'name': 'signups', 'label': 'Signups', 'field': 'signups', 'align': 'left'},
        {'name': 'bracket', 'label': 'Bracket', 'field': 'bracket_label',
         'sortable': True, 'align': 'left'},
        {'name': 'seed_generator', 'label': 'Seed', 'field': 'seed_generator',
         'sortable': True, 'align': 'left'},
        {'name': 'match_length', 'label': 'Match length', 'field': 'match_length',
         'sortable': True, ':sort': _LENGTH_SORT, 'align': 'left'},
        {'name': 'player_count', 'label': 'Players', 'field': 'player_count',
         'sortable': True, 'align': 'right'},
        {'name': 'tournament_format', 'label': 'Format', 'field': 'tournament_format',
         'align': 'left', 'hidden': True},
        {'name': 'description', 'label': 'Description', 'field': 'description',
         'align': 'left', 'hidden': True},
        {'name': 'is_active', 'label': 'Active', 'field': 'is_active', 'sortable': True,
         'align': 'left', 'hidden': True},
        {'name': 'players_per_match', 'label': 'Players per match',
         'field': 'players_per_match', 'sortable': True, 'align': 'right', 'hidden': True},
        {'name': 'actions', 'label': '', 'field': 'id', 'align': 'right'},
    ]


class TournamentTableView:
    """Encapsulates the tournament table UI and logic for admin/player dashboards."""
    def __init__(self, columns, get_query, extra_slots=None, submit_tournament_callback=None,
                 table_key=None, searchable=False, can_delete=False, can_sync=False,
                 can_link=False):
        self.columns = columns
        self.can_sync = can_sync
        # Offer Link a bracket… and Unlink bracket…; the services keep these staff-only.
        self.can_link = can_link
        self._live_sites: list = []
        self._unmatched: dict = {}
        self.table_key = table_key
        # A text box over the visible columns. Not persisted: working state.
        self.searchable = searchable
        # Offer Delete in the row menu. The service still refuses non-staff.
        self.can_delete = can_delete
        self._plan = None
        self.get_query = get_query
        self.extra_slots = extra_slots
        self.submit_tournament_callback = submit_tournament_callback
        self.table = None
        # Capture the tenant while the request context is live; background tasks
        # (the initial load, pagination refreshes) run detached from the client
        # slot where the tenant stash lives, so scoped reads would otherwise raise
        # require_tenant_id(). _bg rebinds it — same pattern as MatchTableView.
        self._tenant_id = get_current_tenant_id()
        self._render_context = capture_render_context()
        self._setup_ui()

    def _bg(self, coro) -> None:
        """Schedule ``coro`` with this view's tenant *and* display zone rebound."""
        scoped_background(self._render_context, coro)

    def _more_menu(self, extra_class: str = '') -> str:
        # Two groups: what Wizzrobe keeps for this tournament, then what talks
        # to the site its bracket lives on, headed by that site's name.
        items: list = [
            SlotHeader('Tournament'),
            SlotItem('edit', 'Edit tournament…', 'edit_tournament'),
            SlotItem('group', 'Players…', 'show_players'),
            SlotItem('event_note', 'Round schedule…', 'round_schedule',
                     v_if='props.row.round_schedule'),
            SlotItem('open_in_new', 'Open rules', 'open_rules', v_if='props.row.rules_url',
                     href='props.row.rules_url'),
            SlotItem('open_in_new', 'Open bracket', 'open_bracket',
                     v_if='!props.row.bracket_site && props.row.bracket_url', href='props.row.bracket_url'),
        ]
        if self.can_link:
            items.append(SlotItem('add_link', 'Link a bracket…', 'link_bracket',
                                  v_if='props.row.bracket_linkable'))
        items += [
            SlotHeader('{{ props.row.bracket_label }} bracket',
                       v_if='props.row.bracket_site && (props.row.bracket_manage || props.row.bracket_url)'),
        ]
        if self.can_sync:
            items += [
                SlotItem('sync', 'Sync now', 'sync_bracket', v_if='props.row.bracket_manage'),
                # Challonge offers at most one stage step from any state, so the
                # label rides on the row.
                SlotItem('skip_next', '{{ props.row.stage_action_label }}…', 'stage_action',
                         v_if='props.row.bracket_manage && props.row.stage_action'),
                SlotItem('link', 'Match entrants to members…', 'bracket_entrants',
                         v_if='props.row.bracket_manage', badge='props.row.unmatched_label'),
            ]
        items.append(SlotItem('open_in_new', 'Open on {{ props.row.bracket_label }}', 'open_bracket',
                              v_if='props.row.bracket_site && props.row.bracket_url',
                              href='props.row.bracket_url'))
        if self.can_link:
            items.append(SlotItem('link_off', 'Unlink {{ props.row.bracket_label }} bracket…',
                                  'unlink_bracket', negative=True, v_if='props.row.bracket_unlinkable'))
        if self.can_delete:
            # Only an inactive tournament can be deleted; the service enforces it too.
            items.append(SlotItem('delete_forever', 'Delete tournament…', 'delete_tournament',
                                  negative=True, v_if='!props.row.is_active'))
        return slot_more_menu('props.row.name', items, extra_class=extra_class)

    def _setup_ui(self):
        with ui.row().classes('full-width items-center wiz-toolbar tournament-toolbar'):
            if self.submit_tournament_callback:
                ui.button('Add tournament', icon='add',
                          on_click=self.submit_tournament_callback).props('color=primary').classes('wiz-write')
            ui.space()
            # Filled after the table exists; the toolbar is drawn first. The
            # search box is its own toolbar item so a phone can move it, with
            # refresh, onto a second line under the buttons.
            search_slot = ui.element('div').classes('tournament-toolbar__search')
            tools_slot = ui.row().classes('items-center no-wrap gap-1')
            refresh_button(self.refresh).classes('tournament-toolbar__refresh')

        with ui.column().classes('full-width'):
            self.table = ui.table(
                columns=self.columns,
                rows=[],
                row_key='id',
                # Client-side paging over the rows already loaded — see the note
                # in theme/tables/match.py; there is no pagination handler.
                pagination={'rowsPerPage': 25, 'page': 1},
            ).classes('tournament-table tournament-table-container').props(':grid="Quasar.Screen.lt.md"')
        # This table wires its own :grid prop rather than going through
        # enable_mobile_grid, so it has to honour `hidden` itself.
        apply_column_visibility(self.table, self.columns)
        self.table.add_slot('no-data', no_data_slot('No tournaments yet.'))
        self.table.add_slot('body-cell-name', f'<q-td :props="props">{_NAME_CELL}</q-td>')
        self.table.add_slot('body-cell-signups', f'<q-td :props="props">{_SIGNUPS_CELL}</q-td>')
        self.table.add_slot('body-cell-seed_generator', f'<q-td :props="props">{_SEED_CELL}</q-td>')
        self.table.add_slot('body-cell-match_length', f'<q-td :props="props">{_LENGTH_CELL}</q-td>')
        self.table.add_slot('body-cell-bracket', f'<q-td :props="props">{_BRACKET_CELL}</q-td>')
        self.table.add_slot('body-cell-player_count', f'<q-td :props="props">{_PLAYERS_CELL}</q-td>')
        self.table.add_slot('body-cell-actions', f'<q-td :props="props">{self._more_menu()}</q-td>')
        self.table.add_slot('body-cell-description', '''<q-td :props="props">
            <span v-if="props.value" class="wrap" :title="props.value">
                {{ props.value.length > 120 ? props.value.substring(0, 117) + '...' : props.value }}
            </span>
            <span v-else class="tournament-table__unset">Not set</span>
        </q-td>''')
        self.table.add_slot('body-cell-is_active', '''<q-td :props="props">
            <span :class="props.value ? 'wiz-chip wiz-chip--ok' : 'wiz-chip wiz-chip--neutral'">{{ props.value ? 'Active' : 'Inactive' }}</span>
        </q-td>''')
        # The phone card carries the same facts and the same ⋮ as a desktop
        # row, so the two widths never offer different things: one line each
        # for signups and bracket, then players, seed and length on one line.
        self.table.add_slot('item', f'''
        <div class="tournament-grid-card" style="width: 100%; box-sizing: border-box;">
            <div class="tournament-grid-card__head">
                <div class="tournament-grid-card__title">{_NAME_CELL}</div>
                {self._more_menu('tournament-grid-card__more')}
            </div>
            <div class="tournament-grid-card__status">
                <div class="tournament-grid-card__label">Signups</div>
                <div>{_SIGNUPS_LINE}</div>
                <div class="tournament-grid-card__label">Bracket</div>
                <div>{_BRACKET_CELL}</div>
            </div>
            <div class="tournament-grid-card__facts">{_FACTS_LINE}</div>
        </div>
        ''')
        if self.extra_slots:
            for slot_name, slot_template in self.extra_slots.items():
                self.table.add_slot(slot_name, slot_template)
        if self.table_key:
            # **After** the item slot above, never before: the card is generated
            # from these columns at build time and must not follow a saved
            # layout. Pinned by tests/theme/test_table_preferences.py.
            self._plan = customize_table(self.table, self.columns, key=self.table_key)
            zone = timezone_label()
            with self.table.parent_slot:
                # Cells carry short stamps with no zone, so the board names it once.
                ui.label().classes('text-caption text-grey-7').bind_text_from(
                    self.table, 'rows',
                    backward=lambda rows: f'{len(rows or []):,} tournaments · times in {zone}',
                ).move(target_index=0)
            sticky_header(self.table)
            if self.searchable:
                with search_slot:
                    search_input(self.table, placeholder='Search tournaments…', width='w-full')
            with tools_slot:
                with ui.button('Actions').props('flat dense color=primary icon-right=expand_more no-caps').classes('q-px-sm wiz-write'):
                    with ui.menu().props('anchor="bottom right" self="top right"'):
                        if self.can_sync:
                            menu_action('sync', 'Sync all linked brackets', self.sync_all)
                        csv_export_menu_item(
                            'tournaments',
                            lambda: self._plan.columns if self._plan else self.columns,
                            lambda: self.table.rows,
                        )
                preferences_button(self.table)
        self.table.on('edit_tournament', self.handle_edit_tournament)
        self.table.on('show_players', self.handle_show_players)
        self.table.on('sync_bracket', self.handle_sync_bracket)
        self.table.on('stage_action', self.handle_stage_action)
        self.table.on('bracket_entrants', self.handle_bracket_entrants)
        self.table.on('link_bracket', self.handle_link_bracket)
        self.table.on('unlink_bracket', self.handle_unlink_bracket)
        self.table.on('round_schedule', self.handle_round_schedule)
        self.table.on('delete_tournament', self.handle_delete_tournament)
        # Initial load at build: correctness must not depend on the selected_tab
        # broadcast (which only fires on a *subsequent* tab switch) or on Quasar's
        # incidental pagination event.
        self._bg(self.refresh())

    async def refresh(self, *_, **__):
        # Count enrolled players in SQL rather than hydrating every enrollment
        # row just to len() it.
        tournament_query = self.get_query().annotate(player_count=Count('players'))
        all_tournaments = await tournament_query.order_by('name')
        self._live_sites = await bracket_sources.live_sources()
        self._unmatched = await bracket_sources.unmatched_entrant_counts(all_tournaments)
        self.table.rows = [self._row(t) for t in all_tournaments]
        self.table.update()

    async def update_row_by_id(self, tournament_id):
        """
        Update a single row in the table by its tournament ID, only if the row is currently visible.
        """
        idx = next((i for i, row in enumerate(self.table.rows) if row.get('id') == tournament_id), None)
        if idx is None:
            return
        tournament_query = self.get_query().annotate(player_count=Count('players'))
        t = await tournament_query.filter(id=tournament_id).first()
        if not t:
            return
        self._unmatched.update({t.id: 0, **await bracket_sources.unmatched_entrant_counts([t])})
        idx = next((i for i, row in enumerate(self.table.rows) if row.get('id') == tournament_id), None)
        if idx is None:
            return  # A reload replaced the rows while the query ran
        self.table.rows[idx] = self._row(t)
        self.table.update()

    def _row(self, t) -> dict:
        return _tournament_row(t, self._live_sites, self.can_sync, self.can_link, self._unmatched)

    @staticmethod
    def _event_row(event) -> dict:
        args = event.args if hasattr(event, 'args') else event
        return args['row'] if isinstance(args, dict) and 'row' in args else args

    async def _load(self, event):
        t = await self.get_query().filter(id=self._event_row(event)['id']).first()
        if not t:
            ui.notify("We couldn't find that tournament. It may have been removed.", color='warning')
        return t

    async def handle_edit_tournament(self, event):
        t = await self._load(event)
        if not t:
            return
        # Both callbacks repaint the table: an edit can change any visible column,
        # and a delete leaves a row pointing at a tournament that no longer exists.
        dialog = TournamentDialog(t, on_submit=self.refresh, on_delete=self.refresh)
        await dialog.open()

    async def handle_show_players(self, event):
        t = await self._load(event)
        if not t:
            return
        # The dialog writes now, so the player-count column has to follow it.
        dialog = TournamentPlayersDialog(t, on_change=self.refresh)
        await dialog.open()

    async def handle_round_schedule(self, event):
        t = await self._load(event)
        if not t:
            return
        await RoundScheduleDialog(t).open()

    async def handle_sync_bracket(self, event):
        t = await self._load(event)
        if not t:
            return
        actor = await get_user_from_session(app.storage.user)
        try:
            async with busy(None, f'Syncing {t.name}…'):
                result = await bracket_sources.sync_tournament(t, actor)
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        except Exception as e:
            ui.notify(f"Couldn't sync {t.name}: {e}", color='negative')
            return
        ui.notify(f'Synced {result.tournament_name} from {result.source_label}: {result.summary}.',
                  color='positive')
        await self.update_row_by_id(t.id)

    async def handle_stage_action(self, event):
        t = await self._load(event)
        if not t:
            return
        service = ChallongeService()
        action = next(iter(service.stage_actions_for(t)), None)
        if action is None:
            ui.notify(f'{t.name} has no stage to move on to. Sync it to pick up changes made on Challonge.',
                      color='warning')
            return
        if not await service.can_change_stages():
            ui.notify('Reconnect Challonge under Settings → Integrations to let Wizzrobe change '
                      'tournament stages.', color='warning')
            return

        async def do_advance() -> None:
            actor = await get_user_from_session(app.storage.user)
            try:
                await service.advance_stage(t.id, action.key, actor)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            ui.notify(f'{t.name}: {action.label.lower()} done.', color='positive')
            await self.update_row_by_id(t.id)

        ConfirmationDialog(
            action.confirm, on_confirm=do_advance, confirm_text=action.label,
            tone='primary', title=action.label, busy_text='Updating Challonge…',
        ).open()

    async def handle_bracket_entrants(self, event):
        t = await self._load(event)
        if not t:
            return
        site = _bracket_site(t, self._live_sites)
        # Each match or clear changes the row's unmatched count.
        on_change = lambda: self.update_row_by_id(t.id)
        if site == 'challonge':
            await ChallongeParticipantsDialog(t, on_change=on_change).open()
        elif site == 'startgg':
            await StartggEntrantsDialog(t, on_change=on_change).open()

    async def handle_link_bracket(self, event):
        t = await self._load(event)
        if not t:
            return
        open_link_bracket_dialog(t, self._live_sites, on_linked=lambda: self.update_row_by_id(t.id))

    async def handle_unlink_bracket(self, event):
        t = await self._load(event)
        if not t:
            return
        site = _bracket_site(t, self._live_sites)
        if site == 'challonge':
            message = (f'Unlink “{t.name}” from Challonge? Its mirrored entrants and bracket '
                       'matches are removed here. The bracket on Challonge is untouched, and '
                       'linking it again re-imports it. Scheduled matches stay as they are.')
        else:
            message = (f'Unlink “{t.name}” from start.gg? Its pools and bracket are removed '
                       'here and players can no longer book from it. The event on start.gg is '
                       'untouched, and scheduled matches stay as they are.')

        async def do_unlink() -> None:
            actor = await get_user_from_session(app.storage.user)
            if actor is None:
                return
            try:
                label = await bracket_sources.unlink(t, actor)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            ui.notify(f'{t.name} unlinked from {label}.', color='positive')
            await self.update_row_by_id(t.id)

        ConfirmationDialog(message, on_confirm=do_unlink, confirm_text='Unlink',
                           title='Unlink bracket?').open()

    async def sync_all(self):
        # The menu closes as it's tapped, so nothing stops a second tap
        # starting a second run over the same brackets.
        if getattr(self, '_syncing_all', False):
            return
        self._syncing_all = True
        try:
            await self._sync_all()
        finally:
            self._syncing_all = False

    async def _sync_all(self):
        actor = await get_user_from_session(app.storage.user)
        tournaments = await self.get_query().order_by('name')
        async with busy(None, 'Syncing brackets…') as state:
            results = await bracket_sources.sync_all(
                list(tournaments), actor, on_progress=lambda name: state.say(f'Syncing {name}…'))
        if not results:
            ui.notify('No tournaments here are linked to a bracket site.', color='warning')
            return
        ok = [r for r in results if r.error is None]
        failed = [r for r in results if r.error is not None]
        message = f"Synced {len(ok)} bracket{'' if len(ok) == 1 else 's'}"
        if ok:
            message += ': ' + ', '.join(f'{r.tournament_name} ({r.source_label})' for r in ok)
        message += '.'
        if failed:
            message += ' Failed: ' + '; '.join(f'{r.tournament_name}: {r.error}' for r in failed)
        ui.notify(message, color='warning' if failed else 'positive', multi_line=True)
        await self.refresh()

    async def handle_delete_tournament(self, event):
        t = await self._load(event)
        if not t:
            return
        await confirm_tournament_delete(t, on_deleted=self.refresh)

