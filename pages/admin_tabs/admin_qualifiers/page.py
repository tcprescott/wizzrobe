"""Admin Async Qualifiers page — create/administer qualifiers, author pools, review runs.

Gated by ``QUALIFIER_ADMIN``/STAFF (or a per-qualifier admin). Mirrors the
Tournaments admin pattern: a list of qualifiers with a create/edit dialog, plus a
"Manage" drill-down that authors pools/permalinks, works the reviewer queue, and
shows the leaderboard.

Rendering follows the ``service_health_view`` pattern: sync ``@ui.refreshable``
views read from ``state``; async loaders fetch, stash into ``state``, and restore
the captured client (``with client:``) before ``.refresh()``. Mutating handlers
run directly from ``on_click`` (event context has a slot) so ``ui.notify`` is safe
without a background task.
"""

from nicegui import app, context, ui

from application.services import (
    AsyncQualifierLiveRaceService,
    AsyncQualifierService,
    AuthService,
    PresetService,
    UserService,
    get_user_from_session,
)
from application.services.async_qualifier.async_qualifier_rules import display_name_of
from application.tenant_context import require_tenant_id, tenant_scope
from application.utils.timezone import parse_local_datetime
from pages.admin_tabs.admin_qualifiers.live_races import build_live_tab
from pages.admin_tabs.admin_qualifiers.pools import build_pools_tab
from pages.admin_tabs.admin_qualifiers.review_queue import build_queue_tab
from pages.admin_tabs.admin_qualifiers.reviewers import build_reviewers_tab
from pages.admin_tabs.admin_qualifiers.shared import (
    BOARD_COLUMNS,
    BOARD_PAGE,
    BOARD_TAB,
    GRANT_ACTION,
    LIVE_TAB,
    POOLS_TAB,
    QUEUE_PAGE_SIZE,
    QUEUE_TAB,
    REVIEWERS_TAB,
    RUNS_COLUMNS,
    RUNS_PAGE,
    RUNS_TAB,
    board_rows,
    enum_value,
    fmt,
    run_rows,
)
from theme.dialog._helpers import dialog_actions, form_dialog, native_date_input, native_time_input
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.empty_state import empty_panel
from theme.help import help_icon
from theme.markdown_editor import MarkdownEditor
from theme.menu import menu_action, more_menu
from theme.notify import notify_error
from theme.page_header import page_header
from theme.qualifier_copy import BOARD_EXPLAINER
from theme.tables.admin_crud import wire_tab_refresh
from theme.tables.mobile_grid import enable_mobile_grid
from theme.tables.preferences import TableKeys, row_count_label, search_input, sticky_header

# Deep-link tab keys → the tab names the drill-down is addressed by. Short slugs
# rather than the labels themselves, so a DM's URL does not carry "Review%20Queue"
# and does not break when a label is reworded.
_DEEP_LINK_TABS = {
    'queue': QUEUE_TAB,
    'pools': POOLS_TAB,
    'runs': RUNS_TAB,
    'board': BOARD_TAB,
    'live': LIVE_TAB,
    'reviewers': REVIEWERS_TAB,
}


async def admin_qualifiers_page(
    qualifier: int | None = None,
    open_tab: str | None = None,
) -> None:
    """The staff surface. ``qualifier``/``open_tab`` come from a notification link.

    The "runs are waiting" DM points here, and a DM outlives the thing it points
    at — so a qualifier id that no longer resolves says so rather than silently
    landing on the list, which would read as a broken button.
    """
    service = AsyncQualifierService()
    live_race_service = AsyncQualifierLiveRaceService()
    preset_service = PresetService()
    client = context.client
    # Captured at page build, where a request tenant is guaranteed; every
    # later handler re-binds it rather than re-resolving.
    tenant_id = require_tenant_id()
    state: dict = {
        'qualifiers': [], 'managing': None, 'shell': None, 'list_error': None,
        'can_create': False,
        # The drill-down loads one tab at a time. 'loaded' is which tabs have their
        # data, 'errors' is the per-tab refusal to render in place of it, and 'tab'
        # survives a rebuild so a verdict does not throw the reviewer back to Pools.
        'tab': POOLS_TAB, 'loaded': set(), 'errors': {}, 'queue_shown': QUEUE_PAGE_SIZE,
        'pools': [], 'presets': [], 'live_races': [], 'queue': [], 'queue_context': {},
        'runs': [], 'board': [], 'viewer_id': None,
        'reviewers': [], 'reviewer_options': {}, 'pending': 0, 'racer_options': {},
    }

    # name → the refreshable that renders it. Filled in once the views are defined
    # (they close over ``state``, so they cannot be declared before it).
    _TAB_VIEWS: dict = {}

    async def _current():
        return await get_user_from_session(app.storage.user)

    # ---------------------------------------------------------------- loaders

    async def load_list() -> None:
        try:
            with tenant_scope(tenant_id):
                current = await _current()
                state['qualifiers'] = await service.list_qualifiers(current)
                # A per-qualifier reviewer sees the ones they review, but only
                # STAFF and QUALIFIER_ADMIN may create one.
                state['can_create'] = await AuthService.can_admin_qualifier(current)
            state['list_error'] = None
        except PermissionError as e:
            state['qualifiers'] = []
            state['list_error'] = str(e)
        with client:
            list_view.refresh()

    async def _fetch_pools(current, qid) -> None:
        state['pools'] = await service.list_pools(current, qid)
        state['presets'] = await preset_service.list_selectable()

    async def _fetch_live(current, qid) -> None:
        state['live_races'] = await live_race_service.list_live_races(current, qid)
        # Who a hand-recorded result may be attributed to. The community's own
        # people, like every other person picker — a live race is raced by members,
        # and the global User table is not this page's business.
        state['racer_options'] = {
            person.id: display_name_of(person.display_name, person.username, person.id)
            for person in await UserService().get_community_people_summaries()
        }
        if POOLS_TAB not in state['loaded']:
            # The New Live Race dialog picks a pool and one of its permalinks, so
            # this tab cannot render its own control without them.
            await _fetch_pools(current, qid)
            state['loaded'].add(POOLS_TAB)

    async def _fetch_queue(current, qid) -> None:
        state['queue'] = await service.list_review_queue(current, qid)
        state['pending'] = len(state['queue'])
        state['queue_context'] = await service.review_queue_context(current, qid, state['queue'])
        state['queue_shown'] = QUEUE_PAGE_SIZE
        # Whose claims count as "mine" on the cards. Stashed rather than re-read
        # per card: the view is sync and cannot await.
        state['viewer_id'] = current.id if current is not None else None

    async def _fetch_reviewers(current, qid) -> None:
        state['reviewers'] = await service.list_admins(current, qid)
        already = {p.id for p in state['reviewers']}
        # The community's own people, minus whoever is already on the roster. The
        # picker's default, not an authorization rule — ``add_admin`` gates that.
        state['reviewer_options'] = {
            person.id: display_name_of(person.display_name, person.username, person.id)
            for person in await UserService().get_community_people_summaries()
            if person.id not in already
        }

    async def _fetch_runs(current, qid) -> None:
        state['runs'] = await service.list_runs(current, qid)

    async def _fetch_board(current, qid) -> None:
        state['board'] = await service.get_leaderboard(current, qid)

    _FETCH = {
        POOLS_TAB: _fetch_pools,
        LIVE_TAB: _fetch_live,
        QUEUE_TAB: _fetch_queue,
        REVIEWERS_TAB: _fetch_reviewers,
        RUNS_TAB: _fetch_runs,
        BOARD_TAB: _fetch_board,
    }

    async def load_shell() -> None:
        """The drill-down's header, and nothing else.

        Opening Manage used to pay for the qualifier, its pools, the review queue,
        every run, the leaderboard, every preset and every live race — sequentially,
        and again after every single mutation. Each tab now fetches its own data the
        first time it is selected, and a verdict reloads the queue rather than the
        tournament.
        """
        qid = state.get('managing')
        state['loaded'] = set()
        state['errors'] = {}
        if qid is None:
            state['shell'] = None
        else:
            try:
                with tenant_scope(tenant_id):
                    current = await _current()
                    state['shell'] = {
                        'qualifier': await service.get_qualifier(current, qid)}
                    # Two scalars, so the tab strip can carry the pending count on
                    # arrival. Reading it from the queue tab's own data would show 0
                    # until someone opened that tab, which is the moment the count
                    # stops being news.
                    state['pending'] = await service.count_pending_review(current, qid)
            except (ValueError, PermissionError) as e:
                state['shell'] = {'error': str(e)}
                state['pending'] = 0
        with client:
            detail_view.refresh()
        if state.get('shell') and not state['shell'].get('error'):
            await load_tab(state['tab'])

    async def load_tab(name: str, *, force: bool = False) -> None:
        """Fetch one tab's data if it does not have it, then refresh only that tab.

        A refusal is stashed per tab and rendered in place rather than notified:
        this also runs from ``load_shell``, where there is no event slot to notify
        into.
        """
        qid = state.get('managing')
        if qid is None or name not in _FETCH:
            return
        if not force and name in state['loaded']:
            return
        try:
            # Scope the reads explicitly rather than leaning on the client-stash
            # fallback, the same reason pages/brackets.py does: the tenant
            # contextvar is unset in a handler, so `get_current_tenant_id` falls
            # back to `app.storage.client`, which is not reachable across a chain
            # of awaited reloads in one handler. Measured: a mutation reloading
            # three tabs read tenant 1, 1, then None — and a None tenant makes
            # every feature flag read as off, so the third tab rendered "the Async
            # Qualifiers feature is not enabled for this community".
            with tenant_scope(tenant_id):
                await _FETCH[name](await _current(), qid)
            state['errors'].pop(name, None)
        except (ValueError, PermissionError) as e:
            state['errors'][name] = str(e)
        state['loaded'].add(name)
        with client:
            _TAB_VIEWS[name].refresh()

    async def reload_open_tabs(*names: str) -> None:
        """Re-fetch the tabs a mutation invalidated, skipping any not yet opened.

        A pool edit moves the board and the runs list too, but reloading a tab
        nobody has looked at buys nothing — it is fetched on selection anyway.
        """
        for name in names:
            if name in state['loaded']:
                await load_tab(name, force=True)

    # ------------------------------------------------------------ list view

    @ui.refreshable
    def list_view() -> None:
        if state['list_error']:
            ui.label(state['list_error']).classes('text-warning')
            return
        with ui.row().classes('full-width items-center'):
            if state['can_create']:
                ui.button('New Qualifier', icon='add',
                          on_click=lambda: open_qualifier_dialog()).props('color=primary')
            ui.space()
            ui.button(icon='refresh',
                      on_click=load_list).props('flat color=primary').tooltip('Refresh')
        if not state['qualifiers']:
            empty_panel('No qualifiers yet. Create one to get started.', icon='timer')
        with ui.element('div').classes('wiz-tile-grid wiz-tile-grid--md'):
            for q in state['qualifiers']:
                with ui.card().classes('w-full gap-1'):
                    with ui.row().classes('items-center full-width'):
                        ui.label(q.name).classes('section-title')
                        ui.badge('Active' if q.is_active else 'Inactive',
                                 color='green' if q.is_active else 'grey')
                        ui.space()
                        with ui.row().classes('wiz-card-actions items-center no-wrap gap-1'):
                            ui.button('Manage', icon='tune',
                                      on_click=lambda qid=q.id: _manage(qid)).props('flat color=primary')
                            with more_menu(q.name):
                                menu_action('edit', 'Edit qualifier',
                                            lambda row=q: open_qualifier_dialog(row))
                                ui.separator()
                                menu_action('delete', 'Delete qualifier',
                                            lambda row=q: _delete_qualifier(row), negative=True)
                    ui.label(
                        f'Window: {fmt(q.opens_at)} → {fmt(q.closes_at)}  ·  '
                        f'Runs/pool: {q.runs_per_pool}  ·  Reattempts: {q.allowed_reattempts}'
                    ).classes('text-caption text-grey')
                    if q.event_name:
                        ui.label(f'Feeds: {q.event_name}').classes('text-caption text-grey')

    async def _manage(qid: int) -> None:
        state['managing'] = qid
        await load_shell()

    def _delete_qualifier(qualifier) -> None:
        qid = qualifier.id

        async def do_delete() -> None:
            try:
                await service.delete_qualifier(await _current(), qid)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            ui.notify('Qualifier deleted', color='positive')
            if state['managing'] == qid:
                state['managing'] = None
                await load_shell()
            await load_list()

        ConfirmationDialog(
            message=f'Delete {qualifier.name}? Every pool, permalink and submitted run '
                    'in it goes too, and this can’t be undone.',
            on_confirm=do_delete, confirm_text='Delete forever', title='Delete qualifier?',
            require_phrase='permanently delete',
        ).open()

    def open_qualifier_dialog(existing=None) -> None:
        is_edit = existing is not None
        with form_dialog('Edit Qualifier' if is_edit else 'New Qualifier', card_classes='dialog-card dialog-card--md') as dialog:
            name_in = ui.input('Name', value=existing.name if is_edit else '').classes('w-full')
            event_in = ui.input(
                'Feeds event (optional)', value=(existing.event_name or '') if is_edit else ''
            ).classes('w-full')
            desc_in = MarkdownEditor(
                'Description', value=(existing.description or '') if is_edit else '',
                placeholder='Who it\'s for, what it seeds into, and the rules for a run.',
            )
            ui.label('Window (leave a date blank for open-ended)').classes('text-caption text-grey')
            with ui.row().classes('form-pair gap-2'):
                opens_date = native_date_input('Opens date')
                opens_time = native_time_input('Opens time', '00:00')
            with ui.row().classes('form-pair gap-2'):
                closes_date = native_date_input('Closes date')
                closes_time = native_time_input('Closes time', '23:59')
            with ui.row().classes('form-pair gap-2'):
                rpp_in = ui.number('Runs per pool', value=existing.runs_per_pool if is_edit else 1,
                                   min=1, precision=0)
                reattempts_in = ui.number('Allowed reattempts',
                                          value=existing.allowed_reattempts if is_edit else 0,
                                          min=0, precision=0)
            active_in = ui.switch('Active', value=existing.is_active if is_edit else True)

            def _parse(date_in, time_in):
                if not date_in.value:
                    return None
                return parse_local_datetime(date_in.value, time_in.value or '00:00')

            async def submit():
                try:
                    opens_at = _parse(opens_date, opens_time)
                    closes_at = _parse(closes_date, closes_time)
                except ValueError as e:
                    ui.notify(str(e), color='warning')
                    return
                try:
                    current = await _current()
                    if is_edit:
                        await service.update_qualifier(
                            current, existing.id,
                            name=name_in.value, event_name=event_in.value,
                            description=desc_in.value, opens_at=opens_at, closes_at=closes_at,
                            runs_per_pool=int(rpp_in.value or 1),
                            allowed_reattempts=int(reattempts_in.value or 0),
                            is_active=active_in.value,
                        )
                        ui.notify('Qualifier updated', color='positive')
                    else:
                        await service.create_qualifier(
                            current, name=name_in.value, event_name=event_in.value,
                            description=desc_in.value, opens_at=opens_at, closes_at=closes_at,
                            runs_per_pool=int(rpp_in.value or 1),
                            allowed_reattempts=int(reattempts_in.value or 0),
                            is_active=active_in.value,
                        )
                        ui.notify('Qualifier created', color='positive')
                    dialog.close()
                    await load_list()
                except (ValueError, PermissionError) as e:
                    notify_error(e)

            with dialog_actions().classes('justify-end'):
                ui.button('Cancel', on_click=dialog.close).props('flat')
                ui.button('Save' if is_edit else 'Create', icon='save',
                          on_click=submit).props('color=primary')
        dialog.open()

    # ---------------------------------------------------------- detail view

    @ui.refreshable
    def detail_view() -> None:
        shell = state.get('shell')
        if not shell:
            return
        if shell.get('error'):
            ui.label(shell['error']).classes('text-warning')
            return
        qualifier = shell['qualifier']
        ui.separator()
        with ui.row().classes('items-center full-width'):
            ui.label(f'Managing: {qualifier.name}').classes('section-title')
            ui.space()
            ui.button(icon='close', on_click=_close_manage).props('flat round').tooltip('Close')
        with ui.tabs().classes('w-full') as tabs:
            for name in (POOLS_TAB, LIVE_TAB, QUEUE_TAB, RUNS_TAB, BOARD_TAB,
                         REVIEWERS_TAB):
                # The label carries the pending count while the name stays the key
                # every loader and ``state['tab']`` are addressed by. Nothing told a
                # moderator work was waiting without opening the tab first.
                waiting = state['pending'] if name == QUEUE_TAB else 0
                ui.tab(name, label=f'{name} ({waiting})' if waiting else name)
        # Selecting a tab is what fetches it, so the drill-down opens on one read
        # instead of seven. The value comes from state so a rebuild lands back where
        # the reviewer was.
        tabs.on_value_change(lambda e: _select_tab(e.value))
        with ui.tab_panels(tabs, value=state['tab']).classes('w-full'):
            with ui.tab_panel(POOLS_TAB):
                pools_view()
            with ui.tab_panel(LIVE_TAB):
                live_view()
            with ui.tab_panel(QUEUE_TAB):
                queue_view()
            with ui.tab_panel(RUNS_TAB):
                runs_view()
            with ui.tab_panel(BOARD_TAB):
                board_view()
            with ui.tab_panel(REVIEWERS_TAB):
                reviewers_view()

    async def _select_tab(name) -> None:
        name = getattr(name, 'name', name)
        if not isinstance(name, str):
            return
        state['tab'] = name
        await load_tab(name)

    def _tab_placeholder(name: str) -> bool:
        """Render a tab's error or its not-yet-loaded state; True when it handled it."""
        if state['errors'].get(name):
            ui.label(state['errors'][name]).classes('text-warning')
            return True
        if name not in state['loaded']:
            ui.skeleton().classes('w-full h-8')
            return True
        return False

    # Each tab is its own module, built here rather than inline because every one of
    # them needs the loaders defined above.
    pools_view = build_pools_tab(
        state=state, service=service, current=_current,
        reload_tabs=reload_open_tabs, placeholder=_tab_placeholder,
        notify_error=notify_error,
    )
    live_view = build_live_tab(
        state=state, service=live_race_service, current=_current,
        reload_tabs=reload_open_tabs, placeholder=_tab_placeholder,
        notify_error=notify_error,
    )
    queue_view = build_queue_tab(
        state=state, service=service, current=_current,
        reload_tabs=reload_open_tabs,
        # Claim and Release change the card without settling anything, so the queue
        # is re-read rather than patched in place the way a verdict is.
        reload_queue=lambda: load_tab(QUEUE_TAB, force=True),
        placeholder=_tab_placeholder, notify_error=notify_error, client=client,
    )
    reviewers_view = build_reviewers_tab(
        state=state, service=service, current=_current,
        reload_tabs=reload_open_tabs, placeholder=_tab_placeholder,
        notify_error=notify_error,
    )

    async def _close_manage() -> None:
        state['managing'] = None
        await load_shell()

    @ui.refreshable
    def runs_view() -> None:
        """Every run, because a forfeit never reaches the review queue.

        A forfeit is written straight to approved/score 0, so this list is the
        only place a reviewer can find a mis-clicked one and grant a reattempt.
        """
        if state.get('shell') is None or _tab_placeholder(RUNS_TAB):
            return
        runs = state['runs']
        if not runs:
            ui.label('No runs yet. Check back once qualifiers start.').classes('text-grey')
            return
        columns = list(RUNS_COLUMNS)
        rows = run_rows(runs)
        by_id = {r.id: r for r in runs}
        toolbar = ui.row().classes('items-center w-full')
        table = ui.table(columns=columns, rows=rows, row_key='id',
                         pagination=RUNS_PAGE).classes('w-full wiz-table')
        # This is the only place a mis-clicked forfeit can be found, and a real
        # qualifier holds thousands of runs — so the way in is a name, not a scroll.
        with toolbar:
            search_input(table, placeholder='Find a player or pool…')
            ui.space()
            row_count_label(table, 'runs')
        sticky_header(table)
        table.add_slot('body-cell-actions', f'<q-td :props="props">{GRANT_ACTION}</q-td>')
        table.on('grant', lambda e: _open_grant_dialog(by_id.get(e.args.get('id'))))
        table.on('override', lambda e: _open_override_dialog(by_id.get(e.args.get('id'))))
        enable_mobile_grid(table, columns, actions=GRANT_ACTION,
                           table_key=TableKeys.ADMIN_QUALIFIERS)

    def _open_override_dialog(run) -> None:
        """Reverse a settled verdict — the only surface that can, since the queue
        holds only pending runs."""
        if run is None:
            return
        runner = run.user.display_name or run.user.username
        was = enum_value(run.review_status)
        approve = was != 'approved'
        with form_dialog(f'Change the verdict on {runner}’s run', card_classes='dialog-card dialog-card--sm') as dialog:
            ui.label(
                f'This run is {was}. Overturning it {"approves" if approve else "rejects"} '
                'it instead, tells the runner their earlier result changed, and records '
                'who changed it.'
            ).classes('text-caption text-grey')
            reason_in = ui.textarea('Reason (shown to the runner)').classes('w-full').props('rows=3')

            async def submit() -> None:
                try:
                    await service.review_run(
                        await _current(), run.id, approved=approve,
                        note=reason_in.value, override=True,
                    )
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                ui.notify('Verdict changed', color='positive')
                dialog.close()
                await reload_open_tabs(RUNS_TAB, BOARD_TAB, QUEUE_TAB)

            with dialog_actions().classes('justify-end'):
                ui.button('Cancel', on_click=dialog.close).props('flat')
                confirm = ui.button(
                    f'{"Approve" if approve else "Reject"} instead', icon='gavel',
                    on_click=submit,
                ).props('color=warning')
                confirm.bind_enabled_from(reason_in, 'value', lambda v: bool((v or '').strip()))
        dialog.open()

    def _open_grant_dialog(run) -> None:
        if run is None:
            return
        runner = run.user.display_name or run.user.username
        with form_dialog(f'Grant {runner} another attempt', card_classes='dialog-card dialog-card--sm') as dialog:
            ui.label('This voids the run and frees its pool slot. It does not use up '
                     'the runner\u2019s own reattempt allowance.').classes('text-caption text-grey')
            reason_in = ui.textarea('Reason (shown to the runner)').classes('w-full').props('rows=2')

            async def submit() -> None:
                try:
                    await service.grant_reattempt(
                        await _current(), run.id, reason=reason_in.value,
                    )
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                ui.notify('Reattempt granted', color='positive')
                dialog.close()
                # Voiding a run refreshes its permalink's par, which rescores every
                # approved run on that seed.
                await reload_open_tabs(RUNS_TAB, QUEUE_TAB, BOARD_TAB)

            with dialog_actions().classes('justify-end'):
                ui.button('Cancel', on_click=dialog.close).props('flat')
                grant = ui.button('Grant reattempt', icon='restart_alt', on_click=submit)
                grant.props('color=primary')
                grant.bind_enabled_from(reason_in, 'value', lambda v: bool((v or '').strip()))
        dialog.open()

    @ui.refreshable
    def board_view() -> None:
        if state.get('shell') is None or _tab_placeholder(BOARD_TAB):
            return
        entries = state['board']
        if not entries:
            ui.label('No scored runs yet.').classes('text-grey')
            return
        columns = list(BOARD_COLUMNS)
        rows = board_rows(entries)
        toolbar = ui.row().classes('items-center w-full')
        # Keyed by player, not by rank: ranks are now shared on a tie.
        table = ui.table(columns=columns, rows=rows, row_key='user',
                         pagination=BOARD_PAGE).classes('w-full wiz-table')
        with toolbar:
            search_input(table, placeholder='Find a player…')
            ui.space()
            row_count_label(table, 'players')
        sticky_header(table)
        enable_mobile_grid(table, columns,
                           table_key=TableKeys.ADMIN_QUALIFIER_LEADERBOARD)
        ui.label(BOARD_EXPLAINER).classes('text-caption text-grey')

    # Bound after the views exist; ``load_tab`` reads it by name, and every call
    # happens from a handler long after this module-level wiring has run.
    _TAB_VIEWS.update({
        POOLS_TAB: pools_view,
        LIVE_TAB: live_view,
        QUEUE_TAB: queue_view,
        RUNS_TAB: runs_view,
        BOARD_TAB: board_view,
        REVIEWERS_TAB: reviewers_view,
    })

    # ------------------------------------------------------------------ shell

    with ui.column().classes('page-container'):
        with page_header(
            'Async Qualifiers',
            'Self-paced permalink-pool qualifiers — a peer of Tournaments. Create a '
            'qualifier, author permalink pools, then review submitted runs. The '
            'leaderboard and pools stay hidden from players until the qualifier closes.',
        ):
            await help_icon('qualifiers-pools', 'qualifiers-review-queue')
        list_view()
        detail_view()

    await load_list()
    wire_tab_refresh('Qualifiers', load_list)

    if qualifier is not None:
        if any(q.id == qualifier for q in state['qualifiers']):
            state['tab'] = _DEEP_LINK_TABS.get((open_tab or '').lower(), POOLS_TAB)
            state['managing'] = qualifier
            await load_shell()
        else:
            # Deleted, or in another community, or this viewer cannot administer it.
            # A link that quietly does nothing reads as a broken app.
            ui.notify(
                'That qualifier is no longer available — it may have been deleted.',
                color='warning',
            )
