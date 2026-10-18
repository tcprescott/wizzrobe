"""Admin Users Management Page"""

from datetime import datetime, timedelta, timezone

from nicegui import app, context, ui

from application.services import (
    AccommodationService,
    DiscordMemberImportService,
    NotFoundError,
    TenantMembershipService,
    UserService,
    get_user_from_session,
)
from application.services.tenant_service import TenantService
from application.tenant_context import require_tenant_id
from application.utils.timezone import format_local_datetime, format_local_display
from models import MembershipSource, Role, User
from pages.admin_tabs.admin_accommodations import render_accommodation_panel
from theme.accommodation_copy import STATUS_LABELS
from theme.busy import busy, busy_click
from theme.dialog import AdminUserDialog
from theme.dialog._helpers import dialog_actions, form_dialog
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.dialog.emergency_contact_dialog import open_emergency_contact_dialog
from theme.menu import menu_action
from theme.notify import notify_error
from theme.page_header import page_header
from theme.steady import steady_refreshable
from theme.tables.admin_crud import refresh_button
from theme.tables.export import csv_export_menu_item
from theme.tables.preferences import TableKeys, preferences_button, search_input
from theme.tables.user import UserTableView

_TA_FILTER = '_tournament_admin'
#: The Users-tab "Joined in the last N days" filter.
RECENT_JOIN_WINDOW = timedelta(days=7)

JOIN_SOURCE_LABELS: dict = {
    MembershipSource.STAFF: 'Added by staff',
    MembershipSource.JOIN_REQUEST: 'Join request',
    MembershipSource.DISCORD_AUTO_JOIN: 'Joined via Discord',
    MembershipSource.OPEN: 'Joined openly',
    MembershipSource.ROLE_GRANT: 'Role grant',
    MembershipSource.DISCORD_ROLE: 'Discord role sync',
    MembershipSource.IMPORT: 'SpeedGaming import',
    MembershipSource.DISCORD_IMPORT: 'Discord import',
}

_JOINED_VIA_SLOT = '''<q-td :props="props">
    <span v-if="props.row.joined_discord" class="wiz-chip wiz-chip--live" style="white-space: nowrap">
        <q-icon name="forum" size="xs" class="q-mr-xs" />{{ props.value }}
    </span>
    <span v-else-if="props.value">{{ props.value }}</span>
    <span v-else class="text-grey-7">-</span>
</q-td>'''
_CC_FILTER = '_crew_coordinator'

_EMERGENCY_CONTACT_ITEM = '''
                <q-item clickable @click="$parent.$emit('emergency_contact', props.row)">
                    <q-item-section avatar><q-icon name="contact_phone"/></q-item-section>
                    <q-item-section>Emergency contact…</q-item-section>
                </q-item>'''


def _count(n: int, one: str, many: str) -> str:
    return f'{n} {one if n == 1 else many}'


def _import_stat(value: int, label: str, *, accent: bool = False) -> None:
    with ui.element('div').classes('wiz-import-stat' + (' wiz-import-stat--accent' if accent else '')):
        ui.label(str(value)).classes('wiz-import-stat__value')
        ui.label(label).classes('wiz-import-stat__label')


def _row_actions(emergency_contacts: bool) -> str:
    return '''
    <q-btn flat round dense icon="more_vert" color="grey-7"
           :aria-label="'More for ' + props.row.username">
        <q-menu auto-close anchor="bottom right" self="top right">
            <q-list style="min-width: 200px">
                <q-item clickable @click="$parent.$emit('edit_user', { row: props.row })">
                    <q-item-section avatar><q-icon name="edit"/></q-item-section>
                    <q-item-section>Edit member</q-item-section>
                </q-item>''' + (_EMERGENCY_CONTACT_ITEM if emergency_contacts else '') + '''
                <q-separator />
                <q-item clickable class="text-negative"
                        @click="$parent.$emit('remove_member', props.row)">
                    <q-item-section avatar><q-icon name="person_remove" color="negative"/></q-item-section>
                    <q-item-section>Remove from community…</q-item-section>
                </q-item>
            </q-list>
        </q-menu>
    </q-btn>
'''


async def _in_client(client, coro) -> None:
    """Await ``coro`` inside ``client``'s slot, for a handler run as a background task."""
    with client:
        await coro


async def admin_users_page(
    accommodations: bool = False, ada_request: int | None = None,
    emergency_contacts: bool = False,
) -> None:
    """Members table, plus an ADA requests sub-tab when the community has the feature.

    ``ada_request`` is the staff DM's deep link: the ADA sub-tab opens on that
    request's dialog.
    """
    with ui.column().classes('page-container-wide w-full'):
        page_header('Members')

        if not accommodations:
            if ada_request is not None:
                # A DM link outlives the feature; say why it opens nothing.
                ui.notify(
                    'ADA requests aren’t turned on for this community, so that '
                    'link has nothing to open.', color='warning',
                )
            await _members_panel(accommodations=False, emergency_contacts=emergency_contacts)
            return

        actor = await get_user_from_session(app.storage.user)
        service = AccommodationService()

        # Counts what needs doing, not every open request: an arranged,
        # unchanged request is done, and a count that never reaches zero can't
        # tell staff whether anything is waiting.
        def tab_label(count: int) -> str:
            return f'ADA requests ({count})' if count else 'ADA requests'

        with ui.tabs().props('dense inline-label align=left no-caps').classes('w-full') as sub_tabs:
            members_tab = ui.tab('members', label='Members', icon='group')
            ada_tab = ui.tab('ada', label=tab_label(0), icon='accessible')
        ada_tab.tooltip('New, acknowledged, and changed-since-arranged requests')

        async def recount() -> None:
            try:
                ada_tab.props(f'label="{tab_label(await service.action_needed_count(actor))}"')
            except (ValueError, PermissionError):
                pass

        opener: dict = {}

        async def open_ada(request_id: int) -> None:
            sub_tabs.set_value(ada_tab)
            if opener.get('open'):
                await opener['open'](request_id)

        with ui.tab_panels(sub_tabs, value=members_tab).classes('w-full'):
            with ui.tab_panel(members_tab).style('padding-left: 0; padding-right: 0'):
                await _members_panel(
                    accommodations=True, actor=actor, on_open_ada=open_ada,
                    emergency_contacts=emergency_contacts,
                )
            with ui.tab_panel(ada_tab).style('padding-left: 0; padding-right: 0'):
                opener['open'] = await render_accommodation_panel(on_change=recount)
        await recount()
        if ada_request is not None:
            await open_ada(ada_request)


async def _members_panel(
    accommodations: bool, actor: User | None = None, on_open_ada=None,
    emergency_contacts: bool = False,
) -> None:
    with ui.column().classes('w-full gap-4'):
        ui.label(
            'Everyone in this community and the roles they hold. Add Member '
            'brings in an existing account; Actions › Add user creates a new one. '
            'Click a username to edit it.'
        ).classes('text-caption text-grey')

        # Pending join requests, above the member table. The queue carries its
        # own buttons on purpose: discovery and action on different pages is the
        # mistake that makes a report nobody acts on.
        @steady_refreshable
        async def join_queue() -> None:
            service = TenantMembershipService()
            try:
                pending = await service.list_pending()
            except (ValueError, PermissionError):
                return
            if not pending:
                return
            actor = await get_user_from_session(app.storage.user)

            async def decide(request_id: int, approve: bool) -> None:
                try:
                    if approve:
                        await service.approve_request(actor, request_id)
                    else:
                        await service.deny_request(actor, request_id)
                except (ValueError, PermissionError, NotFoundError) as e:
                    ui.notify(str(e), color='warning')
                    return
                ui.notify('Approved.' if approve else 'Declined.', color='positive')

                # Through the view's _bg: refreshing the queue deletes the button
                # whose handler this is, and the task that outlives it has lost
                # the tenant unless it's rebound.
                async def after_decision() -> None:
                    await join_queue.refresh()
                    await table_view.refresh()
                table_view._bg(after_decision())

            def confirm_decline(request_id: int, name: str) -> None:
                ConfirmationDialog(
                    message=f'Decline {name}’s request to join?',
                    on_confirm=lambda: decide(request_id, False), confirm_text='Decline',
                    title='Decline request?',
                ).open()

            with ui.card().classes('w-full'):
                ui.label(f'{len(pending)} pending join request'
                         f"{'s' if len(pending) != 1 else ''}").classes('text-bold')
                for request in pending:
                    person = request.user
                    # Wraps on a phone: beside the two buttons the name and the
                    # message were squeezed to a word a line.
                    with ui.row().classes('items-start justify-between w-full gap-2'):
                        with ui.column().classes('gap-0').style('flex: 1 1 12rem; min-width: 0'):
                            ui.label(person.display_name or person.username) \
                                .style('overflow-wrap: anywhere')
                            ui.label(f'Asked {format_local_display(request.updated_at)}') \
                                .classes('text-caption text-grey')
                            if request.message:
                                # Plain text, never markup — the requester writes it.
                                ui.label(request.message).classes('text-caption text-grey wiz-free-text')
                        with ui.row().classes('no-wrap gap-1'):
                            ui.button(
                                'Decline', icon='close',
                                on_click=lambda _=None, r=request.id,
                                n=person.display_name or person.username: confirm_decline(r, n),
                            ).props('flat dense color=negative')
                            ui.button(
                                'Approve', icon='check',
                                on_click=lambda _=None, r=request.id: decide(r, True),
                            ).props('flat dense color=positive')

        await join_queue()

        selected = {'value': []}
        ada_only = {'value': False}
        recent_only = {'value': False}
        ada_ids: set[int] = set()
        ada_requests: dict = {}
        memberships: dict = {}
        recent_ids: set[int] = set()

        async def load_row_data() -> None:
            fresh_members = await TenantMembershipService().memberships_by_user()
            memberships.clear()
            memberships.update(fresh_members)
            cutoff = datetime.now(timezone.utc) - RECENT_JOIN_WINDOW
            recent_ids.clear()
            recent_ids.update(
                uid for uid, m in fresh_members.items()
                if m.created_at and m.created_at >= cutoff
            )
            if not accommodations:
                return
            try:
                fresh = await AccommodationService().open_requests_by_user(actor)
            except (ValueError, PermissionError):
                fresh = {}
            ada_requests.clear()
            ada_requests.update(fresh)
            ada_ids.clear()
            ada_ids.update(fresh)

        def decorate(row: dict) -> None:
            membership = memberships.get(row['id'])
            row['joined'] = format_local_datetime(membership.created_at) if membership else ''
            source = membership.source if membership else None
            row['joined_via'] = JOIN_SOURCE_LABELS.get(source, '') if source else ''
            row['joined_discord'] = source is MembershipSource.DISCORD_AUTO_JOIN
            if accommodations:
                mark_ada(row)

        # The request's own status, so New and Arranged don't read the same.
        def mark_ada(row: dict) -> None:
            request = ada_requests.get(row['id'])
            if request is None:
                row['ada'] = ''
                return
            row['ada'] = STATUS_LABELS[request.status] + (
                ', changed' if request.changed_since_arranged else ''
            )
            row['ada_request_id'] = request.id

        columns: list[dict] = [
            {'name': 'username', 'label': 'Username', 'field': 'username', 'sortable': True},
            {'name': 'preferred_name', 'label': 'Display Name', 'field': 'preferred_name',
             'sortable': True},
            {'name': 'pronouns', 'label': 'Pronouns', 'field': 'pronouns', 'sortable': True},
            {'name': 'challonge', 'label': 'Challonge', 'field': 'challonge', 'sortable': True},
            *([{'name': 'ada', 'label': 'ADA', 'field': 'ada', 'sortable': True}]
              if accommodations else []),
            # Not sortable: a comma-joined list of role labels sorts on the
            # string, which orders by whoever happens to hold 'Commentator'.
            {'name': 'roles', 'label': 'Roles', 'field': 'roles'},
            # When and how they got in: Discord auto-join tells nobody, so this
            # is where staff see it.
            {'name': 'joined', 'label': 'Joined', 'field': 'joined', 'sortable': True},
            {'name': 'joined_via', 'label': 'Via', 'field': 'joined_via', 'sortable': True},
            {'name': 'actions', 'label': '', 'field': 'actions', 'align': 'right'},
        ]

        def get_query():
            # Members of this community, not every account on the platform.
            # UserTableView wants a queryset rather than a coroutine, so this
            # hand-scopes; UserService.get_community_people is the canonical
            # version and every other caller uses it.
            sel_list = selected.get('value') or []
            tid = require_tenant_id()
            qs = User.filter(tenant_memberships__tenant_id=tid).exclude(is_system=True)
            if ada_only['value']:
                qs = qs.filter(id__in=list(ada_ids))
            if recent_only['value']:
                qs = qs.filter(id__in=list(recent_ids))
            if not sel_list:
                return qs.distinct()
            global_roles = [v for v in sel_list if v in {r.value for r in Role}]
            if global_roles:
                qs = qs.filter(roles__role__in=global_roles, roles__tenant_id=tid)
            if _TA_FILTER in sel_list:
                qs = qs.filter(admin_tournaments__tenant_id=tid)
            if _CC_FILTER in sel_list:
                qs = qs.filter(crew_coordinated_tournaments__tenant_id=tid)
            return qs.distinct()

        async def add_user():
            async def after_submit(_):
                await table_view.refresh()
            dialog = AdminUserDialog(on_submit=after_submit)
            await dialog.open()

        async def import_from_discord():
            actor = await get_user_from_session(app.storage.user)
            service = DiscordMemberImportService()
            try:
                preview = await service.preview(actor)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return

            with form_dialog('Import from Discord', card_classes='dialog-card dialog-card--sm gap-2') as dialog:
                ui.label(
                    f'The {preview.server_name} Discord server has '
                    f'{_count(preview.people, "person", "people")}.'
                )
                with ui.element('div').classes('wiz-import-stats'):
                    _import_stat(preview.new_members, 'to add as members', accent=True)
                    _import_stat(preview.already_members, 'already members')
                    _import_stat(preview.bots, 'bots, skipped')
                    _import_stat(preview.removed, 'removed from this community, skipped')
                if preview.new_members:
                    ui.label(
                        f'{_count(preview.new_accounts, "of them gets", "of them get")} a new '
                        'Wizzrobe account, ready for them when they sign in with Discord. '
                        'Nobody gets a DM.'
                    ).classes('text-caption text-grey')
                else:
                    ui.label('Everyone in the server is already a member.').classes('text-caption text-grey')
                ui.label(
                    "Safe to run again later: it only adds people who aren't members yet."
                ).classes('wiz-import-note')

                async def submit(e):
                    async with busy(e.sender, 'Importing…'):
                        try:
                            result = await service.run(actor)
                        except (ValueError, PermissionError) as err:
                            notify_error(err)
                            return
                    dialog.close()
                    ui.notify(
                        f'Added {_count(result.members_added, "person", "people")} from Discord'
                        f' ({_count(result.accounts_created, "new account", "new accounts")})',
                        color='positive',
                    )
                    await table_view.refresh()

                with dialog_actions().classes('justify-end'):
                    ui.button('Cancel', on_click=dialog.close).props('flat')
                    import_button = ui.button(
                        f'Import {_count(preview.new_members, "person", "people")}'
                        if preview.new_members else 'Nothing to import',
                        icon='download', on_click=submit,
                    ).props('color=primary')
                    import_button.set_enabled(preview.new_members > 0)
            dialog.open()

        async def add_member():
            actor = await get_user_from_session(app.storage.user)
            candidates = await TenantMembershipService().addable_users()

            with form_dialog('Add a member', card_classes='dialog-card dialog-card--sm gap-2') as dialog:
                ui.label(
                    'Bring an existing account into this community. Their profile '
                    'is shared across communities; only the membership is added '
                    'here.'
                ).classes('text-caption text-grey')
                if not candidates:
                    ui.label('Every account is already a member.').classes('text-caption')
                select = ui.select(
                    options={u.id: (u.display_name or u.username) for u in candidates},
                    label='Account', with_input=True,
                ).classes('w-full')

                async def submit():
                    if not select.value:
                        ui.notify('Choose an account first', color='warning')
                        return
                    user = await UserService().get_user_by_id(select.value)
                    if user is None:
                        ui.notify('That account no longer exists', color='negative')
                        return
                    try:
                        await TenantMembershipService().add_member(actor, user)
                    except (ValueError, PermissionError) as e:
                        ui.notify(str(e), color='warning')
                        return
                    ui.notify(f'{user.display_name or user.username} is now a member',
                              color='positive')
                    dialog.close()
                    await table_view.refresh()

                with dialog_actions().classes('justify-end'):
                    ui.button('Cancel', on_click=dialog.close).props('flat')
                    ui.button('Add', on_click=submit, color='primary')
            dialog.open()

        community = await TenantService.current_community_name()

        async def remove_member(row, client) -> None:
            with client:
                user = await UserService().get_user_by_id(row['id'])
                if user is None:
                    ui.notify('That account no longer exists', color='negative')
                    return
                name = user.display_name or user.username
                consequence = (
                    'They lose access straight away and get a Discord message '
                    'saying so. They can\'t rejoin on their own, even through '
                    'open or Discord joining, until staff add them back or '
                    'approve a request from them.'
                )

                async def confirmed() -> None:
                    actor = await get_user_from_session(app.storage.user)
                    try:
                        await TenantMembershipService().remove_member(actor, user)
                    except (ValueError, PermissionError) as e:
                        notify_error(e)
                        return
                    ui.notify(f'{name} removed from this community', color='positive')
                    await table_view.refresh()

                ConfirmationDialog(
                    title=f'Remove {name} from {community}?',
                    message=(
                        f'{consequence}\n\nAn open ADA request of theirs is '
                        'withdrawn and its details cleared.'
                        + (' Their emergency contact is deleted.' if emergency_contacts else '')
                    ),
                    confirm_text='Remove',
                    on_confirm=lambda: table_view._bg(_in_client(client, confirmed())),
                ).open()

        # One wrapping row: on a phone it folds to the two actions over search,
        # Filters and refresh, where one control a line used to fill the screen.
        filters_open = {'value': False}

        # Below 1024px the CSS collapses .match-filters-card until it carries
        # .wiz-filters-open, so phones need this toggle (hidden on desktop) or
        # the filters are unreachable — the match board's pattern.
        def toggle_filters() -> None:
            filters_open['value'] = not filters_open['value']
            if filters_open['value']:
                filters_card.classes(add='wiz-filters-open')
            else:
                filters_card.classes(remove='wiz-filters-open')

        def update_filter_badge() -> None:
            count = (
                len(selected.get('value') or [])
                + (1 if ada_only['value'] else 0)
                + (1 if recent_only['value'] else 0)
            )
            filter_badge.set_text(str(count))
            filter_badge.set_visibility(count > 0)

        with ui.row().classes('wiz-toolbar w-full items-center'):
            with ui.row().classes('wiz-toolbar__actions items-center no-wrap'):
                ui.button('Add Member', icon='person_add', on_click=add_member).props('color=primary')
                with ui.button('Actions').props('flat color=primary icon-right=expand_more'):
                    # Filled after the table exists: the export reads its rows.
                    more_menu = ui.menu()
            # Filled after the table exists — the search and gear need a live table.
            search_slot = ui.row().classes('wiz-toolbar__search items-center no-wrap')

        with search_slot:
            search_box_slot = ui.element('div').classes('wiz-toolbar__grow')
            with ui.button('Filters', icon='filter_list', on_click=toggle_filters) \
                    .props('flat dense color=primary').classes('wiz-filter-toggle--inline'):
                filter_badge = ui.badge('0').props('floating color=primary')
            filter_badge.set_visibility(False)
            gear_slot = ui.element('div')
            # Lambda, not a direct reference: the toolbar is built before
            # table_view exists.
            refresh_button(lambda: table_view.refresh())

        # Filter card — between toolbar and table, matching match-filters-card pattern
        # Fields label themselves and the role multi-select reads as one line of
        # text, like the match board's filters (theme/tables/match_filters.py).
        with ui.card().classes('match-filters-card match-filters-card--compact user-filters') as filters_card:
            with ui.row().classes('match-filter-row'):
                role_options = {r.value: r.label for r in Role}
                role_options[_TA_FILTER] = 'Tournament Admin'
                role_options[_CC_FILTER] = 'Crew Coordinator'
                role_select = (
                    ui.select(options=role_options, value=[], multiple=True, label='Roles')
                    .classes('match-filter-field')
                    .props('outlined dense options-dense clearable')
                )
                role_select.bind_value(selected, 'value')
                recent_checkbox = ui.checkbox(
                    f'Joined in the last {RECENT_JOIN_WINDOW.days} days',
                ).bind_value(recent_only, 'value')
                if accommodations:
                    ada_checkbox = ui.checkbox(
                        'Needs ADA',
                    ).bind_value(ada_only, 'value')

        # Table — toolbar suppressed since we rendered it above
        table_view = UserTableView(
            columns=columns, get_query=get_query, show_toolbar=False,
            row_actions=_row_actions(emergency_contacts), table_key=TableKeys.ADMIN_USERS,
            before_refresh=load_row_data,
            decorate_row=decorate,
            extra_slots={'body-cell-joined_via': _JOINED_VIA_SLOT},
            empty_message=(
                'Nobody is a member of this community yet. Add Member brings an '
                'existing account in; granting someone a role makes them a member too.'
            ),
        )
        table_view.table.on(
            'remove_member',
            lambda e: table_view._bg(remove_member(e.args, context.client)),
        )
        if emergency_contacts:
            async def open_contact(row, client) -> None:
                with client:
                    member = await UserService().get_user_by_id(row['id'])
                    if member is None:
                        ui.notify('That account no longer exists', color='negative')
                        return
                    viewer = await get_user_from_session(app.storage.user)
                    if viewer is None:
                        return
                    await open_emergency_contact_dialog(viewer, member)
            table_view.table.on(
                'emergency_contact',
                lambda e: table_view._bg(open_contact(e.args, context.client)),
            )
        if on_open_ada is not None:
            async def open_ada(e) -> None:
                row = e.args.get('row', e.args) if isinstance(e.args, dict) else {}
                if row.get('ada_request_id'):
                    await on_open_ada(row['ada_request_id'])
            table_view.table.on('open_ada', open_ada)
        with search_box_slot:
            search_input(table_view.table, placeholder='Search people…', width='w-full')
        with gear_slot:
            preferences_button(table_view.table)
        with more_menu:
            menu_action('add', 'Add user', add_user)
            menu_action(
                'group_add', 'Import from Discord…',
                busy_click('Reading the Discord server…', import_from_discord),
            )
            # The plan's columns, not the shipped list: the export matches what
            # the person is looking at, hidden columns and all.
            csv_export_menu_item(
                'community-people',
                lambda: table_view._plan.columns if table_view._plan else columns,
                lambda: table_view.table.rows,
            )

        # Same rebind as the tab-switch below: an 'update:model-value' handler
        # is a client event, so a bare background task loses the tenant.
        role_select.on('update:model-value', lambda *_: table_view._bg(table_view.refresh()))
        role_select.on_value_change(lambda _: update_filter_badge())
        recent_checkbox.on('update:model-value', lambda *_: table_view._bg(table_view.refresh()))
        recent_checkbox.on_value_change(lambda _: update_filter_badge())
        if accommodations:
            ada_checkbox.on('update:model-value', lambda *_: table_view._bg(table_view.refresh()))
            ada_checkbox.on_value_change(lambda _: update_filter_badge())

        # Route through the view's _bg so refresh rebinds the tenant captured at
        # build — the selected_tab handler runs detached, and _format_user_row
        # relies on that tenant to keep the roles column scoped.
        def on_tab_selected():
            table_view._bg(table_view.refresh())
        ui.on('selected_tab', lambda e: on_tab_selected() if e.args == 'Members' else None)
