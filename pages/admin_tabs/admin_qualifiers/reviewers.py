"""Admin Async Qualifiers — the Reviewers tab.

A qualifier's ``admins`` are its reviewer set: who may work the queue, and who is
blocked from reviewing their own run. `add_admin`, `remove_admin` and
`list_admins` have existed on the service and over REST since PR 9, and no
control anywhere called them — so the one thing that decides who signs off on a
community's qualifying could only be changed with an API token. This is the tab
that changes that.

Built as a factory for the same reason as ``live_races``: the page owns ``state``
and the loaders, and this tab reads the former and triggers the latter.
"""

from typing import Any, Awaitable, Callable

from nicegui import ui

from application.services import UserService
from application.services.async_qualifier.async_qualifier_rules import display_name
from pages.admin_tabs.admin_qualifiers.shared import REVIEWERS_TAB
from theme.dialog._helpers import dialog_actions, form_dialog
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.empty_state import empty_panel
from theme.menu import menu_action, more_menu


def build_reviewers_tab(
    *,
    state: dict,
    service: Any,
    current: Callable[[], Awaitable[Any]],
    reload_tabs: Callable[..., Awaitable[None]],
    placeholder: Callable[[str], bool],
    notify_error: Callable[[Exception], None],
) -> Callable[[], None]:
    """Return the refreshable Reviewers view."""

    @ui.refreshable
    def reviewers_view() -> None:
        if state.get('shell') is None or placeholder(REVIEWERS_TAB):
            return
        ui.label(
            'A reviewer may approve or reject any run in this qualifier — except '
            'their own, which the service refuses. Everyone here can also edit this '
            'list.'
        ).classes('text-caption text-grey')
        with ui.row().classes('items-center'):
            ui.button('Add reviewer', icon='person_add', on_click=_open_add_dialog
                      ).props('color=primary').classes('wiz-write')
        reviewers = state['reviewers']
        if not reviewers:
            empty_panel('No qualifier-specific reviewers. Community staff can still '
                        'review every run here.', icon='group')
            return
        with ui.element('div').classes('wiz-tile-grid'):
            for person in reviewers:
                with ui.card():
                    with ui.row().classes('items-center full-width'):
                        ui.label(display_name(person)).classes('text-subtitle1')
                        if not person.discord_id:
                            ui.badge('no Discord', color='grey').tooltip(
                                'This reviewer will not receive queue notifications.')
                        ui.space()
                        with more_menu(display_name(person)):
                            menu_action('person_remove', 'Remove reviewer',
                                        lambda p=person: _remove(p), negative=True)

    def _open_add_dialog() -> None:
        with form_dialog('Add a reviewer', card_classes='dialog-card dialog-card--sm') as dialog:
            picker = ui.select(
                state['reviewer_options'], label='Person', with_input=True,
            ).classes('w-full')
            if not state['reviewer_options']:
                ui.label('Everyone in this community is already a reviewer.').classes(
                    'text-caption text-grey')

            async def submit() -> None:
                if not picker.value:
                    return
                try:
                    await service.add_admin(
                        await current(), state['managing'],
                        await UserService().get_user_by_id(int(picker.value)),
                    )
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                ui.notify('Reviewer added', color='positive')
                dialog.close()
                await reload_tabs(REVIEWERS_TAB)

            with dialog_actions().classes('justify-end'):
                ui.button('Cancel', on_click=dialog.close).props('flat')
                add = ui.button('Add', icon='person_add', on_click=submit).props('color=primary')
                add.bind_enabled_from(picker, 'value', lambda v: v is not None)
        dialog.open()

    def _remove(person) -> None:
        async def do_remove() -> None:
            try:
                await service.remove_admin(await current(), state['managing'], person)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            ui.notify('Reviewer removed', color='positive')
            await reload_tabs(REVIEWERS_TAB)

        ConfirmationDialog(
            message=f'Remove {display_name(person)} as a reviewer for this qualifier?',
            on_confirm=do_remove, confirm_text='Remove', title='Remove reviewer?',
        ).open()

    return reviewers_view
