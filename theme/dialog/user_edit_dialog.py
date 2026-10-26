from nicegui import app, ui

from application.event_context import get_current_event_id
from application.services import (
    AuthService,
    ChallongeService,
    EventService,
    FeatureFlagService,
    TournamentService,
    UserService,
    get_user_from_session,
)
from application.tenant_context import require_tenant_id
from models import FeatureFlag, User
from theme.dialog._helpers import (
    dialog_actions,
    dialog_header,
    dialog_more_menu,
    mobile_sheet,
    submit_on_enter,
)
from theme.dialog.role_grants import RoleGrantsEditor
from theme.dialog.send_message_dialog import SendMessageDialog
from theme.menu import menu_action
from theme.notify import notify_error
from theme.pickers import CHIP_PROPS


class BaseUserDialog:
    """Base class for user dialogs with common functionality."""

    def __init__(self, user: User = None, on_submit=None):
        self.user = user
        self.on_submit = on_submit
        self.dialog = None
        self._initial_updated_at = user.updated_at if user else None
        self.user_service = UserService()
        self.tournament_service = TournamentService()

    async def _get_tournament_data(self):
        """Fetch tournament data for the user."""
        tournaments = await self.tournament_service.get_all_tournaments(active_only=True)
        user_tournaments = []
        if self.user:
            user_tournaments = await self.tournament_service.get_enrolled_players_by_user(self.user)
        selected_tournament_ids = [tp.tournament_id for tp in user_tournaments]
        tournament_options = {str(t.id): t.name for t in tournaments}
        return tournaments, user_tournaments, selected_tournament_ids, tournament_options

    async def _update_tournament_enrollments(self, user_tournaments, tournament_multiselect, actor):
        selected_ids = set(map(int, tournament_multiselect.value))
        await self.user_service.update_user_tournament_registrations(
            user=self.user,
            actor=actor,
            selected_tournament_ids=selected_ids,
            current_registrations=user_tournaments,
        )

    async def _add_tournament_enrollments(self, tournament_multiselect, new_user, actor):
        selected_ids = set(map(int, tournament_multiselect.value))
        if selected_ids:
            await self.user_service.manage_tournament_enrollments(
                user=new_user,
                actor=actor,
                tournament_ids=selected_ids,
                is_update=False,
            )


class UserDialog(BaseUserDialog):
    """Self-edit dialog: display name and pronouns. No roles, no enrolments.

    It used to carry a Tournaments multiselect, reachable only by clicking your
    own name in a match table — a second, even less findable route to the same
    opt-in the Profile checkbox offered. Signing up lives on the Tournaments tab
    now, where it can show the window, the entrants and the rules.
    """

    async def open(self):
        with ui.dialog() as dialog, ui.card().classes('dialog-card dialog-card--sm'):
            self.dialog = dialog
            mobile_sheet(dialog)
            dialog_header('Edit Profile', dialog)
            with ui.column().classes('q-pa-md gap-2'):
                ui.input('Username', value=self.user.username if self.user else '').props(
                    'readonly' if self.user else ''
                ).classes('input-full-width')
                display_name_input = ui.input(
                    'Display Name', value=self.user.display_name if self.user else ''
                ).classes('input-full-width')
                pronouns_input = ui.input(
                    'Pronouns', value=self.user.pronouns if self.user else ''
                ).classes('input-full-width')

            async def submit():
                if not self.user:
                    with self.dialog:
                        ui.notify('Self-edit dialog requires an existing user.', color='warning')
                    return
                actor = await get_user_from_session(app.storage.user)
                if actor is None:
                    with self.dialog:
                        ui.notify('You must be logged in to edit your profile.', color='negative')
                    return
                try:
                    with self.dialog:
                        await self.user_service.update_user_profile(
                            self.user,
                            display_name=display_name_input.value,
                            pronouns=pronouns_input.value,
                            check_concurrency=True,
                            initial_updated_at=self._initial_updated_at,
                            actor=actor,
                        )
                        ui.notify('User updated.', color='positive')
                        dialog.close()
                        if self.on_submit:
                            await self.on_submit(self.user)
                except (ValueError, PermissionError) as e:
                    with self.dialog:
                        notify_error(e)

            with dialog_actions().classes('justify-end'):
                ui.button('Cancel', on_click=dialog.close).props('flat')
                ui.button('Save', on_click=submit).props('color=primary')

            submit_on_enter(dialog, submit)
            dialog.open()


class AdminUserDialog(BaseUserDialog):
    """Staff-edit dialog: profile + active flag + roles + TA/CC tournament assignments."""

    async def open(self):
        actor = await get_user_from_session(app.storage.user)
        if actor is None or not await AuthService.is_staff(actor):
            ui.notify('Only Staff can manage users.', color='negative')
            return

        is_create = self.user is None
        title = 'Add User' if is_create else 'Edit User'

        with ui.dialog() as dialog, ui.card().classes('dialog-card'):
            self.dialog = dialog
            mobile_sheet(dialog)
            dialog_header(title, dialog)
            with ui.column().classes('q-pa-md gap-2'):
                if is_create:
                    ui.label('* required').classes('required-legend')
                username_input = ui.input(
                    'Username',
                    value=self.user.username if self.user else '',
                ).props('readonly' if self.user else 'required').classes('input-full-width')
                display_name_input = ui.input(
                    'Display Name', value=self.user.display_name if self.user else ''
                ).classes('input-full-width')
                pronouns_input = ui.input(
                    'Pronouns', value=self.user.pronouns if self.user else ''
                ).classes('input-full-width')
                challonge_username_input = None
                challonge_id_input = None
                # Credentials alone aren't enough: the identity is global but the
                # feature is per-tenant, so a user linked in another community
                # would otherwise get these fields here and the save would raise.
                challonge_live = (
                    ChallongeService.is_configured()
                    and await FeatureFlagService().is_enabled(FeatureFlag.CHALLONGE)
                )
                may_edit_account = (
                    self.user is None
                    or await self.user_service.may_edit_account(self.user, actor)
                )
                if self.user and challonge_live and not may_edit_account:
                    ui.label(
                        f'Challonge: {self.user.challonge_username or self.user.challonge_user_id}'
                        if self.user.challonge_user_id else 'Challonge: not linked'
                    ).classes('text-caption text-grey-7')
                elif self.user and challonge_live:
                    if self.user.challonge_user_id:
                        challonge_username_input = ui.input(
                            'Challonge username',
                            value=self.user.challonge_username or '',
                        ).props(
                            f'clearable hint="Linked account id: {self.user.challonge_user_id}"'
                        ).classes('input-full-width')
                    else:
                        ui.label(
                            'No Challonge account linked. Enter the account id to link manually.'
                        ).classes('text-caption text-grey-7')
                        challonge_id_input = ui.input('Challonge account id').props('inputmode=numeric').classes('input-full-width')
                        challonge_username_input = ui.input(
                            'Challonge username (optional)'
                        ).classes('input-full-width')
                is_active_checkbox = ui.checkbox('Active', value=self.user.is_active if self.user else True)
                if self.user and not may_edit_account:
                    display_name_input.props('readonly')
                    pronouns_input.props('readonly')
                    is_active_checkbox.disable()
                    ui.label(
                        f'{self.user.preferred_name} also belongs to another community or '
                        'runs the platform, so only a platform admin can change their name, '
                        'pronouns, active status or Challonge link.'
                    ).classes('text-caption text-grey-7')
                if is_create:
                    discord_id_input = ui.input(
                        'Discord ID', value=''
                    ).props('inputmode=numeric').classes('input-full-width')
                else:
                    discord_id_input = None

                # Community-wide roles, then the on-site roles in one event (layout
                # A). Neither picker offers SUPER_ADMIN: it's a platform role and
                # the service refuses it.
                held = await self.user_service.role_grants(self.user) if self.user else []
                roles_editor = RoleGrantsEditor(
                    held, await EventService.events_for_tenant(require_tenant_id()), get_current_event_id(),
                )
                roles_editor.build()

                tournaments, user_tournaments, selected_tournament_ids, tournament_options = await self._get_tournament_data()
                tournament_multiselect = ui.select(
                    label='Tournaments (Player)',
                    options=tournament_options,
                    value=[str(tid) for tid in selected_tournament_ids],
                    multiple=True,
                ).props(CHIP_PROPS).classes('input-full-width')

                admin_tournament_ids = []
                if self.user:
                    admin_tournament_ids = [str(t.id) for t in await self.user.admin_tournaments.all()]
                admin_tournament_multiselect = ui.select(
                    label='Tournament Admin of',
                    options=tournament_options,
                    value=admin_tournament_ids,
                    multiple=True,
                ).props(CHIP_PROPS).classes('input-full-width')

                cc_tournament_ids = []
                if self.user:
                    cc_tournament_ids = [str(t.id) for t in await self.user.crew_coordinated_tournaments.all()]
                cc_tournament_multiselect = ui.select(
                    label='Crew Coordinator of',
                    options=tournament_options,
                    value=cc_tournament_ids,
                    multiple=True,
                ).props(CHIP_PROPS).classes('input-full-width')

            async def sync_role_assignments(target_user):
                granting, revoking = roles_editor.changes()
                for role, event_id in granting:
                    await self.user_service.grant_role(target_user, role, actor=actor, event_id=event_id)
                for role, event_id in revoking:
                    await self.user_service.revoke_role(target_user, role, actor=actor, event_id=event_id)  # style-guide: exempt — part of Save

            async def sync_tournament_memberships(target_user, multiselect, current_ids_str, add_method, remove_method):
                desired = set(map(int, multiselect.value or []))
                current = set(map(int, current_ids_str))
                tournaments_by_id = {t.id: t for t in tournaments}
                for tid in desired - current:
                    t = tournaments_by_id.get(tid)
                    if t:
                        await add_method(t, target_user, actor=actor)
                for tid in current - desired:
                    t = tournaments_by_id.get(tid)
                    if t:
                        await remove_method(t, target_user, actor=actor)

            async def submit():
                if is_create and not (username_input.value or '').strip():
                    with self.dialog:
                        ui.notify('Please fill required field(s): Username.', color='warning')
                    return
                try:
                    if self.user:
                        with self.dialog:
                            await self.user_service.update_user_profile(
                                self.user,
                                display_name=display_name_input.value,
                                pronouns=pronouns_input.value,
                                check_concurrency=True,
                                initial_updated_at=self._initial_updated_at,
                                actor=actor,
                            )
                            await self.user_service.update_user_admin_fields(
                                self.user,
                                is_active=is_active_checkbox.value,
                                actor=actor,
                            )
                            if self.user.challonge_user_id:
                                if challonge_username_input is not None:
                                    await ChallongeService().set_player_username(
                                        self.user,
                                        challonge_username_input.value,
                                        actor=actor,
                                    )
                            elif challonge_id_input is not None and (challonge_id_input.value or '').strip():
                                await ChallongeService().link_player_manually(
                                    self.user,
                                    challonge_id_input.value,
                                    challonge_username_input.value if challonge_username_input else None,
                                    actor=actor,
                                )
                            await self._update_tournament_enrollments(
                                user_tournaments, tournament_multiselect, actor,
                            )
                            await sync_role_assignments(self.user)
                            await sync_tournament_memberships(
                                self.user, admin_tournament_multiselect, admin_tournament_ids,
                                self.tournament_service.add_admin, self.tournament_service.remove_admin,
                            )
                            await sync_tournament_memberships(
                                self.user, cc_tournament_multiselect, cc_tournament_ids,
                                self.tournament_service.add_crew_coordinator,
                                self.tournament_service.remove_crew_coordinator,
                            )
                            ui.notify('User updated.', color='positive')
                            dialog.close()
                            if self.on_submit:
                                await self.on_submit(self.user)
                    else:
                        new_user = await self.user_service.create_user(
                            username=username_input.value,
                            display_name=display_name_input.value,
                            pronouns=pronouns_input.value,
                            is_active=is_active_checkbox.value,
                            discord_id=discord_id_input.value if discord_id_input else None,
                            actor=actor,
                        )
                        await self._add_tournament_enrollments(tournament_multiselect, new_user, actor)
                        await sync_role_assignments(new_user)
                        await sync_tournament_memberships(
                            new_user, admin_tournament_multiselect, [],
                            self.tournament_service.add_admin, self.tournament_service.remove_admin,
                        )
                        await sync_tournament_memberships(
                            new_user, cc_tournament_multiselect, [],
                            self.tournament_service.add_crew_coordinator,
                            self.tournament_service.remove_crew_coordinator,
                        )
                        with self.dialog:
                            ui.notify('User created.', color='positive')
                            dialog.close()
                            if self.on_submit:
                                await self.on_submit(new_user)
                except (ValueError, PermissionError) as e:
                    with self.dialog:
                        notify_error(e)

            async def open_message_dialog():
                if self.user:
                    dialog_instance = SendMessageDialog(self.user)
                    await dialog_instance.open()

            with dialog_actions():
                if self.user:
                    with dialog_more_menu():
                        menu_action('mail', 'Send message', open_message_dialog)
                ui.space()
                ui.button('Cancel', on_click=dialog.close).props('flat')
                if is_create:
                    create_btn = ui.button('Create', on_click=submit).props('color=primary')
                    create_btn.bind_enabled_from(
                        username_input, 'value',
                        backward=lambda v: bool(v and v.strip()),
                    )
                else:
                    ui.button('Save', on_click=submit).props('color=primary')

            submit_on_enter(dialog, submit)
            dialog.open()
