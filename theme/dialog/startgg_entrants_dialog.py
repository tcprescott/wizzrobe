"""A linked start.gg event's entrants and the Wizzrobe member each maps to.

Most start.gg entrants map on their own: through the start.gg account a player
linked on their profile, or the Discord account they connected on start.gg. The
rest are listed first. A same-name member shows as a suggestion that staff
confirm or wave off ("Not them"); anyone can also be assigned by hand. A sync
never overwrites what's set here.
"""

from nicegui import app, ui

from application.services import StartggService, UserService, get_user_from_session
from theme.dialog._helpers import dialog_actions, dialog_header, mobile_sheet
from theme.notify import notify_error
from theme.steady import steady_refreshable


def _person(user) -> str:
    return user.display_name or user.username


def _suggestion_text(user) -> str:
    person = _person(user)
    tag = user.startgg_username
    if tag and tag.lower() != person.lower():
        return f'Same name as member {person} (linked start.gg as {tag})'
    return f'Same name as member {person}'


def _status(entrant) -> tuple:
    if entrant.user is None:
        if entrant.suggested_user is not None:
            return 'Suggested', 'info', 1
        return 'Not matched', 'warning', 0
    if entrant.user_assigned_manually:
        return 'Matched by hand', 'primary', 2
    return 'Linked account', 'positive', 3


class StartggEntrantsDialog:
    def __init__(self, tournament, on_change=None):
        self.tournament = tournament
        self.on_change = on_change

    async def open(self) -> None:
        service = StartggService()
        actor = await get_user_from_session(app.storage.user)
        members = {u.id: _person(u) for u in await UserService().get_community_people_summaries()}

        with ui.dialog() as dialog, ui.card().classes('dialog-card'):
            mobile_sheet(dialog)
            dialog_header(f'start.gg entrants: {self.tournament.name}', dialog)

            with ui.column().classes('q-pa-md w-full gap-3'):
                ui.label(
                    "Players who linked start.gg on their profile, or connected Discord on "
                    "start.gg, are matched on their own. Match the rest here so they can "
                    "book. A name match is only a suggestion until you confirm it."
                ).classes('text-caption text-muted')

                @steady_refreshable
                async def roster() -> None:
                    try:
                        entrants = await service.list_entrants(self.tournament.id, actor)
                    except (ValueError, PermissionError) as e:
                        ui.label(str(e)).classes('text-error')
                        return
                    if not entrants:
                        ui.label('No entrants yet. Sync the event first.').classes('text-muted')
                        return
                    matched = sum(1 for e in entrants if e.user is not None)
                    ui.label(f'{matched} of {len(entrants)} matched to a member').classes('text-bold')
                    for entrant in sorted(entrants, key=lambda e: (_status(e)[2], e.name.lower())):
                        entrant_row(entrant)

                def entrant_row(entrant) -> None:
                    label, color, _ = _status(entrant)
                    with ui.column().classes('w-full gap-1'):
                        with ui.row().classes('items-center no-wrap w-full gap-2'):
                            ui.label(entrant.name).classes('text-bold')
                            ui.badge(label).props(f'color={color}')
                        if entrant.user is not None:
                            ui.label(_person(entrant.user)).classes('text-caption text-muted')
                        if entrant.user is None and entrant.suggested_user is not None:
                            with ui.row().classes('items-center w-full gap-2'):
                                ui.label(_suggestion_text(entrant.suggested_user)).classes('text-caption grow')
                                ui.button(
                                    'Not them', on_click=lambda _=None, e=entrant: dismiss(e),
                                ).props('flat')
                                ui.button(
                                    'Confirm', icon='check',
                                    on_click=lambda _=None, e=entrant: assign(e, e.suggested_user.id),
                                ).props('flat color=primary')
                        with ui.row().classes('items-center w-full gap-2'):
                            options = dict(members)
                            if entrant.user is not None:
                                options.setdefault(entrant.user.id, _person(entrant.user))
                            picker = ui.select(
                                options=options, label='Member', with_input=True,
                                value=entrant.user.id if entrant.user is not None else None,
                            ).props('dense outlined').classes('grow min-w-56')
                            if entrant.user_assigned_manually:
                                ui.button(
                                    'Clear', icon='link_off',
                                    on_click=lambda _=None, e=entrant: clear(e),
                                ).props('flat color=negative').tooltip(
                                    'Go back to whoever their linked accounts point to'
                                )
                            ui.button(
                                'Assign', icon='person_add',
                                on_click=lambda _=None, e=entrant, p=picker: assign(e, p.value),
                            ).props('flat color=primary')
                        ui.separator()

                async def assign(entrant, user_id) -> None:
                    if not user_id:
                        ui.notify('Choose a member to match', color='warning')
                        return
                    try:
                        await service.assign_entrant(entrant.id, user_id, actor)
                    except (ValueError, PermissionError) as e:
                        notify_error(e)
                        return
                    ui.notify(f'{entrant.name} matched to {members.get(user_id, "that member")}',
                              color='positive')
                    await changed()

                async def dismiss(entrant) -> None:
                    try:
                        await service.dismiss_suggestion(entrant.id, actor)
                    except (ValueError, PermissionError) as e:
                        notify_error(e)
                        return
                    await changed()

                async def clear(entrant) -> None:
                    try:
                        await service.clear_entrant(entrant.id, actor)  # style-guide: exempt — clears a mapping, reassigned in place
                    except (ValueError, PermissionError) as e:
                        notify_error(e)
                        return
                    ui.notify(f"{entrant.name}'s match cleared", color='positive')
                    await changed()

                async def confirm_all() -> None:
                    try:
                        count = await service.confirm_all_suggestions(self.tournament.id, actor)
                    except (ValueError, PermissionError) as e:
                        notify_error(e)
                        return
                    ui.notify(f'Confirmed {count} suggestion{"" if count == 1 else "s"}.', color='positive')
                    await changed()

                async def changed() -> None:
                    if self.on_change:
                        await self.on_change()
                    roster.refresh()

                await roster()

            with dialog_actions().classes('justify-end'):
                ui.button('Confirm all suggestions', icon='done_all', on_click=confirm_all) \
                    .props('color=primary')

            dialog.open()
