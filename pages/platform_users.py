"""The Users tab of ``/platform``: every account, and password sign-in.

A super-admin gives someone a sign-in name and password here (or creates an
account that has no Discord at all) so they can sign in at the unlisted
``/password-login`` page, and deactivates or reactivates accounts. Roles are
shown, not edited: each community grants its own from Admin → Users.
"""

from typing import Any, Callable, Optional

from nicegui import ui

from application.services import PasswordAuthService
from application.services.password_auth_service import (
    MIN_PASSWORD_LENGTH,
    generate_password,
    normalize_login_name,
)
from models import Role, User
from pages.platform_shared import ACTIVE_ICON_SLOT, current_actor, section_intro
from theme.clipboard import copy_to_clipboard
from theme.dialog import ConfirmationDialog
from theme.dialog._helpers import dialog_actions, form_dialog, submit_on_enter
from theme.menu import SlotItem, slot_more_menu
from theme.notify import notify_error
from theme.tables.preferences import (
    TableKeys,
    customize_table,
    preferences_button,
    search_input,
)
from theme.url_state import bind_url

_NAME = '''
    <div class="text-weight-bold">{{ props.row.display }}</div>
    <div class="text-caption text-grey-7">{{ props.row.handle }}</div>
'''
_COMMUNITIES = '''
    <div class="row q-gutter-xs">
        <span v-for="c in props.row.communities" :key="c" class="wiz-chip wiz-chip--neutral">{{ c }}</span>
        <span v-if="!props.row.communities.length" class="text-grey-7">—</span>
    </div>
'''
_SIGN_IN = '''
    <div class="row q-gutter-xs">
        <span v-if="props.row.has_discord" class="wiz-chip wiz-chip--neutral">Discord</span>
        <span v-if="props.row.login_name" class="wiz-chip wiz-chip--ok">Password · {{ props.row.login_name }}</span>
        <span v-if="props.row.super_admin" class="wiz-chip wiz-chip--neutral">Super admin</span>
    </div>
'''
_ACTIONS = slot_more_menu('props.row.display', [
    SlotItem('key', 'Set password…', 'set_password', v_if='!props.row.login_name && !props.row.super_admin'),
    SlotItem('key', 'Change password…', 'set_password', v_if='props.row.login_name && !props.row.super_admin'),
    SlotItem('person', 'Reactivate account', 'reactivate', v_if='!props.row.active_bool'),
    SlotItem('key_off', 'Remove password…', 'remove_password', negative=True,
             v_if='props.row.login_name && props.row.has_discord'),
    SlotItem('block', 'Deactivate account…', 'deactivate', negative=True,
             v_if='props.row.active_bool && !props.row.is_me'),
])


def _row(user: User, me: User) -> dict[str, Any]:
    communities = sorted({m.tenant.name for m in user.tenant_memberships})  # type: ignore[attr-defined]
    super_admin = any(r.role == Role.SUPER_ADMIN and r.tenant_id is None for r in user.roles)  # type: ignore[attr-defined]
    handle = f'@{user.username}' if user.discord_id else 'No Discord'
    return {
        'id': user.id,
        'display': user.preferred_name,
        'handle': handle,
        'name': ' '.join(filter(None, [
            user.preferred_name, user.username, str(user.discord_id or ''), user.login_name or '',
        ])),
        'discord_id': str(user.discord_id) if user.discord_id else '—',
        'communities': communities,
        'communities_text': ', '.join(communities),
        'has_discord': bool(user.discord_id),
        'login_name': user.login_name or '',
        'super_admin': super_admin,
        'active_bool': user.is_active,
        'is_me': user.id == me.id,
    }


async def render_users_section(user: User) -> None:
    """The Users tab: search, a password filter, New user, and the table."""
    service = PasswordAuthService()
    actions = section_intro(
        'Every account on the platform. Give someone a password to let them sign '
        'in without Discord; nothing links to the password sign-in page, so share '
        'its address with them.'
    )
    state: dict[str, Any] = {'rows': [], 'only_password': False}
    with actions:
        ui.button('New user', icon='person_add', on_click=lambda: _open_new_user_dialog(reload)) \
            .props('color=primary')
    header_row = ui.row().classes('w-full items-center gap-2')

    columns: list[dict] = [
        {'name': 'name', 'label': 'Name', 'field': 'name', 'align': 'left', 'sortable': True},
        {'name': 'discord_id', 'label': 'Discord ID', 'field': 'discord_id', 'align': 'left'},
        {'name': 'communities', 'label': 'Communities', 'field': 'communities_text', 'align': 'left'},
        {'name': 'sign_in', 'label': 'Sign-in', 'field': 'login_name', 'align': 'left'},
        {'name': 'active', 'label': 'Active', 'field': 'active_bool', 'align': 'left'},
        {'name': 'actions', 'label': '', 'field': 'actions', 'align': 'right'},
    ]
    table = ui.table(columns=columns, rows=[], row_key='id', pagination=25).classes(
        'w-full wiz-table').props(':grid="Quasar.Screen.lt.md"')
    table.add_slot('body-cell-name', f'<q-td :props="props">{_NAME}</q-td>')
    table.add_slot('body-cell-discord_id', '''
        <q-td :props="props"><span class="cell-timestamp">{{ props.row.discord_id }}</span></q-td>
    ''')
    table.add_slot('body-cell-communities', f'<q-td :props="props">{_COMMUNITIES}</q-td>')
    table.add_slot('body-cell-sign_in', f'<q-td :props="props">{_SIGN_IN}</q-td>')
    table.add_slot('body-cell-active', ACTIVE_ICON_SLOT)
    table.add_slot('body-cell-actions', f'<q-td :props="props" style="white-space:nowrap">{_ACTIONS}</q-td>')
    table.add_slot('item', '''
        <div class="q-pa-xs col-xs-12 col-sm-6">
            <q-card bordered flat class="q-pa-sm wiz-grid-card">
                <div class="row items-start justify-between no-wrap q-mb-xs">
                    <div>''' + _NAME + '''</div>
                    <q-icon :name="props.row.active_bool ? 'check_circle' : 'cancel'"
                            :color="props.row.active_bool ? 'positive' : 'negative'" size="sm">
                        <q-tooltip>{{ props.row.active_bool ? 'Active' : 'Inactive' }}</q-tooltip>
                    </q-icon>
                </div>
                <div class="row q-mb-xs">
                    <div class="col-4 text-grey-7 text-caption">Discord ID</div>
                    <div class="col-8 cell-timestamp" style="overflow-wrap:anywhere">{{ props.row.discord_id }}</div>
                </div>
                <div class="row q-mb-xs">
                    <div class="col-4 text-grey-7 text-caption">Communities</div>
                    <div class="col-8">''' + _COMMUNITIES + '''</div>
                </div>
                <div class="row items-center q-mb-xs">
                    <div class="col-4 text-grey-7 text-caption">Sign-in</div>
                    <div class="col-8">''' + _SIGN_IN + '''</div>
                </div>
                <div class="row justify-end items-center">''' + _ACTIONS + '''</div>
            </q-card>
        </div>
    ''')
    customize_table(table, columns, key=TableKeys.PLATFORM_USERS)

    def show() -> None:
        rows = state['rows']
        table.rows = [r for r in rows if r['login_name']] if state['only_password'] else rows
        table.update()

    async def reload() -> None:
        me = await current_actor()
        if me is None:
            return
        try:
            accounts = await service.list_accounts(me)
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        state['rows'] = [_row(u, me) for u in accounts]
        show()

    def on_filter(e) -> None:
        state['only_password'] = e.value == 'password'
        show()

    with header_row:
        search_input(table, placeholder='Search name, handle or Discord ID…', width='flex-1 sm:flex-none sm:w-64')
        accounts = ui.toggle({'all': 'Everyone', 'password': 'Has password'}, value='all', on_change=on_filter) \
            .props('no-caps dense unelevated no-wrap').classes('wiz-segmented')
        bind_url('accounts', accounts, default='all', history='replace')
        preferences_button(table)

    async def on_set_password(e) -> None:
        await _open_password_dialog(e.args, reload)

    async def on_remove_password(e) -> None:
        row = e.args

        async def confirmed() -> None:
            await _act(row['id'], lambda svc, actor, target: svc.remove_password(actor, target),
                       f"{row['display']} can no longer sign in with a password.", reload)

        ConfirmationDialog(
            f"Remove {row['display']}'s password? They'll be signed out of any password "
            'session and can still sign in with Discord.',
            title='Remove password', confirm_text='Remove password', on_confirm=confirmed,
        ).open()

    async def on_deactivate(e) -> None:
        row = e.args

        async def confirmed() -> None:
            await _act(row['id'], lambda svc, actor, target: svc.set_active(actor, target, False),
                       f"{row['display']}'s account is deactivated.", reload)

        ConfirmationDialog(
            f"Deactivate {row['display']}? They're signed out everywhere and can't sign "
            'in to any community until you reactivate the account.',
            title='Deactivate account', confirm_text='Deactivate', on_confirm=confirmed,
        ).open()

    async def on_reactivate(e) -> None:
        row = e.args
        await _act(row['id'], lambda svc, actor, target: svc.set_active(actor, target, True),
                   f"{row['display']}'s account is active again.", reload)

    table.on('set_password', on_set_password)
    table.on('remove_password', on_remove_password)
    table.on('deactivate', on_deactivate)
    table.on('reactivate', on_reactivate)

    await reload()


async def _act(user_id: int, action: Callable, done: str, reload) -> None:
    from application.services import UserService

    actor = await current_actor()
    target = await UserService().get_user_by_id(user_id)
    if actor is None or target is None:
        ui.notify('That account is no longer here. Reload the page.', color='warning')
        return
    try:
        await action(PasswordAuthService(), actor, target)
    except (ValueError, PermissionError) as e:
        notify_error(e)
        return
    ui.notify(done, color='positive')
    await reload()


def _field_error(field: ui.input, message: Optional[str]) -> None:
    """Show ``message`` under ``field`` (or clear it). Set on ``_props`` directly
    because the message is free text that a props string would have to quote."""
    field._props['error'] = bool(message)
    field._props['error-message'] = message or ''
    field.update()


def _password_fields() -> tuple[ui.input, Callable[[], None]]:
    """The password box with Generate and Copy, shared by both dialogs."""
    password = ui.input(
        'Password', password=True, password_toggle_button=True,
    ).props(f'outlined dense autocomplete=new-password hint="At least {MIN_PASSWORD_LENGTH} '
            "characters. Share it with them directly; it won't be shown again.\"").classes('w-full')

    def generate() -> None:
        password.value = generate_password()
        # Shown on purpose, so it can be read out or copied before the dialog closes.
        password.props('type=text')

    with ui.row().classes('items-center gap-2'):
        ui.button('Generate', icon='casino', on_click=generate).props('outline no-caps')
        ui.button('Copy', icon='content_copy',
                  on_click=lambda: copy_to_clipboard(password.value or '', 'Password')) \
            .props('flat no-caps')
    return password, generate


def _route_error(message: str, name: ui.input, password: ui.input, other: Optional[ui.input] = None) -> None:
    for field in (name, password) + ((other,) if other else ()):
        _field_error(field, None)
    lowered = message.lower()
    if 'sign-in name' in lowered:
        _field_error(name, message)
    elif 'character' in lowered:
        _field_error(password, message)
    elif other is not None and 'display name' in lowered:
        _field_error(other, message)
    else:
        ui.notify(message, color='warning')


async def _open_password_dialog(row: dict, reload) -> None:
    from application.services import UserService

    target = await UserService().get_user_by_id(row['id'])
    if target is None:
        ui.notify('That account is no longer here. Reload the page.', color='warning')
        return
    verb = 'Change' if target.login_name else 'Set'
    suggested = target.login_name or normalize_login_name(target.username)
    with form_dialog(f'{verb} password for {target.preferred_name}',
                     card_classes='dialog-card dialog-card--sm gap-3') as dialog:
        name = ui.input('Sign-in name', value=suggested).props(
            'outlined dense autocapitalize=none spellcheck=false',
        ).classes('w-full')
        password, _ = _password_fields()

        async def submit() -> None:
            actor = await current_actor()
            try:
                await PasswordAuthService().set_password(actor, target, name.value, password.value or '')
            except PermissionError as e:
                notify_error(e)
                return
            except ValueError as e:
                _route_error(str(e), name, password)
                return
            ui.notify(f'{target.preferred_name} can sign in as {normalize_login_name(name.value)}.',
                      color='positive')
            dialog.close()
            await reload()

        with dialog_actions().classes('justify-end'):
            ui.button('Cancel', on_click=dialog.close).props('flat')
            ui.button(f'{verb} password', on_click=submit, color='primary')
        submit_on_enter(dialog, submit)
    dialog.open()


def _open_new_user_dialog(reload) -> None:
    with form_dialog('New user', card_classes='dialog-card dialog-card--sm gap-3') as dialog:
        ui.label(
            'An account with no Discord that signs in only with this password. '
            "Add it to a community from that community's Admin → Users."
        ).classes('text-caption text-grey-7')
        display = ui.input('Display name').props('outlined dense').classes('w-full')
        name = ui.input('Sign-in name').props(
            'outlined dense autocapitalize=none spellcheck=false',
        ).classes('w-full')
        password, _ = _password_fields()

        async def submit() -> None:
            actor = await current_actor()
            try:
                created = await PasswordAuthService().create_account(
                    actor, display.value or '', name.value or '', password.value or '',
                )
            except PermissionError as e:
                notify_error(e)
                return
            except ValueError as e:
                _route_error(str(e), name, password, display)
                return
            ui.notify(f'{created.preferred_name} can sign in as {created.login_name}.', color='positive')
            dialog.close()
            await reload()

        with dialog_actions().classes('justify-end'):
            ui.button('Cancel', on_click=dialog.close).props('flat')
            ui.button('Create user', on_click=submit, color='primary')
        submit_on_enter(dialog, submit)
    dialog.open()
