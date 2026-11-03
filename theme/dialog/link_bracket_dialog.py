"""Link a bracket: the Tournaments row ⋮ "Link a bracket…" dialog.

One field takes a Challonge or start.gg link and the site is read off the
link, so staff don't pick a site first. A bare Challonge id still works when
Challonge is the only site the viewer can link.
"""

from typing import Awaitable, Callable, Optional, Sequence

from nicegui import app, ui

from application.services import bracket_sources, get_user_from_session
from theme.busy import busy_click
from theme.dialog._helpers import dialog_actions, form_dialog, submit_on_enter
from theme.notify import notify_error


def open_link_bracket_dialog(
    tournament, sites: Sequence, on_linked: Optional[Callable[[], Awaitable[None]]] = None,
) -> None:
    """``sites`` are the live bracket sources this viewer may link."""
    labels = ' or '.join(s.label for s in sites)
    keys = {s.key for s in sites}

    with form_dialog(f'Link a bracket · {tournament.name}', card_classes='dialog-card dialog-card--sm') as dialog:
        with ui.column().classes('w-full gap-2'):
            link_input = ui.input(
                f'{labels} link',
                placeholder='https://speedgaming.challonge.com/…' if 'challonge' in keys
                else 'https://www.start.gg/tournament/…/event/…',
            ).props('autofocus').classes('input-full-width')
            ui.label(
                'Wizzrobe copies its entrants and matches here. If you unlink it later, '
                'scheduled matches stay as they are.'
            ).classes('text-caption text-muted')

        async def submit() -> None:
            value = (link_input.value or '').strip()
            if not value:
                ui.notify(f'Paste the link to the {labels} bracket.', color='warning')
                return
            site = bracket_sources.site_for_link(value)
            if site is None and len(keys) == 1:
                site = next(iter(keys))
            if site is None:
                ui.notify(f'Paste the full {labels} link so Wizzrobe knows which site it is.',
                          color='warning')
                return
            if site not in keys:
                label = bracket_sources.site_labels().get(site, site)
                ui.notify(f"{label} isn't available to you here.", color='warning')
                return
            actor = await get_user_from_session(app.storage.user)
            if actor is None:
                return
            try:
                summary = await bracket_sources.link(site, tournament, value, actor)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            dialog.close()
            ui.notify(f'{tournament.name}: {summary}.', color='positive')
            if on_linked is not None:
                await on_linked()

        with dialog_actions().classes('justify-end'):
            ui.button('Cancel', on_click=dialog.close).props('flat')
            ui.button('Link & Sync', icon='sync', on_click=busy_click('Syncing…', submit)).props('color=primary')
        submit_on_enter(dialog, submit)
    dialog.open()
