"""A linked Challonge bracket's entrants and the Wizzrobe user each maps to.

An entrant added to the bracket by name has no Challonge account, so sync can't
resolve them to anyone and none of their matches can be scheduled. A tournament
editor assigns them here; the assignment survives a re-sync for as long as
the entrant stays on Challonge.
"""

from nicegui import app, ui

from application.services import ChallongeService, UserService, get_user_from_session
from theme.dialog._helpers import dialog_header, mobile_sheet
from theme.notify import notify_error
from theme.pickers import member_select
from theme.steady import steady_refreshable

_STATUS = {
    'unlinked': ('Not linked', 'warning'),
    'manual': ('Assigned by hand', 'primary'),
    'verified': ('Challonge account', 'positive'),
}
_ORDER = {'unlinked': 0, 'manual': 1, 'verified': 2}


def _person(user) -> str:
    return user.display_name or user.username


class ChallongeParticipantsDialog:
    def __init__(self, tournament, on_change=None):
        self.tournament = tournament
        self.on_change = on_change

    async def open(self) -> None:
        service = ChallongeService()
        actor = await get_user_from_session(app.storage.user)
        members = {u.id: _person(u) for u in await UserService().get_community_people_summaries()}

        with ui.dialog() as dialog, ui.card().classes('dialog-card'):
            mobile_sheet(dialog)
            dialog_header(f'Entrants — {self.tournament.name}', dialog)

            with ui.column().classes('q-pa-md w-full gap-3'):
                ui.label(
                    "Entrants added by name on Challonge can't be matched to anyone "
                    "automatically, and their matches can't be scheduled until "
                    "they're assigned. Re-syncing keeps what you set here unless the "
                    "entrant is removed on Challonge."
                ).classes('text-caption text-muted')

                @steady_refreshable
                async def roster() -> None:
                    try:
                        rows = await service.list_bracket_participants(self.tournament.id, actor)
                    except (ValueError, PermissionError) as e:
                        ui.label(str(e)).classes('text-error')
                        return
                    if not rows:
                        ui.label('No entrants yet. Sync the bracket first.').classes('text-muted')
                        return
                    for row in sorted(rows, key=lambda r: _ORDER[r['status']]):
                        entrant_row(row)

                def entrant_row(row: dict) -> None:
                    label, color = _STATUS[row['status']]
                    with ui.column().classes('w-full gap-1'):
                        with ui.row().classes('items-center no-wrap w-full gap-2'):
                            ui.label(row['name'] or 'Unnamed entrant').classes('text-bold')
                            ui.badge(label).props(f'color={color}')
                        if row['verified_user'] is not None:
                            ui.label(
                                f"Their Challonge account is linked to "
                                f"{_person(row['verified_user'])}; this assignment overrides it."
                            ).classes('text-caption text-warning')
                        with ui.row().classes('items-center w-full gap-2'):
                            if row['user'] is not None:
                                members.setdefault(row['user'].id, _person(row['user']))
                            picker = member_select(
                                members, label='Wizzrobe user',
                                value=row['user'].id if row['user'] is not None else None,
                            ).props('dense outlined').classes('grow min-w-56')
                            if row['status'] == 'manual':
                                ui.button(
                                    'Clear', icon='link_off',
                                    on_click=lambda _=None, r=row: clear(r),
                                ).props('flat color=negative').tooltip(
                                    'Go back to whoever their Challonge account is linked to'
                                )
                            ui.button(
                                'Assign', icon='person_add',
                                on_click=lambda _=None, r=row, p=picker: assign(r, p.value),
                            ).props('flat color=primary')
                        ui.separator()

                async def assign(row: dict, user_id) -> None:
                    if not user_id:
                        ui.notify('Choose someone to assign', color='warning')
                        return
                    try:
                        await service.assign_participant_user(row['id'], user_id, actor)
                    except (ValueError, PermissionError) as e:
                        notify_error(e)
                        return
                    ui.notify(f"{row['name']} assigned to {members.get(user_id, 'that user')}",
                              color='positive')
                    await changed()

                async def clear(row: dict) -> None:
                    try:
                        await service.clear_participant_user(row['id'], actor)  # style-guide: exempt — clears a mapping, reassigned in place
                    except (ValueError, PermissionError) as e:
                        notify_error(e)
                        return
                    ui.notify(f"{row['name']}'s assignment cleared", color='positive')
                    await changed()

                async def changed() -> None:
                    if self.on_change:
                        await self.on_change()
                    roster.refresh()

                await roster()

            dialog.open()
