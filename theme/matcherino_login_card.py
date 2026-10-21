"""The Matcherino login card on Admin → Check-in (Staff).

Holds the refresh token the check-in sync signs in with. The token is
**write-only**: the card shows whose Matcherino account it is, when it was saved
and by whom, never the token itself.

Getting a token is a bookmarklet away. Matcherino's web app keeps its login in a
``credentials`` cookie that page scripts can read, so the "Copy Matcherino
login" bookmark, clicked on matcherino.com, copies the refresh token out of it.
It copies rather than opening this page because the token should come from a
private window that is then closed without signing out (signing out, or a
Matcherino tab idling for six hours, makes Matcherino revoke it), and a private
window is not signed in to Wizzrobe. Copying also means no URL can ever pre-fill
this card with somebody else's token.
"""

import json
from typing import Optional
from urllib.parse import quote

from nicegui import background_tasks, context, ui

from application.services import MatcherinoLoginService
from application.utils.timezone import format_local_display
from theme.busy import busy_click
from theme.connection import REQUIRES_SOCKET_CLASS
from theme.dialog import ConfirmationDialog
from theme.notify import notify_error
from theme.steady import steady_refreshable
from theme.tables.admin_crud import current_actor

BOOKMARK_LABEL = 'Copy Matcherino login'

_NOT_ON_MATCHERINO = (
    'Drag this button to your bookmarks bar, then click the bookmark on matcherino.com '
    'while signed in.'
)
_NO_LOGIN = 'No Matcherino login found here. Sign in to matcherino.com, then click it again.'
_COPIED = 'Matcherino login copied. Paste it into the Matcherino login card in Wizzrobe.'

_STEPS = (
    f'Drag the "{BOOKMARK_LABEL}" button to your bookmarks bar. You only do this once.',
    'Open a private (incognito) window and sign in to matcherino.com there, as an '
    'account that is an admin of your venue.',
    'Click the bookmark. It copies the login.',
    'Close the private window without signing out, then paste here and press Save.',
)


def bookmarklet_href() -> str:
    """A ``javascript:`` URL that copies the refresh token out of Matcherino's cookie.

    Falls back to a ``prompt`` showing the token selected when the clipboard
    write is refused, so it still works where clipboard access isn't granted.
    """
    script = (
        '(()=>{'
        "if(!/(^|\\.)matcherino\\.com$/.test(location.hostname)){alert(%s);return}"
        "const c=document.cookie.split('; ').find(x=>x.startsWith('credentials='));"
        'let t=null;'
        "try{let v=c.slice(12);if(/^%%7b/i.test(v))v=decodeURIComponent(v);t=JSON.parse(v).refreshToken}catch(e){}"
        'if(!t){alert(%s);return}'
        'const done=()=>alert(%s);'
        "const show=()=>prompt('Copy the Matcherino login:',t);"
        'navigator.clipboard?navigator.clipboard.writeText(t).then(done,show):show()'
        '})()'
    ) % (json.dumps(_NOT_ON_MATCHERINO), json.dumps(_NO_LOGIN), json.dumps(_COPIED))
    return 'javascript:' + quote(script, safe='')


def _bookmark_button() -> None:
    ui.link(BOOKMARK_LABEL, bookmarklet_href()).props(
        'draggable=true title="Drag to your bookmarks bar"'
    ).classes(
        'q-btn q-btn--outline q-btn--rectangle q-btn--no-uppercase text-primary q-px-md q-py-xs no-underline'
    )


async def matcherino_login_card() -> None:
    service = MatcherinoLoginService()

    async def save(token_input, client) -> None:
        with client:
            actor = await current_actor()
            if actor is None:
                return
            try:
                status = await service.set_login(actor, token_input.value)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            token_input.value = ''
            account = f' as account #{status.matcherino_user_id}' if status.matcherino_user_id else ''
            ui.notify(f'Matcherino accepted the token. Ticket sync now signs in{account}.',
                      color='positive')
            login_section.refresh()

    async def remove(client) -> None:
        with client:
            actor = await current_actor()

            async def do_remove() -> None:
                try:
                    await service.clear_login(actor)
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                ui.notify('Matcherino login removed.', color='positive')
                login_section.refresh()

            ConfirmationDialog(
                message='Remove the saved Matcherino login? Ticket sync stops until someone adds a '
                        'new one, and the alert recipient is cleared with it.',
                on_confirm=do_remove, confirm_text='Remove',
            ).open()

    async def set_recipient(user_id: Optional[int], client) -> None:
        with client:
            actor = await current_actor()
            if actor is None:
                return
            try:
                status = await service.set_alert_user(actor, user_id)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                login_section.refresh()
                return
            who = status.alert_user or 'nobody'
            ui.notify(f'Refused-login alerts now go to {who}.', color='positive')

    @steady_refreshable
    async def login_section() -> None:
        actor = await current_actor()
        try:
            status = await service.status(actor)
            candidates = await service.alert_candidates(actor) if status.configured else []
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        with ui.card().classes('w-full'):
            ui.label('Matcherino login').classes('text-subtitle2 text-bold')
            if status.configured:
                account = (f'Matcherino account #{status.matcherino_user_id}'
                           if status.matcherino_user_id else 'A Matcherino account')
                saved = format_local_display(status.updated_at) if status.updated_at else 'an unknown time'
                by = f' by {status.updated_by}' if status.updated_by else ''
                ui.label(f'{account} · saved {saved}{by}').classes('text-caption text-positive')
            else:
                ui.label(
                    "Not set. Ticket sync can't read your venue's badge sales until you add one."
                ).classes('text-caption text-warning')
            token_input = ui.input('Refresh token').props(
                'type=password autocomplete=off dense outlined'
            ).classes('w-full')
            if status.configured:
                token_input.props('hint="Saving a new token replaces the one stored"')
            with ui.element('div').classes('wiz-pair-actions'):
                if status.configured:
                    ui.button(
                        'Remove', icon='delete',
                        on_click=lambda: background_tasks.create(remove(context.client)),
                    ).props('flat color=negative').classes(REQUIRES_SOCKET_CLASS)
                ui.button(
                    'Save', icon='save',
                    on_click=busy_click('Checking…', lambda: save(token_input, context.client)),
                ).props('color=primary').classes(REQUIRES_SOCKET_CLASS)
            if status.configured:
                options = {0: 'Nobody', **{u.id: u.preferred_name for u in candidates}}
                ui.select(
                    options, value=status.alert_user_id or 0, label='DM when Matcherino refuses it',
                    on_change=lambda e: background_tasks.create(
                        set_recipient(e.value or None, context.client)
                    ),
                ).props('dense outlined').classes('w-full sm:w-72').classes(REQUIRES_SOCKET_CLASS)
            with ui.expansion('Where do I get one?').classes('w-full text-caption'):
                _bookmark_button()
                for number, step in enumerate(_STEPS, start=1):
                    ui.label(f'{number}. {step}').classes('text-caption')
                ui.label(
                    "Why a private window: signing out of Matcherino, or leaving a Matcherino tab "
                    'idle for six hours (it signs out on its own), makes Matcherino throw the token '
                    "away. A session you never come back to can't do either. Treat the token like "
                    'a password: whoever holds it is that Matcherino account.'
                ).classes('text-caption text-grey-7')
                ui.label(
                    'No bookmark? In DevTools, open Application → Cookies → matcherino.com and '
                    'paste the whole value of the credentials cookie.'
                ).classes('text-caption text-grey-7')

    await login_section()
