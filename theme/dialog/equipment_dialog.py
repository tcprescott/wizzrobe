"""Create / edit (and bulk-create) lending assets — Equipment Manager / Staff only."""

from typing import Awaitable, Callable, Optional

from nicegui import ui

from application.services import EquipmentService, TenantService, UserService
from models import Equipment, EquipmentStatus, User
from theme.busy import busy_click
from theme.dialog._helpers import dialog_actions, dialog_header, mobile_sheet

_WIZ_OWNER = ''  # empty owner value ⇒ owned by the community (its tenant)

_STATUS_OPTIONS = {
    EquipmentStatus.AVAILABLE.value: 'Available to lend',
    EquipmentStatus.RETIRED.value: 'Retired (can\'t be checked out)',
}


class EquipmentDialog:
    """Asset create/edit dialog. In create mode it also supports bulk creation
    of multiple identical assets (a contiguous block of asset numbers)."""

    def __init__(
        self,
        actor: User,
        equipment: Optional[Equipment] = None,
        on_saved: Optional[Callable[[], Awaitable[None]]] = None,
    ):
        self.actor = actor
        self.equipment = equipment
        self.on_saved = on_saved
        self.dialog = None
        self.service = EquipmentService()

    async def open(self) -> None:
        is_edit = self.equipment is not None

        # This community's people, not every User on the platform — an asset
        # recorded as owned by a stranger (or by the automation account) is not a
        # useful record. The stored owner is force-included so editing an asset
        # whose owner has since fallen out of the set cannot silently drop them.
        stored_owner = self.equipment.owner_user_id if is_edit else None
        users = await UserService().get_community_people(
            include_user_ids=[i for i in (self.actor.id, stored_owner) if i is not None],
        )
        community = await TenantService.current_community_name()
        owner_options = {_WIZ_OWNER: community}
        owner_options.update({str(u.id): u.preferred_name for u in users})
        current_owner = str(stored_owner) if stored_owner else _WIZ_OWNER
        if current_owner not in owner_options:
            # Deactivated or system-owned (the two exclusions the read enforces):
            # keep the value selectable so saving does not clear it by accident.
            owner_options[current_owner] = f'#{current_owner} (no longer available)'

        with ui.dialog() as dialog, ui.card().classes('dialog-card'):
            self.dialog = dialog
            mobile_sheet(dialog)
            dialog_header('Edit Asset' if is_edit else 'Add Asset', dialog)

            with ui.column().classes('q-pa-md gap-2 full-width'):
                name_input = ui.input(
                    label='Name',
                    value=self.equipment.name if is_edit else '',
                ).props('required autofocus').classes('full-width')
                description_input = ui.textarea(
                    label='Description',
                    value=(self.equipment.description or '') if is_edit else '',
                ).classes('full-width')
                notes_input = ui.textarea(
                    label='Private notes (managers only)',
                    value=(self.equipment.private_notes or '') if is_edit else '',
                ).classes('full-width')
                owner_select = ui.select(
                    label='Owner',
                    options=owner_options,
                    value=current_owner,
                ).props('use-input').classes('full-width')

                # Checked out is owned by check out / check in, so the select only
                # appears when the asset is on the shelf.
                status_select = None
                current = self.equipment
                if current is not None and current.status != EquipmentStatus.CHECKED_OUT:
                    status_select = ui.select(
                        label='Status',
                        options=_STATUS_OPTIONS,
                        value=current.status.value,
                    ).classes('full-width')
                elif is_edit:
                    ui.label(
                        "It's checked out right now. Check it in before you retire it."
                    ).classes('italic-note')

                count_input = None
                if not is_edit:
                    count_input = ui.number(
                        label='Quantity (creates this many identical assets)',
                        value=1, min=1, max=200, precision=0, format='%.0f',
                    ).props('inputmode=numeric').classes('full-width')

                ui.label('* name required').classes('required-legend')

            async def submit():
                name = (name_input.value or '').strip()
                if not name:
                    with self.dialog:
                        ui.notify('Asset name is required.', color='warning')
                    return
                owner_id = int(owner_select.value) if owner_select.value else None

                with self.dialog:
                    try:
                        if is_edit:
                            await self.service.update_asset(
                                actor=self.actor,
                                equipment_id=self.equipment.id,
                                name=name,
                                description=description_input.value,
                                private_notes=notes_input.value,
                                owner_user_id=owner_id,
                                # Only a changed status is sent, so an asset
                                # checked out while this dialog sat open saves
                                # its other edits instead of being refused.
                                status=(
                                    EquipmentStatus(status_select.value)
                                    if status_select is not None
                                    and status_select.value != self.equipment.status.value
                                    else None
                                ),
                            )
                            ui.notify('Asset updated.', color='positive')
                        else:
                            count = int(count_input.value or 1)
                            if count == 1:
                                await self.service.create_asset(
                                    actor=self.actor, name=name,
                                    description=description_input.value,
                                    private_notes=notes_input.value,
                                    owner_user_id=owner_id,
                                )
                                ui.notify('Asset created.', color='positive')
                            else:
                                created = await self.service.bulk_create_assets(
                                    actor=self.actor, name=name, count=count,
                                    description=description_input.value,
                                    private_notes=notes_input.value,
                                    owner_user_id=owner_id,
                                )
                                ui.notify(f'Created {len(created)} assets.', color='positive')
                    except (ValueError, PermissionError) as e:
                        ui.notify(str(e), color='warning')
                        return
                    dialog.close()
                    if self.on_saved:
                        await self.on_saved()

            with dialog_actions().classes('justify-end'):
                ui.button('Cancel', on_click=dialog.close).props('flat')
                ui.button('Save' if is_edit else 'Create',
                          on_click=busy_click('Saving…' if is_edit else 'Creating…', submit)).props('color=primary')

            dialog.open()
