"""Equipment asset detail page — the QR-code target for each asset.

A scannable QR code on this page encodes the asset's own URL, so scanning it
jumps straight here (login required). Logged-in users see the asset summary,
its QR code, and loan history; those with the right roles can check the asset
out or back in, and managers can edit/delete it.
"""

from nicegui import app, background_tasks, context, ui

from application.services import (
    AuthService,
    EquipmentService,
    TenantLogoService,
    TenantService,
    get_user_from_session,
)
from application.tenant_context import get_current_tenant_id
from application.utils.environment import get_base_url
from application.utils.hostname import effective_request_host
from application.utils.qrcode_util import asset_qr_png_bytes, asset_qr_svg, asset_qr_svg_data_uri
from application.utils.tenant_urls import encoded_host_mismatch, tenant_url
from application.utils.timezone import format_local_display
from middleware.auth import protected_page
from models import FeatureFlag
from theme.base import BaseLayout
from theme.connection import REQUIRES_SOCKET_CLASS
from theme.dialog import EquipmentDialog, open_checkout, quick_checkin
from theme.equipment_copy import equipment_guidance
from theme.help import help_icon
from theme.page_header import page_title
from theme.realtime import refresh_on_reconnect
from theme.steady import steady_refreshable

_STATUS_LABELS = {
    'available': 'Available',
    'checked_out': 'Checked out',
    'retired': 'Retired',
}

#: Loan rows painted before the "Show all" expansion. An asset lent two hundred
#: times used to fetch and render all two hundred, on a phone.
_HISTORY_PREVIEW = 5


def _render_loans(loans) -> None:
    """One caption line per loan; the still-open one gets a badge, not prose.

    Presentation only — the *limit* is the repository's job, this is the shape.
    """
    for loan in loans:
        out = format_local_display(loan.checked_out_at)
        with ui.row().classes('items-baseline gap-2'):
            if loan.checked_in_at is None:
                ui.badge('Out now', color='warning')
            back = (
                format_local_display(loan.checked_in_at)
                if loan.checked_in_at else 'not yet returned'
            )
            ui.label(
                f'{loan.borrower.preferred_name}: {out} → {back} '
                f'(out by {loan.checked_out_by.preferred_name})'
            ).classes('text-caption')


def create() -> None:
    @protected_page('/equipment/{asset_id}', feature=FeatureFlag.EQUIPMENT)
    async def equipment_detail(asset_id: int) -> None:
        ui.page_title(f'Equipment — {await TenantService.current_community_name() or "Wizzrobe"}')
        # get_user_from_session enforces is_active so a deactivated user
        # cannot keep reading asset owner labels / manager-only private notes.
        user = await get_user_from_session(app.storage.user)
        await BaseLayout(user=user).render()

        if user is None:
            ui.label('You must be logged in to view this page.').classes('text-error')
            return

        service = EquipmentService()
        can_manage = await AuthService.can_manage_equipment(user)
        can_checkout = await AuthService.can_checkout_equipment(user)
        can_checkin = await AuthService.can_checkin_equipment(user)

        @steady_refreshable
        async def render_detail() -> None:
            asset = await service.get_asset(asset_id)
            if asset is None:
                ui.label("Couldn't find that asset. It may have been removed.").classes('text-error')
                return

            open_loan = await service.current_loan(asset)
            history = await service.loan_history(asset, limit=_HISTORY_PREVIEW)
            total_loans = await service.loan_count(asset)

            # Tenant-qualified deep link so a scanned QR resolves to this
            # community: its own custom domain when set, else the path-mode form
            # (/t/<slug>/equipment/<id>). On the bare platform host a bare
            # /equipment/<id> would 404 (protected pages require a tenant).
            tid = get_current_tenant_id()
            tenant = await TenantService.get_by_id(tid) if tid is not None else None
            asset_link = (
                tenant_url(tenant, f'/equipment/{asset.id}')
                if tenant is not None else f'{get_base_url()}/equipment/{asset.id}'
            )

            with ui.card().classes('page-container-narrow w-full q-pa-lg q-mt-md column'):
                with ui.row().classes('header-row items-center wiz-help-row'):
                    page_title(f'#{asset.asset_number} · {asset.name}')
                    ui.badge(
                        _STATUS_LABELS.get(asset.status.value, asset.status.value),
                        color=('positive' if asset.status.value == 'available'
                               else 'warning' if asset.status.value == 'checked_out' else 'grey'),
                    )
                    if can_manage:
                        await help_icon('equipment-desk', user=user)
                    else:
                        await help_icon('equipment-borrow', 'equipment-return', user=user)
                ui.separator().classes('separator-spacing')

                with ui.row().classes('w-full gap-6 items-start'):
                    with ui.column().classes('gap-1'):
                        if asset.description:
                            ui.label(asset.description).classes('wiz-free-text')
                        community = await TenantService.current_community_name()
                        ui.label(f'Owner: {asset.owner_label(community)}').classes('italic-note')
                        if open_loan is not None:
                            ui.label(
                                f'Checked out to {open_loan.borrower.preferred_name} '
                                f'({format_local_display(open_loan.checked_out_at)})'
                            ).classes('italic-note')
                        if can_manage and asset.private_notes:
                            with ui.card().classes('q-pa-sm bg-amber-1 text-grey-10'):
                                ui.label('Private notes').classes('text-caption text-bold')
                                ui.label(asset.private_notes).classes('wiz-free-text')

                    with ui.column().classes('items-center gap-1'):
                        qr_logo = await TenantLogoService.qr_logo_data_uri()
                        ui.image(asset_qr_svg_data_uri(asset_link, qr_logo)).classes('w-40 h-40')
                        ui.label(asset_link).classes('text-caption wiz-free-text')
                        # Manager-only: a volunteer scanning a label cannot act
                        # on a BASE_URL misconfiguration and does not need the
                        # noise. Compared against the tenant's canonical base —
                        # a custom-domain community's links are meant to differ
                        # from the platform host.
                        wrong_host = encoded_host_mismatch(
                            asset_link,
                            effective_request_host(context.client.request.headers),
                        ) if can_manage else None
                        if wrong_host:
                            ui.label(
                                f'Encodes {wrong_host}, not the host you are on — check BASE_URL.'
                            ).classes('text-warning text-caption')

                        def download_qr():
                            # The logo is an SVG or WebP the server can't
                            # rasterise into a PNG, so a logo code downloads as SVG.
                            if qr_logo:
                                ui.download(asset_qr_svg(asset_link, qr_logo).encode('utf-8'),
                                            f'asset-{asset.asset_number}-qr.svg', 'image/svg+xml')
                            else:
                                ui.download(asset_qr_png_bytes(asset_link), f'asset-{asset.asset_number}-qr.png')

                        with ui.row().classes('gap-1'):
                            ui.button('Download QR', icon='download', on_click=download_qr).props('flat dense')
                            if can_manage:
                                ui.button(
                                    'Print label', icon='print',
                                    on_click=lambda aid=asset.id: ui.navigate.to(
                                        f'/equipment/qr-labels?ids={aid}', new_tab=True),
                                ).props('flat dense').tooltip('Open a printable label for this asset')

                # --- Actions ---
                # Every button here needs a round trip, so each carries the
                # offline guard's class (theme/connection.py). Download QR and
                # Print label above deliberately do not: the data URI is already
                # in the page, and a new tab fails on its own terms.
                with ui.row().classes('q-mt-md gap-2'):
                    if open_loan is None and can_checkout and asset.status.value != 'retired':
                        label = 'Check out…' if can_manage else 'Check out to me'
                        ui.button(label, icon='logout', on_click=do_checkout).props(
                            'color=primary').classes(REQUIRES_SOCKET_CLASS)
                    if open_loan is not None and can_checkin:
                        ui.button('Check in', icon='login', on_click=do_checkin).props(
                            'color=secondary').classes(REQUIRES_SOCKET_CLASS)
                    if can_manage:
                        ui.button('Edit', icon='edit', on_click=edit_asset).props(
                            'flat').classes(REQUIRES_SOCKET_CLASS)

                guidance = equipment_guidance(
                    status=asset.status.value,
                    is_checked_out=open_loan is not None,
                    viewer_is_holder=(
                        open_loan is not None and open_loan.borrower_id == user.id
                    ),
                    can_checkout=can_checkout,
                    can_checkin=can_checkin,
                    can_manage=can_manage,
                )
                if guidance:
                    ui.label(guidance).classes('italic-note q-mt-sm')

                # --- History ---
                if history:
                    ui.separator().classes('separator-spacing')
                    with ui.row().classes('items-baseline gap-2 q-mt-md'):
                        ui.label('Loan history').classes('section-title')
                        if total_loans > len(history):
                            ui.label(f'{len(history)} of {total_loans}').classes('text-caption')
                    with ui.column().classes('gap-1 w-full'):
                        _render_loans(history)
                    if total_loans > len(history):
                        # Fetched on first open, not at build time: the whole
                        # point of the bound is not paying for rows nobody asked
                        # to see.
                        with ui.expansion(f'Show all {total_loans}').classes('w-full') as expansion:
                            rest = ui.column().classes('gap-1 w-full')
                        loaded = {'value': False}

                        async def load_rest(client) -> None:
                            with client:
                                loaded['value'] = True
                                every = await service.loan_history(asset)
                                with rest:
                                    # Only what is not already painted above, so
                                    # "Show all 15" leaves fifteen on screen, not
                                    # the first five twice.
                                    _render_loans(every[len(history):])

                        def on_expand(event) -> None:
                            if event.value and not loaded['value']:
                                background_tasks.create(load_rest(context.client))

                        expansion.on_value_change(on_expand)

        async def do_checkout():
            actor = await get_user_from_session(app.storage.user)
            await open_checkout(actor, asset_id, can_manage=can_manage, on_done=render_detail.refresh)

        async def do_checkin():
            actor = await get_user_from_session(app.storage.user)
            await quick_checkin(actor, asset_id, on_done=render_detail.refresh)

        async def edit_asset():
            actor = await get_user_from_session(app.storage.user)
            asset = await service.get_asset(asset_id)
            if asset is None:
                ui.notify("Couldn't find that asset. It may have been removed.", color='warning')
                return
            await EquipmentDialog(actor, equipment=asset, on_saved=render_detail.refresh).open()

        refresh_on_reconnect(render_detail.refresh)
        await render_detail()
