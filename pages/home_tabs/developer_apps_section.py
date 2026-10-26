"""The Developer apps section on a member's profile.

Folded away by default: most members never build anything. Inside, the apps
you registered, one row each with a ⋮ for Edit, Rotate secret (websites only)
and Delete, and a New app button. The dialog asks what you are building first,
in plain words, because that picks the client type: a website with its own
server keeps a secret, anything else signs in with PKCE alone.

The owner sees how many people connected an app, never who. A client secret is
shown once, right after creating or rotating it.

Presentation only: service calls, no ORM.
"""

from typing import Dict, List, Optional

from nicegui import ui

from application.api_scopes import ApiScope
from application.services import OAuthAppService
from application.services.oauth_app_service import DESCRIPTION_MAX, MAX_APPS_PER_OWNER, NAME_MAX
from models import OAuthApp, OAuthClientType, User
from theme.clipboard import copy_to_clipboard
from theme.dialog._helpers import dialog_actions, form_dialog
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.help import help_icon
from theme.menu import menu_action, more_menu
from theme.notify import notify_error
from theme.steady import steady_refreshable

# Short names for the registration checkboxes. The consent screen uses the
# longer CONSENT_WORDING, since that is the person deciding.
SCOPE_LABELS: Dict[ApiScope, str] = {
    ApiScope.PROFILE: 'Profile',
    ApiScope.PROFILE_WRITE: 'Change profile',
    ApiScope.SCHEDULE_READ: 'Schedule',
    ApiScope.PLAY: 'Act as a player',
    ApiScope.CREW: 'Crew',
    ApiScope.VOLUNTEER: 'Volunteer shifts',
    ApiScope.STAFF_READ: 'Staff: see',
    ApiScope.STAFF_WRITE: 'Staff: change',
}

_KINDS = {
    OAuthClientType.CONFIDENTIAL: (
        'language', 'A website with its own server', 'Gets a client secret to keep on the server',
    ),
    OAuthClientType.PUBLIC: (
        'devices', 'An app on a phone or computer, or a browser-only site', 'No secret; signs in with PKCE',
    ),
}


def _kind_badge(app: OAuthApp) -> None:
    if app.client_type == OAuthClientType.CONFIDENTIAL:
        ui.badge('Website').props('outline color=primary')
    else:
        ui.badge('Device app').props('outline color=grey-8')


def show_app_credentials(client_id: str, secret: Optional[str]) -> None:
    """The client ID, and the secret once, each with its own Copy."""
    title = 'Copy your client secret now' if secret else 'Your client ID'
    with form_dialog(title, card_classes='dialog-card dialog-card--sm') as dialog:
        with ui.column().classes('q-pa-md gap-2 full-width'):
            if secret:
                ui.label(
                    "This is the only time it's shown. Lost it? Use Rotate secret… on the app."
                ).classes('text-warning')
                _copy_field('Client secret', secret)
            _copy_field('Client ID', client_id)
        with dialog_actions().classes('justify-end'):
            ui.button('Done', on_click=dialog.close).props('color=primary')
    dialog.open()


def _copy_field(label: str, value: str) -> None:
    with ui.row().classes('items-center no-wrap w-full gap-2'):
        ui.input(label, value=value).props('readonly outlined dense stack-label') \
            .classes('col font-mono')
        ui.button(icon='content_copy', on_click=lambda: copy_to_clipboard(value, label)) \
            .props('flat round dense').tooltip(f'Copy {label.lower()}')


async def render_developer_apps_section(user: User) -> None:
    service = OAuthAppService()
    counts: Dict[int, int] = {}

    @steady_refreshable
    async def app_list() -> None:
        apps = await service.list_my_apps(user)
        section.props(f'caption="{len(apps)} registered"' if apps else 'caption=""')
        if not apps:
            ui.label('No apps yet.').classes('text-muted')
        for i, oauth_app in enumerate(apps):
            counts[oauth_app.id] = await service.connected_user_count(user, oauth_app.id)
            people = counts[oauth_app.id]
            dot = 'warn' if oauth_app.disabled_at else ('on' if people else 'off')
            with ui.element('div').classes('wiz-pwebhook-row' + (' wiz-pwebhook-row--first' if i == 0 else '')):
                ui.element('span').classes(f'wiz-pwebhook-row__dot wiz-pwebhook-row__dot--{dot}')
                with ui.column().classes('gap-0 col min-w-0'):
                    with ui.row().classes('items-center gap-1'):
                        ui.label(oauth_app.name).classes('text-weight-medium')
                        _kind_badge(oauth_app)
                    ui.label(oauth_app.client_id).classes('wiz-pwebhook-row__url')
                    if oauth_app.disabled_at:
                        ui.label(f'Disabled: {oauth_app.disabled_reason or "by Wizzrobe"}') \
                            .classes('text-caption st-pending')
                    else:
                        noun = 'person' if people == 1 else 'people'
                        ui.label(f'{people} {noun} connected' if people else 'Nobody connected yet') \
                            .classes('text-caption text-muted')
                with more_menu(oauth_app.name):
                    menu_action('edit', 'Edit…', lambda a=oauth_app: open_dialog(a))
                    menu_action('badge', 'Show client ID', lambda a=oauth_app: show_app_credentials(a.client_id, None))
                    if oauth_app.client_type == OAuthClientType.CONFIDENTIAL:
                        menu_action('vpn_key', 'Rotate secret…', lambda a=oauth_app: confirm_rotate(a))
                    menu_action('delete', 'Delete…', lambda a=oauth_app: confirm_delete(a), negative=True)
        used.set_text(f'{len(apps)} of {MAX_APPS_PER_OWNER}')
        new_button.set_enabled(len(apps) < MAX_APPS_PER_OWNER)

    def confirm_rotate(oauth_app: OAuthApp) -> None:
        async def do_rotate() -> None:
            try:
                secret = await service.rotate_secret(user, oauth_app.id)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            show_app_credentials(oauth_app.client_id, secret)

        ConfirmationDialog(
            title=f'Rotate the secret for {oauth_app.name}?',
            message='The old secret keeps working for 24 hours, so you have time to deploy the new one.',
            confirm_text='Rotate secret',
            on_confirm=do_rotate,
        ).open()

    def confirm_delete(oauth_app: OAuthApp) -> None:
        async def do_delete() -> None:
            try:
                ended = await service.delete_app(user, oauth_app.id)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            noun = 'connection' if ended == 1 else 'connections'
            ui.notify(f'{oauth_app.name} deleted, {ended} {noun} ended', color='positive')
            app_list.refresh()

        people = counts.get(oauth_app.id, 0)
        ConfirmationDialog(
            title=f'Delete {oauth_app.name}?',
            message=(
                f'Everyone who connected it ({people}) is disconnected at once. This can’t be undone.'
                if people else 'This can’t be undone.'
            ),
            confirm_text='Delete',
            on_confirm=do_delete,
        ).open()

    def open_dialog(oauth_app: Optional[OAuthApp] = None) -> None:
        kind = {'value': oauth_app.client_type if oauth_app else OAuthClientType.CONFIDENTIAL}
        uri_inputs: List[ui.input] = []
        scope_boxes: Dict[ApiScope, ui.checkbox] = {}
        chosen = set(oauth_app.allowed_scopes or []) if oauth_app else {
            ApiScope.PROFILE.value, ApiScope.SCHEDULE_READ.value,
        }

        @ui.refreshable
        def kind_cards() -> None:
            for value, (icon, title, hint) in _KINDS.items():
                selected = kind['value'] == value
                classes = 'wiz-kind-card' + (' wiz-kind-card--on' if selected else '')
                if oauth_app is not None:
                    if not selected:
                        continue
                    classes += ' wiz-kind-card--fixed'
                card = ui.element('div').classes(classes)
                if oauth_app is None:
                    card.props(f'role=radio tabindex=0 aria-checked={"true" if selected else "false"}')
                    card.on('click', lambda _, v=value: pick_kind(v))
                    card.on('keydown.enter', lambda _, v=value: pick_kind(v))
                    card.on('keydown.space', lambda _, v=value: pick_kind(v))
                with card:
                    ui.icon(icon).classes('text-muted')
                    with ui.column().classes('gap-0 col min-w-0'):
                        ui.label(title).classes('text-weight-medium')
                        ui.label(hint).classes('text-caption text-muted')
                    if oauth_app is None:
                        ui.icon('radio_button_checked' if selected else 'radio_button_unchecked') \
                            .classes('text-primary' if selected else 'text-muted')

        def pick_kind(value: OAuthClientType) -> None:
            kind['value'] = value
            kind_cards.refresh()

        def add_uri(value: str = '') -> None:
            with uri_box:
                field = ui.input(
                    'Redirect URL' if not uri_inputs else 'Another redirect URL',
                    value=value, placeholder='https://your-app.example/callback',
                ).props('outlined stack-label autocomplete=off').classes('input-full-width font-mono')
                uri_inputs.append(field)

        async def save() -> None:
            uris = [u for u in ((f.value or '').strip() for f in uri_inputs) if u]
            scopes = [s.value for s, box in scope_boxes.items() if box.value]
            try:
                if oauth_app is None:
                    created, secret = await service.create_app(
                        user,
                        name=name_input.value or '',
                        client_type=kind['value'],
                        redirect_uris=uris,
                        allowed_scopes=scopes,
                        description=about_input.value or '',
                        homepage_url=homepage_input.value or '',
                    )
                else:
                    await service.update_app(
                        user, oauth_app.id,
                        name=name_input.value or '',
                        description=about_input.value or '',
                        homepage_url=homepage_input.value or '',
                        redirect_uris=uris,
                        allowed_scopes=scopes,
                    )
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            dialog.close()
            app_list.refresh()
            if oauth_app is None:
                show_app_credentials(created.client_id, secret)
            else:
                ui.notify('App saved', color='positive')

        title = f'Edit {oauth_app.name}' if oauth_app else 'New app'
        with form_dialog(title, card_classes='dialog-card dialog-card--sm') as dialog:
            with ui.column().classes('w-full gap-3'):
                ui.label('What are you building?' if oauth_app is None else 'Kind of app') \
                    .classes('text-weight-medium')
                with ui.column().classes('w-full gap-2').props('role=radiogroup aria-label="Kind of app"'):
                    kind_cards()
                name_input = ui.input('Name', value=oauth_app.name if oauth_app else '') \
                    .props(f'outlined stack-label maxlength={NAME_MAX}').classes('input-full-width')
                about_input = ui.input(
                    'What it does (optional)', value=oauth_app.description if oauth_app else '',
                ).props(f'outlined stack-label maxlength={DESCRIPTION_MAX}').classes('input-full-width')
                homepage_input = ui.input(
                    'Homepage (optional)', value=(oauth_app.homepage_url or '') if oauth_app else '',
                    placeholder='https://your-app.example',
                ).props('outlined stack-label autocomplete=off').classes('input-full-width font-mono')
                uri_box = ui.column().classes('w-full gap-2')
                for uri in (oauth_app.redirect_uris or []) if oauth_app else []:
                    add_uri(uri)
                if not uri_inputs:
                    add_uri()
                ui.button('Add another redirect URL', icon='add', on_click=lambda: add_uri()) \
                    .props('flat dense no-caps color=primary').classes('self-start')
                ui.label('It may ask for').classes('text-weight-medium')
                with ui.element('div').classes('wiz-pwebhook-areas w-full'):
                    for scope, label in SCOPE_LABELS.items():
                        scope_boxes[scope] = ui.checkbox(label, value=scope.value in chosen).props('dense')
                ui.label(
                    'People still choose what to allow when they connect, and staff items '
                    'only reach people who hold a staff role.'
                ).classes('text-caption text-muted')
            with dialog_actions().classes('justify-end'):
                ui.button('Cancel', on_click=dialog.close).props('flat')
                ui.button('Save' if oauth_app else 'Create app', on_click=save).props('color=primary')
        dialog.open()

    with ui.card().classes('card-full-width'):
        # A plain label, not a header slot: Quasar names a slotted header
        # "Expand", which axe flags against the visible text.
        with ui.expansion('Developer apps', icon='code').classes('w-full') \
                .props('header-class=text-weight-bold') as section:
            # The help button sits in the body: a button inside the expansion's
            # clickable header is a nested control (axe nested-interactive).
            with ui.row().classes('items-center gap-1 no-wrap w-full wiz-help-row'):
                ui.label(
                    "Build something that signs people in with Wizzrobe. You'll see how many "
                    'people connected, never who.'
                ).classes('text-muted text-caption')
                await help_icon('developer-apps', user=user)
            list_slot = ui.column().classes('w-full gap-0')
            with ui.row().classes('items-center gap-2 q-mt-sm'):
                new_button = ui.button('New app', icon='add', on_click=lambda: open_dialog()) \
                    .props('color=primary dense no-caps').classes('q-px-sm')
                used = ui.label('').classes('text-caption text-muted')
            with list_slot:
                await app_list()
