"""Shared equipment checkout flow — borrower picker for managers, self-checkout
for everyone else. Used by the asset detail page and the inventory tables."""

from typing import Awaitable, Callable, Iterable, Optional

from nicegui import background_tasks, ui

from application.services import EquipmentService, UserService
from application.services.user_service import PersonSummary
from models import SYSTEM_USER_DISCORD_ID, User
from theme.dialog._helpers import dialog_actions, dialog_header, mobile_sheet
from theme.notify import notify_error


def _borrower_options(users: Iterable[User | PersonSummary]) -> dict[str, str]:
    """Selectable borrowers, keyed by id.

    The two hard exclusions are applied here as well as in the scoped read, so
    they hold for the "include people outside this community" list too — that
    toggle widens *which community's* people are offered, never who may borrow.
    """
    return {
        str(u.id): u.preferred_name
        for u in users
        if u.is_active and str(u.discord_id) != str(SYSTEM_USER_DISCORD_ID)
    }


class CheckoutDialog:
    """Borrower-picker checkout dialog (managers/staff check out on behalf of any user)."""

    def __init__(
        self,
        actor: User,
        equipment_id: int,
        on_done: Optional[Callable[[], Awaitable[None]]] = None,
    ):
        self.actor = actor
        self.equipment_id = equipment_id
        self.on_done = on_done
        self.service = EquipmentService()

    async def open(self) -> None:
        user_service = UserService()
        # The actor is force-included: a manager checking something out to
        # themselves must always be in their own picker.
        community = await user_service.get_community_people_summaries(include_user_ids=[self.actor.id])
        options = _borrower_options(community)
        with ui.dialog() as dialog, ui.card().classes('dialog-card dialog-card--sm'):
            mobile_sheet(dialog)
            dialog_header('Check out equipment', dialog)
            with ui.column().classes('q-pa-md gap-2 full-width'):
                # Labelled as a widening, not a filter: the default list is this
                # community's people, and the venue case — lending to someone who
                # just walked in and is not enrolled in anything — is real.
                widen = ui.checkbox('Include people outside this community')
                borrower_select = ui.select(
                    label='Borrower', options=options, value=str(self.actor.id),
                    with_input=True,
                ).classes('full-width')

                async def apply_scope() -> None:
                    people = (
                        await user_service.get_all_users() if widen.value
                        else await user_service.get_community_people_summaries(
                            include_user_ids=[self.actor.id],
                        )
                    )
                    # The two hard exclusions survive the escape hatch: nobody
                    # lends a cable to the automation actor, and a deactivated
                    # account cannot even log in.
                    borrower_select.options = _borrower_options(people)
                    if borrower_select.value not in borrower_select.options:
                        borrower_select.value = str(self.actor.id)
                    borrower_select.update()

                widen.on_value_change(lambda: background_tasks.create(apply_scope()))

            async def confirm():
                try:
                    await self.service.checkout(
                        self.actor, self.equipment_id, borrower_id=int(borrower_select.value)
                    )
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                dialog.close()
                ui.notify('Checked out.', color='positive')
                if self.on_done:
                    await self.on_done()

            with dialog_actions().classes('justify-end'):
                ui.button('Cancel', on_click=dialog.close).props('flat')
                ui.button('Check out', on_click=confirm).props('color=primary')
            dialog.open()


async def open_checkout(
    actor: User,
    equipment_id: int,
    *,
    can_manage: bool,
    on_done: Optional[Callable[[], Awaitable[None]]] = None,
) -> None:
    """Check an asset out. Managers/staff get a borrower picker; everyone else
    is checked out to themselves immediately."""
    if can_manage:
        await CheckoutDialog(actor, equipment_id, on_done=on_done).open()
        return
    try:
        await EquipmentService().checkout(actor, equipment_id)
    except (ValueError, PermissionError) as e:
        notify_error(e)
        return
    ui.notify('Checked out to you.', color='positive')
    if on_done:
        await on_done()


async def quick_checkin(
    actor: User,
    equipment_id: int,
    on_done: Optional[Callable[[], Awaitable[None]]] = None,
) -> None:
    """Check an asset back in (manager/staff only — enforced by the service)."""
    try:
        await EquipmentService().checkin(actor, equipment_id)
    except (ValueError, PermissionError) as e:
        notify_error(e)
        return
    ui.notify('Checked in.', color='positive')
    if on_done:
        await on_done()
