"""Public bracket view pages (native brackets, unit B11).

**Anonymous-readable.** Both routes are :func:`public_page`, not
``protected_page``: a bracket is the spectator-facing artefact of a tournament,
so a link to it has to work for someone who has never signed in. The feature
flag and the tenant gate still apply; only the login requirement is lifted, and
every staff affordance stays behind ``is_staff`` (``False`` when ``user`` is
``None``).

Read-only presentation for a tournament's bracket stages: a per-tournament index
listing every stage and a per-bracket detail that renders each stage by its
format —

* single / double elimination: a column-per-round card view (winners bracket,
  plus a separate losers-bracket section for double elim), each match a card
  showing both entrant display names with the winner highlighted;
* round robin: per-group standings tables plus a results list;
* swiss: a standings table plus per-round results.

Presentation only: reads through :class:`BracketService`, does read-only
load-or-404 ORM lookups for the owning tournament, and computes live display
standings with the pure :func:`compute_standings` helper. No ORM writes.
"""

import asyncio
import html
from typing import Callable, Dict, List, Optional

from nicegui import app, background_tasks, context, ui
from starlette.responses import RedirectResponse

from application.services import (
    AuthService,
    BracketService,
    TenantService,
    get_user_from_session,
)
from application.services.bracket_engines.standings import (
    compute_standings,
    standings_config_from,
)
from application.tenant_context import require_tenant_id, tenant_scope
from application.utils.route_params import parse_route_id
from middleware.auth import public_page
from middleware.event_routing import page_request, row_event_redirect
from models import (
    Bracket,
    BracketEntry,
    BracketEntryStatus,
    BracketFormat,
    BracketMatch,
    BracketMatchState,
    BracketState,
    FeatureFlag,
    Tournament,
)
from theme.assets import asset_url
from theme.base import BaseLayout
from theme.brackets import (
    assign_match_numbers,
    build_context,
    build_match_dialog,
    config_summary,
    entry_avatars,
    entry_records,
    format_label,
    match_nodes,
    register_bracket_view,
    render_elimination,
    render_elimination_mobile,
    results_from_matches,
    stage_label,
    state_color,
    state_label,
)
from theme.brackets.render import booked_times, viewer_entries
from theme.brackets.tables import render_crosstable, render_pairings, render_standings
from theme.empty_state import empty_panel
from theme.error_page import render_not_found
from theme.page_header import page_title
from theme.steady import steady_refreshable
from theme.url_state import bind_url


def _slot_label(
    entry_id: Optional[int], entry_name: Dict[int, str], *, completed: bool
) -> str:
    if entry_id is not None:
        return entry_name.get(entry_id, 'Unknown')
    return 'BYE' if completed else 'TBD'


async def _next_stage_advancement(
    service: BracketService, bracket: Bracket
) -> Optional[dict]:
    """The next stage's advancement rule, if any — drives cut lines / tinting."""
    brackets = await service.list_brackets(bracket.tournament_id)
    nxt = next(
        (b for b in brackets if b.stage_order == bracket.stage_order + 1), None
    )
    if nxt is None or not nxt.config:
        return None
    return (nxt.config or {}).get('advancement')


# Delegated hover-run highlight: hovering any entrant row tints that entrant's
# rows across every match in the bracket (Gracket's data-attribute technique).
_HOVER_RUN_JS = (
    '<script>'
    'if(!window.__wizBracketHover){'
    'window.__wizBracketHover=true;'
    'var t=function(on){return function(e){'
    'var el=e.target.closest?e.target.closest(\'[data-entrant-id]\'):null;'
    'if(!el)return;'
    'var id=el.getAttribute(\'data-entrant-id\');'
    'if(!id)return;'
    'var ns=document.querySelectorAll(\'.bracket-slot[data-entrant-id="\'+id+\'"]\');'
    'for(var i=0;i<ns.length;i++){ns[i].classList.toggle(\'run-highlight\',on);}'
    '};};'
    "document.addEventListener('mouseover',t(true),true);"
    "document.addEventListener('mouseout',t(false),true);"
    '}'
    '</script>'
)


def _install_hover_run() -> None:
    ui.add_body_html(_HOVER_RUN_JS)


def _populate_bracket_toolbar(
    row, views, wrapper, refresh: Callable, view_state: Dict[str, str],
) -> None:
    """Fill the elimination toolbar: a mobile view toggle, zoom, and a refresh.

    The toggle is CSS-hidden above ``lt.md`` (the 2-D bracket is the only view
    there); below it, it swaps the accordion for the 2-D bracket by re-classing
    the shared ``views`` wrapper. ``view_state`` survives a live-event rebuild of
    the page body, so an auto-refresh does not throw the reader back to the list.
    """
    zoom = {'v': 1.0}

    def apply() -> None:
        wrapper.style(f'--bracket-zoom: {zoom["v"]}')
        label.set_text(f'{int(zoom["v"] * 100)}%')

    def step(delta: float) -> None:
        zoom['v'] = max(0.5, min(1.6, round(zoom['v'] + delta, 2)))
        apply()

    def set_view(value: str) -> None:
        view_state['v'] = value
        views.classes(remove='view-list view-bracket', add=f'view-{value}')

    with row:
        layout = ui.toggle(
            {'list': 'List', 'bracket': 'Bracket'},
            value=view_state['v'],
            on_change=lambda e: set_view(e.value),
        ).props('dense unelevated no-caps size=sm').classes('wiz-segmented bracket-view-toggle')
        bind_url('layout', layout, default='list')
        # Zoom drives the 2-D canvas only, so it hides with it in list mode.
        with ui.row().classes('bracket-zoom-controls items-center gap-1'):
            ui.button(icon='zoom_out', on_click=lambda: step(-0.1)) \
                .props('flat dense round').tooltip('Zoom out')
            label = ui.label('100%').classes('text-caption text-grey')
            ui.button(icon='zoom_in', on_click=lambda: step(0.1)) \
                .props('flat dense round').tooltip('Zoom in')
        ui.space()
        ui.button(icon='refresh', on_click=refresh) \
            .props('flat dense round').tooltip('Refresh')


def _group_letter(group: Optional[int]) -> Optional[str]:
    if group is None:
        return None
    try:
        return chr(ord('A') + (int(group) - 1))
    except (TypeError, ValueError):
        return str(group)


def _render_results_list(
    matches: List[BracketMatch], entry_name: Dict[int, str]
) -> None:
    played = [m for m in matches if m.state == BracketMatchState.COMPLETE]
    upcoming = [m for m in matches if m.state == BracketMatchState.OPEN]
    if not played and not upcoming:
        ui.label('No matches yet.').classes('text-caption text-grey')
        return
    with ui.column().classes('gap-1 w-full'):
        for match in played:
            name1 = _slot_label(match.entry1_id, entry_name, completed=True)
            name2 = _slot_label(match.entry2_id, entry_name, completed=True)
            if match.entry2_id is None or match.entry1_id is None:
                winner = _slot_label(match.winner_id, entry_name, completed=True)
                ui.label(f'{winner} — bye').classes('text-caption')
            else:
                winner_name = entry_name.get(match.winner_id, 'Unknown')
                loser_name = name2 if match.winner_id == match.entry1_id else name1
                ui.label(f'{winner_name} def. {loser_name}').classes('text-caption')
        for match in upcoming:
            name1 = _slot_label(match.entry1_id, entry_name, completed=False)
            name2 = _slot_label(match.entry2_id, entry_name, completed=False)
            ui.label(f'{name1} vs {name2}').classes('text-caption text-grey-6')


def _render_round_robin(
    entries: List[BracketEntry],
    matches: List[BracketMatch],
    entry_name: Dict[int, str],
    config: Optional[dict],
    *,
    advancement: Optional[dict] = None,
    complete: bool = False,
    entry_avatar: Optional[Dict[int, str]] = None,
) -> None:
    entry_seed = {e.id: e.seed for e in entries}
    tiebreakers = standings_config_from(config).tiebreakers
    group_of: Dict[int, Optional[int]] = {}
    for m in matches:
        for eid in (m.entry1_id, m.entry2_id):
            if eid is not None:
                group_of[eid] = m.group_number
    for e in entries:
        group_of.setdefault(e.id, e.group_number)

    groups = sorted({group_of.get(e.id) for e in entries}, key=lambda g: (g is None, g))
    # A per-group advancement rule tints/cuts each group's top `count`.
    per_group_cut = (
        advancement.get('count')
        if advancement and advancement.get('per_group')
        else None
    )

    with ui.element('div').classes('bracket-groups-grid'):
        for group in groups:
            group_entry_ids = [e.id for e in entries if group_of.get(e.id) == group]
            group_matches = [m for m in matches if m.group_number == group]
            with ui.card().classes('bracket-group-card'):
                letter = _group_letter(group)
                ui.label(f'Group {letter}' if letter else 'Standings') \
                    .classes('bracket-section-title')
                if not group_entry_ids:
                    ui.label('No entrants yet.').classes('italic-note')
                    continue
                standings = compute_standings(
                    group_entry_ids,
                    results_from_matches(group_matches),
                    standings_config_from(config),
                )
                render_standings(
                    standings, entry_name, entry_seed, tiebreakers,
                    advancing=per_group_cut, show_elim=complete,
                    entry_avatar=entry_avatar,
                )
                order_ids = [s.ref for s in standings]
                if 2 <= len(group_entry_ids) <= 10:
                    with ui.expansion('Crosstable').classes('w-full'):
                        render_crosstable(order_ids, group_matches, entry_name)
                with ui.expansion('Matches').classes('w-full'):
                    _render_results_list(group_matches, entry_name)


def _render_swiss(
    entries: List[BracketEntry],
    matches: List[BracketMatch],
    entry_name: Dict[int, str],
    config: Optional[dict],
    *,
    advancement: Optional[dict] = None,
    complete: bool = False,
    entry_avatar: Optional[Dict[int, str]] = None,
) -> None:
    entry_seed = {e.id: e.seed for e in entries}
    dropped = {e.id for e in entries if e.status == BracketEntryStatus.DROPPED}
    tiebreakers = standings_config_from(config).tiebreakers

    ui.label('Standings').classes('bracket-section-title')
    if not entries:
        ui.label('No entrants yet.').classes('italic-note')
        return
    standings = compute_standings(
        [e.id for e in entries], results_from_matches(matches), standings_config_from(config)
    )
    swiss_cut = advancement.get('count') if advancement else None
    render_standings(
        standings, entry_name, entry_seed, tiebreakers,
        advancing=swiss_cut, show_elim=complete, dropped_ids=dropped,
        entry_avatar=entry_avatar,
    )
    if 'head_to_head' in tiebreakers:
        ui.label('Remaining ties are broken by head-to-head result.') \
            .classes('text-caption text-grey')

    rounds = sorted({m.round for m in matches})
    if not rounds:
        return
    records = entry_records([e.id for e in entries], matches)
    open_rounds = [m.round for m in matches if m.state == BracketMatchState.OPEN]
    default_round = max(open_rounds) if open_rounds else max(rounds)

    ui.label('Pairings').classes('bracket-section-title')
    with ui.tabs().classes('w-full') as tabs:
        for r in rounds:
            ui.tab(f'r{r}', label=f'Round {r}')
    with ui.tab_panels(tabs, value=f'r{default_round}').classes('w-full'):
        for r in rounds:
            with ui.tab_panel(f'r{r}'):
                render_pairings(
                    [m for m in matches if m.round == r], entry_name, records,
                )
    bind_url('round', tabs, default=f'r{default_round}', choices=[f'r{r}' for r in rounds],
             label=lambda v: f'Round {v[1:]}')


def _static_view_link(path: str, *, public: bool) -> None:
    """Offer the cached, socket-free twin of this page (pages/static_brackets.py).

    ``path`` is tenant-relative: ``ui.link`` prepends the page's ``root_path``,
    so passing a ``/t/<slug>``-qualified path here would double the prefix.

    The interactive page holds a websocket for as long as the tab is open, which
    is the wrong trade for a spectator link posted to a stream or a Discord
    channel — so the page that costs a socket is also where the one that doesn't
    is advertised.

    ``public`` is whether a spectator would see anything there. Staff reach
    draft stages here, and the twin is anonymous by construction, so offering
    the link for a draft hands staff a URL that 404s for everyone they paste it
    to. Until it would work, say when it will.
    """
    if not public:
        ui.label('The spectator view goes live once a stage starts.') \
            .classes('text-caption text-grey')
        return
    ui.link('Shareable spectator view', path).classes('text-caption') \
        .tooltip('A static, cached page for followers — no live connection')


def _share_meta(title: str, description: str) -> None:
    """Title and summary for a link unfurl (Discord, chat apps)."""
    ui.add_head_html(
        f'<meta property="og:title" content="{html.escape(title)}">'
        f'<meta property="og:description" content="{html.escape(description)}">'
    )


async def _viewer():
    """The signed-in viewer (or None) and whether they hold staff here."""
    user = await get_user_from_session(app.storage.user)
    return user, await AuthService.is_staff(user)


def create() -> None:
    @public_page('/tournament/{tournament_id}/brackets', feature=FeatureFlag.BRACKETS, scope='row')
    async def bracket_index(tournament_id: str) -> Optional[RedirectResponse]:
        user, is_staff = await _viewer()
        tid = parse_route_id(tournament_id)

        service = BracketService()
        # The service decides what this viewer may see: a tournament with
        # nothing published (name included) and DRAFT/CANCELLED stages are
        # staff-only, answered exactly like an id that was never used.
        tournament = (
            await service.get_visible_tournament(user, tid) if tid is not None else None
        )
        if tid is None or tournament is None:
            await render_not_found(user=user)
            return None
        redirect = await row_event_redirect(page_request(), tournament.event_id)
        if redirect is not None:
            return redirect
        brackets = await service.list_visible_brackets(user, tid)
        public_stages = [b for b in brackets if BracketService.is_published(b)]

        community = await TenantService.current_community_name() or 'Wizzrobe'
        ui.page_title(f'{tournament.name} — Brackets — {community}')
        _share_meta(
            f'{tournament.name} — Brackets',
            f'{community} · {len(public_stages)} stage(s) · live bracket',
        )
        await BaseLayout(user=user).render()

        with ui.column().classes('page-container-narrow q-mt-md'):
            page_title(f'{tournament.name} — Brackets')
            _static_view_link(
                f'/live/tournament/{tid}/brackets', public=bool(public_stages),
            )
            ui.separator().classes('separator-spacing')
            if not brackets:
                empty_panel('No brackets have been published for this tournament.', icon='account_tree')
                return None
            with ui.element('div').classes('wiz-tile-grid'):
                for bracket in brackets:
                    with ui.card().classes('q-pa-md'):
                        with ui.row().classes('items-center justify-between w-full'):
                            with ui.column().classes('gap-0'):
                                ui.label(bracket.name).classes('text-subtitle1 text-bold')
                                ui.label(
                                    f'{stage_label(bracket.stage_order)} · '
                                    f'{format_label(bracket.format)}'
                                ).classes('text-caption')
                                setup = config_summary(bracket) if is_staff else []
                                if setup:
                                    ui.label(' · '.join(setup)).classes('text-caption text-grey')
                            with ui.row().classes('items-center gap-2'):
                                ui.badge(
                                    state_label(bracket.state),
                                    color=state_color(bracket.state),
                                )
                                ui.button(
                                    'View', icon='visibility',
                                    on_click=lambda bid=bracket.id: ui.navigate.to(
                                        f'/brackets/{bid}'
                                    ),
                                ).props('flat dense')
        return None

    @public_page('/brackets/{bracket_id}', feature=FeatureFlag.BRACKETS, scope='row')
    async def bracket_detail(bracket_id: str) -> Optional[RedirectResponse]:
        user, is_staff = await _viewer()
        parsed_id = parse_route_id(bracket_id)
        service = BracketService()
        # An unpublished (DRAFT) or withdrawn stage reads as absent to everyone
        # but staff, with the same page and status as an id never used.
        first = (
            await service.get_visible_bracket(user, parsed_id) if parsed_id is not None else None
        )
        if parsed_id is None or first is None:
            await render_not_found(user=user)
            return None
        stage_id = parsed_id
        tournament = await Tournament.get_or_none(
            id=first.tournament_id, tenant_id=require_tenant_id(),
        )
        redirect = await row_event_redirect(page_request(), tournament.event_id if tournament else None)
        if redirect is not None:
            return redirect
        tournament_name = tournament.name if tournament else 'Tournament'
        community = await TenantService.current_community_name() or 'Wizzrobe'
        ui.page_title(f'{first.name} — {tournament_name} — {community}')
        _share_meta(
            f'{first.name} — {tournament_name}',
            f'{community} · {stage_label(first.stage_order)} · '
            f'{format_label(first.format)} · {state_label(first.state)}',
        )
        await BaseLayout(user=user).render()
        ui.add_head_html(f'<link rel="stylesheet" href="{asset_url("css/brackets.css")}">')
        _install_hover_run()

        # Captured while the request context is live; row-action/live-refresh
        # handlers fire from detached client events that have lost the tenant
        # contextvar (and the slot context), so scoped reads/writes rebind the
        # tenant and refreshes re-enter this client.
        tenant_id = require_tenant_id()
        client = context.client

        # Which view the phone toggle is on. Held per client (not module level)
        # and outside the refreshable so a live BRACKET_* rebuild keeps the
        # reader where they were instead of snapping back to the list.
        view_state: Dict[str, str] = {'v': 'list'}

        # Debounced refresh: one report publishes several BRACKET_* events (and the
        # report dialog also asks to refresh); without coalescing each would clear +
        # rebuild the @ui.refreshable body separately and the rebuilds can stack
        # into a duplicated render. A short debounce collapses the burst into one.
        _refresh_task: Dict[str, object] = {'t': None}

        async def _coalesced_refresh() -> None:
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                return
            with client:
                render_body.refresh()

        def request_refresh() -> None:
            task = _refresh_task['t']
            if task is not None and not task.done():
                task.cancel()
            _refresh_task['t'] = background_tasks.create(_coalesced_refresh())

        async def _on_saved() -> None:
            request_refresh()

        async def open_match_dialog(match_id: int, client) -> None:
            with client:
                with tenant_scope(tenant_id):
                    bracket = await service.get_visible_bracket(user, stage_id)
                    if bracket is None:
                        return
                    entrants = await service.list_entrants(bracket.tournament_id)
                    entries = await service.list_entries(stage_id)
                    matches = await service.list_matches(stage_id)
                    live_state = await service.matchup_live_state(matches)
                name_by_entrant = {en.id: en.display_name for en in entrants}
                entry_name = {
                    e.id: name_by_entrant.get(e.entrant_id, 'Unknown') for e in entries
                }
                entry_seed = {e.id: e.seed for e in entries}
                match = next((m for m in matches if m.id == match_id), None)
                if match is None:
                    return
                records = entry_records([e.id for e in entries], matches)
                number = assign_match_numbers(match_nodes(matches)).get(match.id)
                build_match_dialog(
                    match, entry_name, entry_seed, records, number,
                    best_of=service.resolve_best_of(bracket, match),
                    is_staff=is_staff, actor=user, tenant_id=tenant_id,
                    service=service, on_saved=_on_saved,
                    live=live_state.get(match.id),
                )

        def on_card_click(match_id: int) -> None:
            background_tasks.create(open_match_dialog(match_id, context.client))

        @steady_refreshable
        async def render_body() -> None:
            # A live-event/dialog refresh re-runs this from a background task whose
            # tenant contextvar is unset, so scope the reads explicitly (the admin
            # dialogs do the same) rather than relying on the client-stash fallback.
            with tenant_scope(tenant_id):
                bracket = await service.get_visible_bracket(user, stage_id)
                # An unpublished (DRAFT) stage reads as absent to everyone but
                # staff — its seeded field is not public until the stage starts.
                if bracket is None:
                    ui.label('Bracket not found.').classes('text-error')
                    return
                entrants = await service.list_entrants(bracket.tournament_id)
                entries = await service.list_entries(stage_id)
                matches = await service.list_matches(stage_id)
                # One extra query for the whole field: the derived per-matchup
                # status and watch link the cards paint (U2). Public — the
                # schedule already shows all of it signed out (D4).
                live_state = await service.matchup_live_state(matches)
            entrant_name = {en.id: en.display_name for en in entrants}
            entry_name = {
                e.id: entrant_name.get(e.entrant_id, 'Unknown') for e in entries
            }
            entry_avatar = entry_avatars(entrants, entries)
            mine = viewer_entries(entrants, entries, user.id if user else None)

            with ui.card().classes('page-container w-full q-pa-lg q-mt-md column'):
                with ui.row().classes('items-center justify-between w-full'):
                    with ui.column().classes('gap-0'):
                        page_title(bracket.name)
                        ui.label(
                            f'{stage_label(bracket.stage_order)} · '
                            f'{format_label(bracket.format)}'
                        ).classes('text-caption')
                        # The stage's rules as staff configured them — a
                        # check on the setup, not something a player acts on.
                        setup = config_summary(bracket) if is_staff else []
                        if setup:
                            ui.label(' · '.join(setup)).classes('text-caption text-grey')
                    ui.badge(
                        state_label(bracket.state),
                        color=state_color(bracket.state),
                    )
                with ui.row().classes('items-center gap-3'):
                    ui.button(
                        'All stages', icon='list',
                        on_click=lambda: ui.navigate.to(
                            f'/tournament/{bracket.tournament_id}/brackets'
                        ),
                    ).props('flat dense')
                    _static_view_link(
                        f'/live/brackets/{stage_id}',
                        public=BracketService.is_published(bracket),
                    )
                ui.separator().classes('separator-spacing')

                if bracket.state == BracketState.DRAFT or not matches:
                    ui.label('This stage has not started yet.').classes('italic-note')
                    if entries:
                        ui.label('Seeded entrants').classes('section-title q-mt-md')
                        with ui.column().classes('gap-1 w-full'):
                            for entry in entries:
                                seed = f'#{entry.seed} · ' if entry.seed is not None else ''
                                ui.label(
                                    f'{seed}{entry_name.get(entry.id, "Unknown")}'
                                ).classes('text-caption')
                    return

                if bracket.format in (BracketFormat.SINGLE_ELIM, BracketFormat.DOUBLE_ELIM):
                    double = bracket.format == BracketFormat.DOUBLE_ELIM
                    ctx = build_context(
                        bracket.config, entries, matches, entry_name,
                        entry_avatar=entry_avatar,
                        on_card_click=on_card_click, live_state=live_state,
                        viewer_entry_ids=mine, booked=booked_times(matches),
                    )
                    # 2-D connector bracket (>= md); a per-round accordion (< md),
                    # with the toolbar toggle swapping between them on a phone.
                    # Exactly one is visible at a time — see brackets.css.
                    views = ui.element('div').classes(
                        f'bracket-views view-{view_state["v"]} w-full'
                    )
                    with views:
                        toolbar_row = ui.row().classes('bracket-toolbar items-center gap-2 q-mb-sm')
                        with ui.element('div').classes('bracket-2d w-full'):
                            wrapper = ui.element('div').classes('bracket-zoomable w-full')
                            with wrapper:
                                render_elimination(matches, ctx, double=double)
                        with ui.element('div').classes('bracket-mobile-list w-full'):
                            render_elimination_mobile(matches, ctx, double=double)
                        _populate_bracket_toolbar(
                            toolbar_row, views, wrapper, request_refresh, view_state,
                        )
                else:
                    with tenant_scope(tenant_id):
                        advancement = await _next_stage_advancement(service, bracket)
                    complete = bracket.state == BracketState.COMPLETE
                    if bracket.format == BracketFormat.ROUND_ROBIN:
                        _render_round_robin(
                            entries, matches, entry_name, bracket.config,
                            advancement=advancement, complete=complete,
                            entry_avatar=entry_avatar,
                        )
                    else:
                        _render_swiss(
                            entries, matches, entry_name, bracket.config,
                            advancement=advancement, complete=complete,
                            entry_avatar=entry_avatar,
                        )

        await render_body()
        # The tournament id enables the live match half (U2): MATCH_* payloads
        # carry no bracket_id, so the subscriber filters on the tournament and
        # lets the debounce absorb the wider net.
        with tenant_scope(tenant_id):
            live_bracket = await service.get_visible_bracket(user, stage_id)
        register_bracket_view(
            stage_id, request_refresh,
            tournament_id=live_bracket.tournament_id if live_bracket else None,
        )
        return None
