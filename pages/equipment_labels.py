"""Bulk equipment QR-label sheet — a printable grid of asset QR codes.

Reached from the admin Equipment tab's "Print QR labels" flow (see
``theme/dialog/qr_label_dialog.py``) or the per-asset "Print label" button. The
selected asset ids arrive as a comma-separated ``ids`` query param and each
asset is rendered as a label (scannable QR + ``#number`` + name, plus any opted-
in extra lines) laid out for the chosen ``template``. The community logo sits in
black in the middle of each QR unless ``logo=0``:

* ``plain`` — a free grid on US Letter or A4 (``paper``), ``cols`` per row, with
  dashed cut guides. Print from the browser (Ctrl/Cmd-P).
* ``avery5160`` / ``avery5163`` / ``avery5162`` — exact-inch layouts positioned
  on the die-cuts of the matching Avery peel-off sheet. Print at 100% (Actual
  size), margins None, single-sided.

Manager-only (Staff / Equipment Manager), gated behind ``FeatureFlag.EQUIPMENT``.
Each QR encodes the asset's tenant-qualified detail URL, exactly as the single-
asset page does.
"""

from nicegui import context, ui

from application.services import EquipmentService, TenantLogoService, TenantService
from application.tenant_context import get_current_tenant_id
from application.utils.environment import get_base_url
from application.utils.hostname import effective_request_host, normalize_hostname
from application.utils.qrcode_util import asset_qr_svg_data_uri
from application.utils.tenant_urls import encoded_host_mismatch, tenant_url
from middleware.auth import protected_page
from models import FeatureFlag, Role

_MIN_COLS, _MAX_COLS, _DEFAULT_COLS = 1, 5, 3

# Avery peel-off layouts (US Letter). Published die-cut geometry in inches — the
# sheet is positioned so labels land on the adhesive cells when the browser
# prints at 100% (no fit-to-page scaling). `qr` is the QR edge length in inches.
_AVERY = {
    'avery5160': {
        'label': 'Avery 5160 · 30/sheet · 1"×2⅝"',
        'cols': 3, 'rows': 10, 'cell_w': 2.625, 'cell_h': 1.0,
        'margin_top': 0.5, 'margin_left': 0.1875, 'gutter_x': 0.125, 'gutter_y': 0.0,
        'qr': 0.84,
    },
    'avery5163': {
        'label': 'Avery 5163 · 10/sheet · 2"×4"',
        'cols': 2, 'rows': 5, 'cell_w': 4.0, 'cell_h': 2.0,
        'margin_top': 0.5, 'margin_left': 0.15625, 'gutter_x': 0.1875, 'gutter_y': 0.0,
        'qr': 1.7,
    },
    'avery5162': {
        'label': 'Avery 5162 · 14/sheet · 1⅓"×4"',
        'cols': 2, 'rows': 7, 'cell_w': 4.0, 'cell_h': 1.333,
        'margin_top': 0.83, 'margin_left': 0.15625, 'gutter_x': 0.1875, 'gutter_y': 0.0,
        'qr': 1.15,
    },
}
_PLAIN = 'plain'
_PAPERS = {'letter': 'letter', 'a4': 'A4'}
_SHOW_FIELDS = ('owner', 'community', 'desc')

# One source of truth for the template picker (dialog) and the page: plain first,
# then each Avery preset by its human label.
TEMPLATE_CHOICES = {_PLAIN: 'Plain grid (Letter/A4)', **{k: v['label'] for k, v in _AVERY.items()}}


def _parse_ids(raw: str) -> list[int]:
    """Comma-separated id string → list of ints, dropping blanks / non-numerics."""
    out: list[int] = []
    for part in (raw or '').split(','):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            continue
    return out


def _parse_show(raw: str) -> set[str]:
    """Comma-separated ``show`` string → the recognized extra-content fields."""
    return {p.strip() for p in (raw or '').split(',')} & set(_SHOW_FIELDS)


def resolve_template(name: str) -> str:
    """Normalize a ``template`` query value to a known key (falls back to plain)."""
    return name if name in _AVERY else _PLAIN


def _chunk(seq: list, size: int) -> list[list]:
    """Split ``seq`` into consecutive chunks of at most ``size`` (size ≥ 1)."""
    size = max(1, size)
    return [seq[i:i + size] for i in range(0, len(seq), size)]


def create() -> None:
    @protected_page(
        '/equipment/qr-labels',
        roles=[Role.STAFF, Role.EQUIPMENT_MANAGER],
        feature=FeatureFlag.EQUIPMENT,
        scope='community',
    )
    async def equipment_qr_labels(
        ids: str = '', cols: int = _DEFAULT_COLS,
        template: str = _PLAIN, paper: str = 'letter', show: str = '', logo: str = '1',
    ) -> None:
        community = await TenantService.current_community_name() or 'Wizzrobe'
        ui.page_title(f'{community} — QR labels')

        tpl = resolve_template(template)
        is_avery = tpl in _AVERY
        paper = paper if paper in _PAPERS else 'letter'
        shown = _parse_show(show)

        # Print rules can't be Quasar classes: @page sizes the sheet (Letter/A4,
        # or margin-0 for Avery so our inch offsets place the cells), and @media
        # print drops the screen-only toolbar. add_head_html(shared=False) keeps
        # this scoped to this page. Only fixed enums flow in — never user text.
        page_rule = ('@page { size: letter; margin: 0; }' if is_avery
                     else f'@page {{ size: {_PAPERS[paper]}; margin: 0.5in; }}')
        ui.add_head_html(f"""
<style>
{page_rule}
@media print {{ .no-print {{ display: none !important; }} html, body {{ background: #fff; }} }}
/* plain free grid */
.qr-sheet {{ display: grid; gap: 0.2in; width: 100%; box-sizing: border-box; }}
.qr-label {{ break-inside: avoid; page-break-inside: avoid; border: 1px dashed #b0b0b0;
    border-radius: 8px; padding: 10px 8px; display: flex; flex-direction: column;
    align-items: center; text-align: center; gap: 3px; }}
.qr-label .qr-code-img {{ width: 100%; max-width: 1.7in; height: auto; }}
/* avery fixed sheet */
.avery-page {{ width: 8.5in; height: 11in; box-sizing: border-box; overflow: hidden; }}
.avery-page + .avery-page {{ page-break-before: always; }}
.avery-grid {{ display: grid; }}
.avery-label {{ display: flex; align-items: center; gap: 0.09in; overflow: hidden; }}
.avery-label .qr-code-img {{ flex: 0 0 auto; }}
.avery-label .txt {{ overflow: hidden; }}
/* shared text */
.lbl-community {{ font-size: 0.62rem; color: #555; line-height: 1.1; }}
.lbl-num {{ font-weight: 700; font-size: 0.95rem; line-height: 1.15; }}
.lbl-name {{ font-size: 0.8rem; line-height: 1.15; word-break: break-word; }}
.lbl-owner {{ font-size: 0.72rem; font-style: italic; color: #444; line-height: 1.1; }}
.lbl-desc {{ font-size: 0.68rem; color: #444; line-height: 1.1;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }}
</style>
""")

        assets = await EquipmentService().get_assets_by_ids(_parse_ids(ids))

        # Tenant-qualified deep link per asset, matching the single-asset page:
        # the custom domain when set, else the path-mode /t/<slug>/… form.
        tid = get_current_tenant_id()
        tenant = await TenantService.get_by_id(tid) if tid is not None else None

        def asset_link(asset) -> str:
            if tenant is not None:
                return tenant_url(tenant, f'/equipment/{asset.id}')
            return f'{get_base_url()}/equipment/{asset.id}'

        # Pre-render each label once (QR generation is the expensive part).
        qr_logo = await TenantLogoService.qr_logo_data_uri() if logo != '0' else None
        labels = [
            {
                'num': f'#{asset.asset_number}',
                'name': asset.name or '',
                'owner': asset.owner_label(community) if 'owner' in shown else None,
                'desc': (asset.description or '') if 'desc' in shown else None,
                'uri': asset_qr_svg_data_uri(asset_link(asset), qr_logo),
            }
            for asset in assets
        ]

        try:
            columns = max(_MIN_COLS, min(int(cols), _MAX_COLS))
        except (TypeError, ValueError):
            columns = _DEFAULT_COLS
        state = {'cols': columns}

        # The host these labels encode, and whether it is the one the operator is
        # actually browsing. Compared against the *tenant's* canonical base, not
        # BASE_URL: a custom-domain community's labels are supposed to encode
        # that domain. `no-print`, so it never lands on a sticker.
        canonical_link = asset_link(assets[0]) if assets else None
        encoded_host = normalize_hostname(canonical_link)
        browsing_host = effective_request_host(context.client.request.headers)
        wrong_host = encoded_host_mismatch(canonical_link, browsing_host)

        if wrong_host:
            # Above the Print button in reading order: a warning below the action
            # is a warning after the fact. Never blocks printing — the operator
            # may be printing at home for a venue on another host.
            with ui.row().classes('no-print full-width q-px-sm q-pt-sm items-center gap-1'):
                ui.icon('warning').classes('text-warning')
                ui.label(
                    f'These labels encode {wrong_host} — you are browsing '
                    f'{browsing_host or "an unknown host"}. Check BASE_URL before printing.'
                ).classes('text-warning text-caption')

        with ui.row().classes('no-print items-center full-width q-pa-sm').style('gap: 0.75rem'):
            ui.label(f'{community} — equipment QR labels').classes('text-h6 q-ma-none')  # style-guide: exempt — a print sheet, not an app page
            if encoded_host:
                # The sheet was the only surface encoding links without showing
                # one; the asset page has printed its link under the QR all along.
                ui.label(f'links → {encoded_host}').classes('text-caption')
            ui.space()
            if labels:
                ui.label(f'{len(labels)} label{"s" if len(labels) != 1 else ""}').classes('text-caption')
                if is_avery:
                    ui.badge(_AVERY[tpl]['label']).props('color=grey-7')
                else:
                    # url-state: exempt — a print layout setting, not a view
                    ui.toggle(
                        {2: '2', 3: '3', 4: '4'},
                        value=state['cols'] if state['cols'] in (2, 3, 4) else 3,
                        on_change=lambda e: _set_cols(int(e.value)),
                    ).props('dense unelevated').classes('wiz-segmented').tooltip('Columns per row')
                ui.button(
                    'Print', icon='print', on_click=lambda: ui.run_javascript('window.print()'),
                ).props('color=primary')

        if not labels:
            ui.label('No equipment selected to print.').classes('text-error q-pa-md')
            return

        if is_avery:
            ui.label(
                f'Load {_AVERY[tpl]["label"].split(" ·")[0]} sheets. In the print dialog set '
                'Scale to 100% (Actual size), Margins to None, and print single-sided.'
            ).classes('no-print text-caption q-px-sm text-warning')

        def _label_text(lab: dict, *, community_line: bool) -> None:
            if community_line and 'community' in shown:
                ui.label(community).classes('lbl-community')
            ui.label(lab['num']).classes('lbl-num')
            if lab['name']:
                ui.label(lab['name']).classes('lbl-name')
            if lab['owner']:
                ui.label(lab['owner']).classes('lbl-owner')
            if lab['desc']:
                ui.label(lab['desc']).classes('lbl-desc')

        if is_avery:
            g = _AVERY[tpl]
            per_page = g['cols'] * g['rows']
            grid_style = (
                f"grid-template-columns: repeat({g['cols']}, {g['cell_w']}in); "
                f"column-gap: {g['gutter_x']}in; row-gap: {g['gutter_y']}in;"
            )
            page_pad = f"padding: {g['margin_top']}in 0 0 {g['margin_left']}in;"
            for page in _chunk(labels, per_page):
                with ui.element('div').classes('avery-page').style(page_pad):
                    with ui.element('div').classes('avery-grid').style(grid_style):
                        for lab in page:
                            with ui.element('div').classes('avery-label').style(
                                f"width: {g['cell_w']}in; height: {g['cell_h']}in;"
                            ):
                                ui.image(lab['uri']).props('loading=eager no-spinner no-transition') \
                                    .classes('qr-code-img').style(f"width: {g['qr']}in; height: {g['qr']}in;")
                                with ui.element('div').classes('txt'):
                                    _label_text(lab, community_line=True)
            return

        @ui.refreshable
        def sheet() -> None:
            with ui.element('div').classes('qr-sheet').style(
                f'grid-template-columns: repeat({state["cols"]}, 1fr)'
            ):
                for lab in labels:
                    with ui.element('div').classes('qr-label'):
                        # loading=eager so labels below the fold decode before print.
                        ui.image(lab['uri']).props('loading=eager no-spinner no-transition').classes('qr-code-img')
                        _label_text(lab, community_line=True)

        def _set_cols(value: int) -> None:
            state['cols'] = max(_MIN_COLS, min(value, _MAX_COLS))
            sheet.refresh()

        sheet()
