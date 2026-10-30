"""Pick equipment assets and open a printable sheet of their QR labels.

Opened from the admin Equipment tab's "Print QR labels" button. The manager
narrows the set (all / available-only / by owner / an asset-number range, or by
hand), picks a template (plain grid, or an Avery peel-off layout), optional
extra label content and whether the community logo goes in the QR codes, and
opens ``/equipment/qr-labels`` in a new tab. The target
is a bare in-app path — ``ui.navigate.to`` prepends the request's ``root_path``
(the ``/t/<slug>`` in path mode, empty on a custom domain) — so the new tab
stays on the community the manager is already viewing and carries the session.
"""

from typing import Optional

from nicegui import ui

from application.services import EquipmentService, TenantService, TenantThemeService
from models import EquipmentStatus, User
from pages.equipment_labels import TEMPLATE_CHOICES
from theme.dialog._helpers import dialog_actions, dialog_header, mobile_sheet

_ANY = 'any'
_WIZ = 'wiz'  # owner-is-None (community-owned) sentinel, distinct from "any owner"


def filtered_ids(
    rows: list[dict],
    *,
    available_only: bool = False,
    owner_key: Optional[str] = None,
    num_from: Optional[int] = None,
    num_to: Optional[int] = None,
) -> list[int]:
    """Ids of ``rows`` matching the quick-select filters (pure, unit-tested).

    ``rows`` are ``{id, num, available, owner_key}`` dicts. ``owner_key=None``
    means "any owner"; ``''`` matches community-owned. An unset bound is open.
    """
    lo = num_from if num_from is not None else float('-inf')
    hi = num_to if num_to is not None else float('inf')
    out: list[int] = []
    for r in rows:
        if available_only and not r['available']:
            continue
        if owner_key is not None and r['owner_key'] != owner_key:
            continue
        if not (lo <= r['num'] <= hi):
            continue
        out.append(r['id'])
    return out


class QrLabelDialog:
    """Select lending assets and open a printable QR-label sheet for them."""

    def __init__(self, actor: User):
        self.actor = actor
        self.service = EquipmentService()
        self.dialog = None
        self._checks: dict[int, ui.checkbox] = {}

    def _set_ids(self, ids) -> None:
        wanted = set(ids)
        for aid, cb in self._checks.items():
            cb.value = aid in wanted

    async def open(self) -> None:
        assets = await self.service.list_assets()
        community = await TenantService.current_community_name()
        has_logo = bool((await TenantThemeService.get_current_theme()).get('logo'))
        rows = [
            {
                'id': a.id,
                'num': a.asset_number,
                'available': a.status == EquipmentStatus.AVAILABLE,
                'owner_key': str(a.owner_user_id) if a.owner_user_id else '',
                'owner_label': a.owner_label(community),
            }
            for a in assets
        ]

        with ui.dialog() as dialog, ui.card().classes('dialog-card'):
            self.dialog = dialog
            mobile_sheet(dialog)
            dialog_header('Print QR labels', dialog)

            template_select = paper_toggle = cols_toggle = None
            owner_select = avail_only = num_from = num_to = None
            show_owner = show_community = show_desc = show_logo = None

            with ui.column().classes('q-pa-md gap-2 full-width'):
                if not assets:
                    ui.label('No equipment to print.').classes('italic-note')
                else:
                    with ui.row().classes('items-center gap-2 full-width'):
                        template_select = ui.select(
                            TEMPLATE_CHOICES, value='plain', label='Template',
                        ).props('dense').classes('col')
                        # url-state: exempt — a print setting inside a dialog
                        paper_toggle = ui.toggle({'letter': 'Letter', 'a4': 'A4'}, value='letter') \
                            .props('dense unelevated no-caps').classes('wiz-segmented')
                        paper_toggle.bind_visibility_from(template_select, 'value', lambda v: v == 'plain')
                        # url-state: exempt — a print setting inside a dialog
                        cols_toggle = ui.toggle({2: '2', 3: '3', 4: '4'}, value=3) \
                            .props('dense unelevated').classes('wiz-segmented') \
                            .tooltip('Columns per row')
                        cols_toggle.bind_visibility_from(template_select, 'value', lambda v: v == 'plain')
                    with ui.row().classes('items-center gap-3 full-width'):
                        ui.label('Also show:').classes('text-caption')
                        show_owner = ui.checkbox('Owner', value=False)
                        show_community = ui.checkbox('Community', value=False)
                        show_desc = ui.checkbox('Description', value=False)
                        if has_logo:
                            show_logo = ui.checkbox('Logo in QR code', value=True)

                    ui.separator()

                    owner_opts = {_ANY: 'Any owner'}
                    seen = set()
                    for r in rows:
                        if r['owner_key'] in seen:
                            continue
                        seen.add(r['owner_key'])
                        owner_opts[_WIZ if r['owner_key'] == '' else r['owner_key']] = r['owner_label']
                    with ui.row().classes('items-center gap-2 full-width'):
                        ui.label('Quick select:').classes('text-caption')
                        avail_only = ui.checkbox('Available only', value=False)
                        owner_select = ui.select(owner_opts, value=_ANY, label='Owner') \
                            .props('dense').classes('col')
                    with ui.row().classes('items-center gap-2 full-width'):
                        ui.label('# range').classes('text-caption')
                        num_from = ui.number(label='from', min=1, precision=0, format='%.0f') \
                            .props('dense').classes('col')
                        num_to = ui.number(label='to', min=1, precision=0, format='%.0f') \
                            .props('dense').classes('col')

                        def apply_filter() -> None:
                            ok = owner_select.value
                            owner_key = None if ok == _ANY else ('' if ok == _WIZ else ok)
                            self._set_ids(filtered_ids(
                                rows,
                                available_only=bool(avail_only.value),
                                owner_key=owner_key,
                                num_from=int(num_from.value) if num_from.value else None,
                                num_to=int(num_to.value) if num_to.value else None,
                            ))

                        ui.button('Apply', icon='filter_alt', on_click=apply_filter).props('flat dense')

                    with ui.row().classes('items-center gap-2 full-width'):
                        ui.button('Select all', on_click=lambda: self._set_ids(r['id'] for r in rows)).props('flat dense')
                        ui.button('Select none', on_click=lambda: self._set_ids(())).props('flat dense')

                    with ui.column().classes('gap-1 full-width').style('max-height: 40vh; overflow-y: auto'):
                        for a in assets:
                            self._checks[a.id] = ui.checkbox(f'#{a.asset_number} · {a.name}', value=True)

            async def generate() -> None:
                selected = [aid for aid, cb in self._checks.items() if cb.value]
                if not selected:
                    with self.dialog:
                        ui.notify('Select at least one asset.', color='warning')
                    return
                ids_csv = ','.join(str(i) for i in selected)
                tpl = template_select.value if template_select is not None else 'plain'
                params = [f'ids={ids_csv}', f'template={tpl}']
                if tpl == 'plain':
                    params.append(f'cols={int(cols_toggle.value)}')
                    params.append(f'paper={paper_toggle.value}')
                show = [f for f, cb in (('owner', show_owner), ('community', show_community),
                                        ('desc', show_desc)) if cb and cb.value]
                if show:
                    params.append('show=' + ','.join(show))
                if show_logo is not None and not show_logo.value:
                    params.append('logo=0')

                dialog.close()
                # Bare path: ui.navigate.to prepends the tenant's root_path.
                ui.navigate.to('/equipment/qr-labels?' + '&'.join(params), new_tab=True)

            with dialog_actions().classes('justify-end'):
                ui.button('Cancel', on_click=dialog.close).props('flat')
                if assets:
                    ui.button(
                        'Open printable sheet', icon='print', on_click=generate,
                    ).props('color=primary')

            dialog.open()
