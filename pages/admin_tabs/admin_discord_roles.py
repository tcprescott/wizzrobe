"""Admin Role Mappings page: Discord roles and volunteer positions that grant app roles."""

import secrets
from urllib.parse import quote

from nicegui import app, background_tasks, context, ui

from application.services import (
    AuthService,
    DiscordLinkService,
    DiscordRoleMappingService,
    DiscordService,
    EventService,
    TenantService,
    TournamentService,
    get_user_from_session,
)
from application.services.discord.discord_link_service import connect_redirect_uri
from application.tenant_context import get_current_tenant_id, is_host_mode, require_tenant_id
from application.utils.mocks.mock_discord import is_mock_discord
from models import EventStatus, Role, TournamentGrant
from pages.admin_tabs.volunteer_role_mappings import TAB_LABEL, volunteer_role_mappings_section
from theme.busy import busy, busy_click, is_busy
from theme.dialog._helpers import dialog_actions, form_dialog
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.notify import notify_error
from theme.page_header import page_header
from theme.pickers import FilterMultiSelect, bulk_add_summary
from theme.tables.admin_crud import refresh_button, wire_tab_refresh
from theme.tables.mobile_grid import enable_mobile_grid
from theme.tables.preferences import TableKeys, search_input

# tenant_grantable, not every Role: a mapping must never hand out SUPER_ADMIN.
_ROLE_OPTIONS = {
    r.value: r.label for r in Role.tenant_grantable()
}
_GRANT_OPTIONS = {g.value: g.value.replace('_', ' ').title() for g in TournamentGrant}

# The prefix keeps a Role and a tournament grant of the same name apart once the value comes back.
_ROLE_PREFIX = 'role:'
_GRANT_PREFIX = 'grant:'

_DISCORD_DEFAULT_COLOR = '#99aab5'


def _mapping_key(mapping) -> tuple[int, str, int | None]:
    """The (discord role, picker value, tournament or event) triple the add dialog compares against."""
    if mapping.app_role is not None:
        return mapping.discord_role_id, f'{_ROLE_PREFIX}{mapping.app_role.value}', mapping.event_id
    return (
        mapping.discord_role_id,
        f'{_GRANT_PREFIX}{mapping.tournament_grant.value}',
        mapping.tournament_id,
    )


def _discord_role_extras(discord_roles: list[dict], mappings) -> dict[int, dict[str, str]]:
    """Colour dot and "already grants …" caption for each role in the picker.

    Two roles can share a name (Discord doesn't forbid it), so a duplicate also
    shows its id: the colour alone may not tell them apart.
    """
    grants: dict[int, list[str]] = {}
    for m in mappings:
        grant, target = _mapping_labels(m)
        if m.app_role is None:
            grant = f'{grant} on {target}'
        elif m.app_role.is_per_event:
            grant = f'{grant} in {target}'
        grants.setdefault(m.discord_role_id, []).append(grant)
    name_counts: dict[str, int] = {}
    for r in discord_roles:
        name_counts[str(r['name'])] = name_counts.get(str(r['name']), 0) + 1

    extras: dict[int, dict[str, str]] = {}
    for r in discord_roles:
        role_id = int(r['id'])
        color = int(r.get('color') or 0)
        caption = []
        if role_id in grants:
            caption.append('Grants ' + ', '.join(grants[role_id]))
        if name_counts[str(r['name'])] > 1:
            caption.append(f'ID {role_id}')
        extras[role_id] = {
            'color': f'#{color:06x}' if color else _DISCORD_DEFAULT_COLOR,
            'caption': ' · '.join(caption),
        }
    return extras


def _plural(count: int, noun: str) -> str:
    return f'{count} {noun}' + ('' if count == 1 else 's')


def _mapping_labels(mapping) -> tuple[str, str]:
    """(what it grants, where it applies) for one mapping row."""
    if mapping.app_role is not None:
        label = _ROLE_OPTIONS.get(mapping.app_role.value, mapping.app_role.value)
        if not mapping.app_role.is_per_event:
            return label, 'Community-wide'
        event = mapping.event
        if event is None:
            return label, 'No event: grants nothing'
        suffix = {EventStatus.DRAFT: ' (Draft)', EventStatus.ARCHIVED: ' (Archived)'}.get(event.status, '')
        return label, f'{event.name}{suffix}'
    grant = _GRANT_OPTIONS.get(mapping.tournament_grant.value, mapping.tournament_grant.value)
    tournament = mapping.tournament.name if mapping.tournament else f'#{mapping.tournament_id}'
    return grant, tournament

_ROW_ACTIONS = '''
    <q-btn flat round dense icon="delete" color="negative"
           @click="$parent.$emit('delete', props.row)" aria-label="Remove mapping">
        <q-tooltip>Remove mapping</q-tooltip>
    </q-btn>
'''


async def admin_discord_roles_page(volunteer_mappings: bool = False) -> None:
    actor = await get_user_from_session(app.storage.user)
    can_manage = await AuthService.can_grant_roles(actor)

    service = DiscordRoleMappingService()
    tenant_id = get_current_tenant_id()
    tenant = await TenantService.get_by_id(tenant_id) if tenant_id else None
    guild_id = tenant.discord_guild_id if tenant else None
    admin_path = f'/t/{tenant.slug}/admin' if tenant else '/admin'

    server_name = None
    if guild_id:
        ok, summary = await DiscordService().get_guild_summary(guild_id)
        if ok and isinstance(summary, dict):
            server_name = str(summary.get('name'))

    async def connect_server():
        if is_host_mode():
            ui.notify(
                'Connect Discord from the main site (…/t/<slug>/admin), not this custom domain.',
                color='warning',
            )
            return
        if not await DiscordLinkService.can_manage_link(actor):
            ui.notify('You need the Staff role to connect a Discord server.', color='warning')
            return
        if tenant is None:
            ui.notify('Select a community first.', color='warning')
            return
        # The callback lands on the bare platform host: the state must carry tenant, CSRF and return path.
        state = secrets.token_urlsafe(32)
        app.storage.user['discord_connect_state'] = state
        app.storage.user['discord_connect_tenant_id'] = tenant.id
        app.storage.user['discord_connect_return'] = admin_path
        if is_mock_discord():
            target = f'{connect_redirect_uri()}?state={quote(state)}&guild_id=1'
        else:
            target = DiscordLinkService.authorize_url(state)
        ui.navigate.to(target)

    async def disconnect_server(client):
        with client:
            try:
                current = await get_user_from_session(app.storage.user)
                if tenant is None:
                    ui.notify('Select a community first.', color='warning')
                    return
                await DiscordLinkService.disconnect(current, tenant)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            ui.notify('Discord server disconnected.', color='positive')
            ui.navigate.to(admin_path)

    def confirm_disconnect(server_label: str) -> None:
        client = context.client
        ConfirmationDialog(
            message=f'Disconnect {server_label}? Role sync stops until someone connects a '
                    'server again. The bot stays in the server.',
            on_confirm=lambda: background_tasks.create(disconnect_server(client)),
            confirm_text='Disconnect', title='Disconnect Discord server?',
        ).open()

    with ui.column().classes('page-container-narrow'):
        page_header('Role Mappings')

        ui.label('Discord roles').classes('subsection-title')
        with ui.card().classes('w-full'):
            if guild_id:
                label = server_name or f'Guild {guild_id}'
                with ui.row().classes('items-center gap-2'):
                    ui.icon('check_circle', color='positive')
                    ui.label(f'Connected to {label}').classes('text-bold')
                if not server_name:
                    ui.label(
                        'The bot is linked but not currently reachable in this server. '
                        'Role sync resumes once the bot is back online in it.'
                    ).classes('text-caption text-warning')
                if can_manage:
                    ui.button(
                        'Disconnect', icon='link_off',
                        on_click=lambda: confirm_disconnect(label),
                    ).props('outline color=negative')
            else:
                ui.label('No Discord server is connected to this community.').classes('text-bold')
                ui.label(
                    'Connecting opens Discord and adds the bot to a server you manage. '
                    'You must have the "Manage Server" permission on that server.'
                ).classes('text-caption text-grey')
                if can_manage and is_host_mode():
                    ui.label(
                        'Connect a Discord server from the main site (…/t/<slug>/admin); '
                        'this step is unavailable on a custom domain.'
                    ).classes('text-caption text-warning')
                elif can_manage:
                    ui.button(
                        'Connect Discord server', icon='hub', on_click=connect_server,
                    ).props('color=primary')

        if guild_id:
            ui.label(
                'When a user signs in, app roles are granted or revoked to match their '
                'Discord roles against these mappings. Manually-granted roles are preserved.'
            ).classes('text-caption text-grey')
            ui.label(
                'A mapping grants Staff or an online admin role across the community, an '
                'on-site role such as Proctor or Volunteer in one event, or Tournament Admin / '
                'Crew Coordinator on one tournament. A tournament grant stops applying once '
                'that tournament is no longer active, and an event grant once the event is archived.'
            ).classes('text-caption text-grey')

            # Keep the 'app_role' name: stored column preferences are keyed on it.
            columns = [
                {'name': 'id', 'label': 'ID', 'field': 'id', 'hidden': True},
                {'name': 'discord_role_name', 'label': 'Discord Role', 'field': 'discord_role_name', 'sortable': True},
                {'name': 'app_role', 'label': 'Grants', 'field': 'app_role', 'sortable': True},
                {'name': 'target', 'label': 'Applies To', 'field': 'target', 'sortable': True},
                {'name': 'actions', 'label': '', 'field': 'actions'},
            ]

            table_container = ui.column().classes('w-full')
            mappings: list = []

            async def refresh_table():
                mappings[:] = await service.list_mappings(guild_id)
                rows = []
                for m in mappings:
                    grant_label, target_label = _mapping_labels(m)
                    rows.append({
                        'id': m.id,
                        'discord_role_name': m.discord_role_name,
                        'app_role': grant_label,
                        'target': target_label,
                    })
                table.rows = rows
                table.selected = []
                table.update()
                _sync_remove_button()

            async def delete_mapping(row, client):
                async def do_remove() -> None:
                    current = await get_user_from_session(app.storage.user)
                    if current is None:
                        return
                    try:
                        await service.remove_mapping(row['id'], current)
                    except (ValueError, PermissionError) as e:
                        notify_error(e)
                        return
                    ui.notify('Mapping removed', color='positive')
                    await refresh_table()

                with client:
                    ConfirmationDialog(
                        f"Remove the mapping for {row['discord_role_name']}? Members lose what it "
                        'granted at their next sign-in or the next Sync All Users.',
                        on_confirm=do_remove, confirm_text='Remove', title='Remove mapping?',
                    ).open()

            async def remove_selected():
                ids = [row['id'] for row in table.selected]
                try:
                    current = await get_user_from_session(app.storage.user)
                    removed = await service.remove_mappings(ids, current)
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                ui.notify(f'Removed {_plural(removed, "mapping")}', color='positive')
                await refresh_table()

            def confirm_remove_selected():
                count = len(table.selected)
                if not count:
                    return
                ConfirmationDialog(
                    f'Remove {_plural(count, "mapping")}? Members lose what these '
                    'granted at their next sign-in or the next Sync All Users.',
                    on_confirm=remove_selected, confirm_text='Remove', title='Remove mappings',
                ).open()

            async def open_add_dialog():
                ok, roles_payload = await DiscordService().list_guild_roles(guild_id)
                if not ok:
                    ui.notify(str(roles_payload), color='warning')
                    return
                discord_roles = sorted(
                    (r for r in roles_payload if r.get('mappable', True)),
                    key=lambda r: str(r['name']).casefold(),
                )
                role_names = {int(r['id']): str(r['name']) for r in discord_roles}
                tournaments = await TournamentService().get_all_tournaments(active_only=True)
                tournament_options = {t.id: t.name for t in sorted(tournaments, key=lambda t: t.name.casefold())}

                existing = {_mapping_key(m) for m in mappings}
                discord_extras = _discord_role_extras(discord_roles, mappings)

                grant_options = {
                    **{f'{_ROLE_PREFIX}{value}': label for value, label in _ROLE_OPTIONS.items()},
                    **{f'{_GRANT_PREFIX}{value}': label for value, label in _GRANT_OPTIONS.items()},
                }
                grant_extras = {
                    **{f'{_GRANT_PREFIX}{value}': {'caption': 'On one tournament'} for value in _GRANT_OPTIONS},
                    **{f'{_ROLE_PREFIX}{r.value}': {'caption': 'In one event'} for r in Role.per_event()},
                }
                tenant_events = await EventService.events_for_tenant(require_tenant_id())
                event_options = {
                    e.id: e.name + (' (Draft)' if e.is_draft else '')
                    for e in tenant_events if not e.is_archived
                }
                default_event = next((e.id for e in tenant_events if e.is_default), None)

                def wants_tournament(selection) -> bool:
                    return any(str(v).startswith(_GRANT_PREFIX) for v in selection or [])

                def wants_event(selection) -> bool:
                    return any(
                        str(v).startswith(_ROLE_PREFIX) and Role(str(v)[len(_ROLE_PREFIX):]).is_per_event
                        for v in selection or []
                    )

                def target_of(selection: str):
                    if selection.startswith(_GRANT_PREFIX):
                        return tournament_select.value
                    if Role(selection[len(_ROLE_PREFIX):]).is_per_event:
                        return event_select.value
                    return None

                def planned() -> tuple[list[tuple[int, str, object]], int]:
                    """(pairs that would be created, pairs already mapped)."""
                    new, skipped = [], 0
                    for role_id in discord_select.value or []:
                        for selection in app_select.value or []:
                            target = target_of(selection)
                            if (int(role_id), selection, target) in existing:
                                skipped += 1
                            else:
                                new.append((int(role_id), selection, target))
                    return new, skipped

                def update_summary(_=None):
                    new, skipped = planned()
                    text = bulk_add_summary(
                        len(new), skipped,
                        empty_hint='Pick one or more Discord roles and what they grant.',
                    )
                    needs_tournament = (
                        wants_tournament(app_select.value) and tournament_select.value is None
                    )
                    if new and needs_tournament:
                        text += ' Pick the tournament for the tournament grants.'
                    summary.set_text(text)
                    add_button.set_enabled(bool(new) and not needs_tournament)

                with table_container, form_dialog('Add Role Mappings', card_classes='dialog-card dialog-card--sm') as dialog:
                    with ui.column().classes('q-pa-md gap-2 full-width'):
                        ui.label(
                            'Every Discord role you pick gets every grant you pick.'
                        ).classes('text-caption text-grey')
                        discord_select = FilterMultiSelect(
                            {rid: name for rid, name in role_names.items()},
                            label='Discord roles', extras=discord_extras,
                        ).classes('w-full')
                        app_select = FilterMultiSelect(
                            grant_options, label='Grants', extras=grant_extras,
                        ).classes('w-full')
                        tournament_select = ui.select(
                            options=tournament_options, label='Tournament', with_input=True,
                        ).classes('w-full')
                        tournament_select.bind_visibility_from(
                            app_select, 'value', backward=wants_tournament,
                        )
                        event_select = ui.select(
                            options=event_options, label='Event', value=default_event,
                        ).classes('w-full')
                        event_select.bind_visibility_from(app_select, 'value', backward=wants_event)
                        if not tournament_options:
                            ui.label(
                                'No active tournaments to grant on — create one first.'
                            ).classes('text-caption text-warning').bind_visibility_from(
                                app_select, 'value', backward=wants_tournament,
                            )
                        summary = ui.label().classes('text-caption')

                    async def submit():
                        new, _ = planned()
                        if not new:
                            ui.notify('Nothing new to add.', color='warning')
                            return
                        selections = list(app_select.value or [])
                        if wants_tournament(selections) and tournament_select.value is None:
                            ui.notify('Pick the tournament this grant applies to.', color='warning')
                            return
                        try:
                            current = await get_user_from_session(app.storage.user)
                            created, skipped = await service.add_mappings(
                                guild_id=guild_id,
                                discord_roles=[
                                    (int(rid), role_names[int(rid)]) for rid in discord_select.value
                                ],
                                app_roles=[
                                    Role(v[len(_ROLE_PREFIX):]) for v in selections
                                    if v.startswith(_ROLE_PREFIX)
                                ],
                                tournament_grants=[
                                    TournamentGrant(v[len(_GRANT_PREFIX):]) for v in selections
                                    if v.startswith(_GRANT_PREFIX)
                                ],
                                tournament_id=(
                                    int(tournament_select.value) if wants_tournament(selections) else None
                                ),
                                event_id=event_select.value if wants_event(selections) else None,
                                actor=current,
                            )
                        except (ValueError, PermissionError) as e:
                            notify_error(e)
                            return
                        dialog.close()
                        message = f'Added {_plural(created, "mapping")}'
                        if skipped:
                            message += f' ({skipped} already existed)'
                        ui.notify(message, color='positive')
                        await refresh_table()

                    with dialog_actions().classes('justify-end'):
                        ui.button('Cancel', on_click=dialog.close).props('flat')
                        add_button = ui.button('Add', icon='add', on_click=submit).props('color=primary')

                for select in (discord_select, app_select, tournament_select, event_select):
                    select.on_value_change(update_summary)
                update_summary()
                dialog.open()

            async def sync_all_users(client, button):
                if is_busy(button):
                    return
                with client:
                    with form_dialog(
                        'Re-sync Discord roles?', card_classes='dialog-card dialog-card--sm',
                    ) as confirm:
                        ui.label(
                            'This applies the current mappings to the community\'s members '
                            'and to anyone in the server holding a mapped role, creates an '
                            'account for those who have never signed in, and may take a moment.'
                        )
                        with dialog_actions().classes('justify-end'):
                            ui.button('Cancel', on_click=lambda: confirm.submit(False)).props('flat')
                            ui.button('Sync', icon='sync', on_click=lambda: confirm.submit(True)).props('color=primary')
                    if not await confirm:
                        return
                    try:
                        async with busy(button, 'Syncing roles…'):
                            current = await get_user_from_session(app.storage.user)
                            result = await service.sync_all_users(current)
                    except (ValueError, PermissionError) as e:
                        notify_error(e)
                        return
                    _show_sync_result(result)

            remove_button = None

            def _sync_remove_button(_=None):
                if remove_button is None:
                    return
                count = len(table.selected)
                remove_button.set_visibility(bool(count))
                remove_button.set_text(f'Remove {count} selected')

            with table_container:
                with ui.row().classes('full-width items-center'):
                    if can_manage:
                        ui.button('Add Mappings', icon='add', on_click=busy_click('Loading roles…', open_add_dialog)).props('color=primary').classes('wiz-write')
                        ui.button(
                            'Sync All Users', icon='sync',
                            on_click=lambda e: background_tasks.create(sync_all_users(context.client, e.sender)),
                        ).props('outline color=primary').tooltip(
                            'Apply current mappings to this community now'
                        )
                        remove_button = ui.button(
                            icon='delete', on_click=confirm_remove_selected,
                        ).props('outline color=negative')
                    ui.space()
                    refresh_button(refresh_table)
                search_row = ui.row().classes('full-width')

                table = ui.table(
                    columns=columns, rows=[], row_key='id',
                    selection='multiple' if can_manage else None,
                    on_select=_sync_remove_button,
                ).classes('w-full wiz-table')
                with search_row:
                    search_input(table, placeholder='Search mappings…')

                if can_manage:
                    table.add_slot('body-cell-actions', f'<q-td :props="props">{_ROW_ACTIONS}</q-td>')

                table.on('delete', lambda e: background_tasks.create(delete_mapping(e.args, context.client)))

                enable_mobile_grid(table, columns, actions=_ROW_ACTIONS if can_manage else '',
                                   table_key=TableKeys.ADMIN_DISCORD_ROLES,
                                   selectable=can_manage)
                _sync_remove_button()

            wire_tab_refresh(TAB_LABEL, refresh_table)
            background_tasks.create(refresh_table())

        if volunteer_mappings:
            ui.separator().classes('separator-spacing')
            await volunteer_role_mappings_section(can_manage)


def _show_sync_result(result: dict) -> None:
    """What one Sync press did, by name: who it created and whose roles moved.

    A count alone ("Synced 37 users") said nothing staff could check, and the
    new accounts looked the same as everyone else on the Users tab.
    """
    created = result.get('created') or []
    changed = result.get('changed') or []
    changed_names = {row['name'] for row in changed}
    with form_dialog('Discord roles synced') as dialog:
        with ui.column().classes('q-pa-md gap-1 full-width'):
            ui.label(
                f"Checked {result['users_processed']} "
                f"{'person' if result['users_processed'] == 1 else 'people'}: "
                f"{result['granted']} granted, {result['revoked']} revoked"
                + (f", {result['skipped']} skipped" if result.get('skipped') else '')
                + '.'
            ).classes('text-body2')
            if created:
                ui.label(f'New accounts ({len(created)})').classes('subsection-title q-mt-sm')
                ui.label(
                    'In the server with a mapped role, never signed in here. An '
                    'account becomes a member once a mapping grants it something.'
                ).classes('text-caption text-grey')
                for name in created:
                    ui.label(
                        name + (' (member now)' if name in changed_names else '')
                    ).classes('text-body2').style('overflow-wrap: anywhere')
            if changed:
                ui.label(f'Roles changed ({len(changed)})').classes('subsection-title q-mt-sm')
                for row in changed:
                    parts = []
                    if row['granted']:
                        parts.append('+ ' + ', '.join(_pretty(r) for r in row['granted']))
                    if row['revoked']:
                        parts.append('− ' + ', '.join(_pretty(r) for r in row['revoked']))
                    with ui.row().classes('items-baseline gap-2 no-wrap'):
                        ui.label(row['name']).classes('text-body2 text-bold') \
                            .style('overflow-wrap: anywhere')
                        ui.label('; '.join(parts)).classes('text-caption')
            if not created and not changed:
                ui.label('Nothing needed changing.').classes('text-caption text-grey q-mt-sm')
    dialog.open()


def _pretty(value: str) -> str:
    return value.replace('_', ' ').replace(':', ' #').title()
