"""The tournament's entrants, managed from the tournament.

The Tournaments tab invites the user to "click a player count to manage
entrants" and this dialog used to be a read-only list plus Close. It is
read-write now, over the community's members — which is what makes it safe to
write to at all: before membership scoping an "add entrant" picker would have
offered every account on the platform.
"""

from nicegui import app, ui

from application.services import TournamentService, UserService, get_user_from_session
from theme.dialog._helpers import dialog_header, mobile_sheet
from theme.dialog.drop_player_dialog import DropPlayerDialog
from theme.steady import steady_refreshable


class TournamentPlayersDialog:
    def __init__(self, tournament, on_change=None):
        self.tournament = tournament
        self.dialog = None
        self.on_change = on_change

    async def open(self):
        service = TournamentService()
        actor = await get_user_from_session(app.storage.user)

        with ui.dialog() as dialog, ui.card().classes('dialog-card'):
            self.dialog = dialog
            mobile_sheet(dialog)
            dialog_header(f'Players — {self.tournament.name}', dialog)

            with ui.column().classes('q-pa-md w-full gap-2'):
                if self.tournament.challonge_tournament_id:
                    ui.label(
                        "This list follows the Challonge bracket. Each sync adds its "
                        "linked entrants and removes anyone who isn't on it."
                    ).classes('text-caption text-muted')

                @steady_refreshable
                async def roster() -> None:
                    players = await service.get_enrolled_players(self.tournament)
                    if not players:
                        ui.label(
                            'No players enrolled yet — a match can only be '
                            'scheduled between entrants.'
                        ).classes('text-grey-7')
                        return
                    for tp in players:
                        user = tp.user
                        with ui.row().classes('items-center justify-between no-wrap w-full'):
                            ui.label(f'{user.display_name or user.username} '
                                     f'(Discord: {user.discord_id})')
                            ui.button(
                                'Remove', icon='person_remove',
                                on_click=lambda _=None, u=user: confirm_remove(u),
                            ).props('flat dense color=negative')

                async def confirm_remove(user) -> None:
                    async def done() -> None:
                        if self.on_change:
                            await self.on_change()
                        await refresh_candidates()
                        roster.refresh()
                    await DropPlayerDialog(self.tournament, user, on_done=done).open()

                async def add() -> None:
                    if not add_select.value:
                        ui.notify('Choose someone to enrol', color='warning')
                        return
                    user = await UserService().get_user_by_id(add_select.value)
                    if user is None:
                        ui.notify('That account no longer exists', color='negative')
                        return
                    try:
                        await service.enroll_player(self.tournament, user, actor)
                    except (ValueError, PermissionError) as e:
                        ui.notify(str(e), color='warning')
                        return
                    ui.notify(f'{user.display_name or user.username} enrolled',
                              color='positive')
                    add_select.value = None
                    if self.on_change:
                        await self.on_change()
                    await refresh_candidates()
                    roster.refresh()

                async def refresh_candidates() -> None:
                    enrolled = {
                        tp.user_id
                        for tp in await service.get_enrolled_players(self.tournament)
                    }
                    members = await UserService().get_community_people_summaries()
                    add_select.options = {
                        u.id: (u.display_name or u.username)
                        for u in members if u.id not in enrolled
                    }
                    add_select.update()

                await roster()

                ui.separator()
                with ui.row().classes('items-center no-wrap w-full gap-2'):
                    add_select = ui.select(
                        options={}, label='Enrol a member', with_input=True,
                    ).classes('grow')
                    ui.button('Enrol', icon='person_add', on_click=add).props('color=primary')
                await refresh_candidates()

            dialog.open()
