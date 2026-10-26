"""Admin Schedule Management Page"""


from typing import Any

from nicegui import background_tasks, context, ui

from application.services import EventService, FeatureFlagService, MatchRescheduleService
from application.tenant_context import require_tenant_id
from models import FeatureFlag, Match, RescheduleRequestStatus
from pages.admin_tabs.links import SCHEDULE, admin_url
from theme.dialog.match_dialog import AdminMatchDialog
from theme.dialog.match_sheet import MatchSheet
from theme.dialog.reschedule_decision_dialog import RescheduleDecisionDialog
from theme.help import help_icon
from theme.match_inbox import MatchInbox
from theme.page_header import page_header
from theme.realtime import register_view
from theme.tables.admin_crud import current_actor
from theme.tables.match import MatchTableView
from theme.tables.match_access import MatchBoardAccess
from theme.tables.match_lifecycle import MatchLifecycleHandlers
from theme.tables.match_run_sheet import RUN_SHEET_COLUMNS, crew_columns
from theme.tables.preferences import TableKeys
from theme.unbooked_board import UnbookedBoard
from theme.url_state import consume_deep_link, url_param, url_state


async def _render_inbox(inbox, anchor, request_id, open_decision, client, *, may_decide: bool) -> None:
    """Draw the inbox into its anchor, then open a deep-linked request's dialog.

    Sequenced rather than concurrent: the dialog's Approve refreshes the inbox,
    so the inbox has to exist first. Runs as a background task, hence the
    explicit client, and it renders inside ``anchor`` so the inbox keeps its
    place beside the board rather than landing wherever the task ran.

    ``may_decide`` gates the deep link as well as the requests. A stream manager
    or crew coordinator reaches this board with ``edit=False``; a forwarded
    ``?reschedule_request=`` would otherwise show them the requester's reason
    and two buttons the service then refuses.
    """
    with client, anchor:
        await inbox.render()
    if request_id is not None:
        if may_decide:
            await open_decision(int(request_id), client)
        consume_deep_link('reschedule_request', client=client)


#: The bracket features whose matchups the "Not booked yet" view lists.
_BRACKET_FLAGS = frozenset({FeatureFlag.CHALLONGE, FeatureFlag.STARTGG, FeatureFlag.BRACKETS})


async def _render_unbooked(board: UnbookedBoard, anchor, client) -> None:
    with client, anchor:
        await board.render()


async def admin_schedule_page(
    access: MatchBoardAccess | None = None,
    match_id: int | None = None,
    tournament_ids: list[int] | None = None,
    reschedule_request: int | None = None,
    comments: int | None = None,
) -> None:
    """The admin Schedule board.

    ``access`` is what this viewer may do (see ``match_access``); a board built
    without one offers nothing but the read.

    ``tournament_ids`` narrows the board to the tournaments the viewer operates.
    It is set for a tournament admin or crew coordinator — whose authority *is*
    per-tournament — and left ``None`` for staff and the stream manager, whose
    authority is community-wide.

    ``reschedule_request`` is a request id arriving from the staff DM's button;
    its decision dialog opens once the board has rendered.

    ``comments`` is a match id from a staff alert DM; that match's sheet opens
    on its comments once the board's first load brings the row.
    """
    access = access or MatchBoardAccess()
    with ui.column().classes('page-container-wide') as page_container:
        with page_header('Match Schedule'):
            await help_icon(('staff-schedule-board', 'The schedule board'))
            if access.edit:
                ui.space()
                ui.button('New match', icon='add', on_click=lambda: submit_admin_match()) \
                    .props('color=primary')

        # Same reasoning as the match_id chip below: a board showing a subset
        # with no explanation reads as a board that lost rows.
        if tournament_ids is not None:
            with ui.row().classes('items-center gap-2 q-mb-sm'):
                with ui.element('span').classes('wiz-chip wiz-chip--neutral'):
                    ui.icon('emoji_events', size='14px')
                    n = len(tournament_ids)
                    # "You run" was wrong for a crew coordinator, who is scoped
                    # here too but runs nothing.
                    ui.label('Showing only your tournament' if n == 1
                             else f'Showing only your {n} tournaments')

        # Creating a match is a scheduling action, not a lifecycle one, so it
        # stays here rather than moving into MatchLifecycleHandlers.
        async def submit_admin_match():
            async def after_submit():
                await table_view.refresh()
            with page_container:
                dialog = AdminMatchDialog(on_submit=after_submit)
                await dialog.open()

        def get_query():
            # The handlers' per-row lookup. The *board's* rows come from
            # MatchDisplayService, so the scope has to be passed to the view as
            # well (``scope_tournament_ids``) — narrowing only here would leave
            # the chip above claiming a scope the board never applied. Per row,
            # so not narrowed to the event either: a row decides its own event
            # (event-scope: exempt).
            qs = Match.filter(tenant_id=require_tenant_id())
            if tournament_ids is not None:
                qs = qs.filter(tournament_id__in=tournament_ids)
            return qs

        # Bound after the view exists (two-phase; see MatchLifecycleHandlers).
        table_view: Any = None

        handlers = MatchLifecycleHandlers(page_container, access=access)
        sheet = MatchSheet(handlers, access=access)
        if comments:
            sheet.open_when_loaded(int(comments))
            consume_deep_link('comments', match_id=match_id or comments)

        async def _open_decision(request_id: int, client) -> None:
            """Open one request's decision dialog, from the inbox or a DM link.

            Takes the client because the DM entry point runs it as a background
            task, where the slot stack is empty and every ``ui.*`` call below —
            the notice and the dialog itself — would otherwise have nowhere to
            render.
            """
            with client:
                service = MatchRescheduleService()
                request = await service.get_by_id(request_id)
                if request is None or request.status is not RescheduleRequestStatus.PENDING:
                    # A DM outlives the request it points at — someone else may
                    # have answered it. Saying so beats a button that does nothing.
                    ui.notify('That request has already been dealt with.', color='warning')
                    return
                actor = await current_actor()
                if actor is None:
                    # A session that outlived its account, or one deactivated
                    # since the page loaded. Caught here rather than let through
                    # to fail as a PermissionError from inside the decision.
                    ui.notify('Log in again to decide this.', color='warning')
                    return

                async def after():
                    inbox.refresh()
                    await table_view.refresh()

                with page_container:
                    await RescheduleDecisionDialog(request, actor, on_decision=after).open()

        inbox = MatchInbox(
            access=access,
            handlers=handlers,
            open_match=sheet.open,
            open_request=lambda request_id: _open_decision(request_id, context.client),
            tournament_ids=tournament_ids,
        )

        def _rows_changed(rows) -> None:
            inbox.refresh()
            sheet.sync(rows)

        # The inbox sits beside the board on a desktop and above it on a phone.
        with ui.element('div').classes('schedule-layout full-width'):
            # An empty anchor claiming the inbox's place, filled by a background
            # task once the board exists (the inbox reads the board's rows).
            inbox_anchor = ui.column().classes('schedule-layout__inbox gap-0')
            with ui.column().classes('schedule-layout__board gap-0'):
                # Staff and tournament admins can book a bracket's matchups for
                # its players, so they get the matchups nobody has booked yet
                # as a second view of the board.
                show_unbooked = access.edit and bool(
                    _BRACKET_FLAGS & await FeatureFlagService().enabled_flags()
                )
                switch: Any = None
                view_param: Any = None
                opening_view = 'unbooked' if show_unbooked and url_state().requested('view') == 'unbooked' \
                    else 'booked'
                if show_unbooked:
                    def _show_view(view: str) -> None:
                        booked_box.set_visibility(view != 'unbooked')
                        unbooked_box.set_visibility(view == 'unbooked')
                        if view == 'unbooked':
                            unbooked.refresh()

                    def _switch_views(view: str) -> None:
                        _show_view(view)
                        if view_param is not None:
                            view_param.set(view)

                    def _restore_view(view: str) -> None:
                        view = 'unbooked' if view == 'unbooked' else 'booked'
                        view_param.value = view
                        switch.set_value(view)
                        _show_view(view)

                    switch = ui.toggle(
                        {'booked': 'Booked', 'unbooked': 'Not booked yet'}, value=opening_view,
                        on_change=lambda e: _switch_views(e.value),
                    ).props('no-caps dense unelevated aria-label="Show"') \
                        .classes('wiz-segmented schedule-view-switch')
                    view_param = url_param(
                        'view', switch, default='booked', value=opening_view,
                        label=lambda v: 'Not booked yet' if v == 'unbooked' else None,
                        on_restore=_restore_view,
                    )
                booked_box = ui.column().classes('full-width gap-0')
                unbooked_box = ui.column().classes('full-width gap-0')
                booked_box.set_visibility(opening_view != 'unbooked')
                unbooked_box.set_visibility(opening_view == 'unbooked')
                with booked_box:
                    table_view = MatchTableView(
                        columns=crew_columns(RUN_SHEET_COLUMNS, await EventService.current_event_broadcasters()),
                        get_query=get_query,
                        admin_controls=True,
                        access=access,
                        storage_key='admin_schedule',
                        table_key=TableKeys.ADMIN_SCHEDULE,
                        show_accommodations=True,
                        show_comment_counts=True,
                        searchable=True,
                        # The admin's job *is* the Finished-not-yet-Confirmed set, so it
                        # must be on screen without them discovering the State filter.
                        default_state_filter=['Scheduled', 'Checked In', 'Started', 'Finished'],
                        default_day_scope='Today',
                        match_ids=[match_id] if match_id else None,
                        scope_tournament_ids=tournament_ids,
                        on_rows_changed=_rows_changed,
                        layout='run_sheet',
                        on_open_match=sheet.open,
                        **handlers.callbacks(),
                    )
                if show_unbooked:
                    unbooked = UnbookedBoard(
                        on_count=lambda n: switch.set_options(
                            {'booked': 'Booked', 'unbooked': f'Not booked yet · {n}' if n else 'Not booked yet'},
                            value=switch.value,
                        ),
                        on_booked=lambda: table_view.refresh(),
                        can_open_native_entrants=access.edit_scope is None,
                    )
        if match_id:
            table_view.show_one_match_chip(admin_url(SCHEDULE), 'Show all matches')
        handlers.table_view = table_view
        sheet.view = table_view
        inbox.view = table_view

        # After the board exists, so the inbox can read its rows, and so the
        # deep-linked dialog opens over a rendered board.
        background_tasks.create(_render_inbox(
            inbox, inbox_anchor, reschedule_request,
            _open_decision, context.client, may_decide=access.edit,
        ))
        if show_unbooked:
            background_tasks.create(_render_unbooked(unbooked, unbooked_box, context.client))

        async def _on_live_change(_match_id, _change_type) -> None:
            """Re-read the inbox when anything about a match changes elsewhere.

            Its own subscription as well as the board's ``on_rows_changed``: the
            requests are not rows. A request can land on a match the board's
            day or state filter is hiding, and ``update_row_by_id`` returns early
            for a row that is not on screen, so riding the board alone would
            leave staff looking at a stale inbox precisely when a filter is
            narrowing their view.
            """
            if access.edit:
                inbox.refresh()

        register_view(_on_live_change)

        # Route through the view's _bg so the tab-switch refresh rebinds the
        # tenant (the selected_tab handler runs in a detached task that lost it).
        def on_tab_selected():
            table_view._bg(table_view.refresh())
        ui.on('selected_tab', lambda e: on_tab_selected() if e.args == 'Match Schedule' else None)
