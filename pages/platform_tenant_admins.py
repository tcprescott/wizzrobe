"""The first-admin dialog for ``/platform``.

Split out of ``pages/platform.py`` (already the longest page module) rather than
added to it. Grants a community its first STAFF member, which until now was
reachable only through ``scripts/seed_tenant.py --operator-discord-id``.

The user select here is the **global** account list on purpose: a super-admin is
choosing from every account on the platform precisely because the target tenant
has no members yet. It is the one picker the membership scoping does not apply
to.
"""

from nicegui import ui

from application.services import TenantService, UserService
from theme.dialog._helpers import dialog_actions, form_dialog
from theme.notify import notify_error
from theme.steady import steady_refreshable


async def open_tenant_admins_dialog(actor, tenant_id: int, tenant_name: str) -> None:
    """List a tenant's staff and grant a new one, from the platform surface."""
    users = await UserService().get_all_users()

    with form_dialog(f'Admins for {tenant_name}', card_classes='dialog-card dialog-card--sm gap-2') as dialog:
        ui.label(
            'Staff run a community: they configure it, schedule its matches and '
            'grant its other roles. A community with no staff cannot be '
            'administered by anyone but a super-admin.'
        ).classes('text-caption text-grey')

        @steady_refreshable
        async def staff_list() -> None:
            try:
                staff = await TenantService.list_staff(actor, tenant_id)
            except PermissionError as e:
                ui.label(str(e)).classes('text-caption text-negative')
                return
            if not staff:
                ui.label(
                    'No admins yet. Grant STAFF to someone below to get this community started.'
                ).classes('text-caption text-warning')
                return
            for member in staff:
                with ui.row().classes('items-center no-wrap gap-2'):
                    ui.icon('shield').props('size=xs')
                    ui.label(member.display_name or member.username)

        await staff_list()

        ui.separator()
        select = ui.select(
            options={u.id: (u.display_name or u.username) for u in users},
            label='Grant STAFF to', with_input=True,
        ).classes('w-full')

        async def submit() -> None:
            if not select.value:
                ui.notify('Choose a user first', color='warning')
                return
            user = await UserService().get_user_by_id(select.value)
            if user is None:
                ui.notify('That user no longer exists', color='negative')
                return
            try:
                await TenantService.bootstrap_staff(actor, tenant_id, user)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            ui.notify(f'Granted STAFF in {tenant_name}', color='positive')
            select.value = None
            staff_list.refresh()

        with dialog_actions().classes('justify-end'):
            ui.button('Cancel', on_click=dialog.close).props('flat')
            ui.button('Grant', on_click=submit, color='primary')
    dialog.open()
