"""Admin Payouts Page — prize pool and placement splits per tournament."""

import re
from decimal import Decimal, InvalidOperation

from nicegui import app, background_tasks, context, ui

from application.services import PayoutService, TournamentService, get_user_from_session
from application.services.payout_service import format_percentage
from application.utils.timezone import format_local_time
from theme.busy import busy_click
from theme.clipboard import copy_to_clipboard
from theme.dialog._helpers import dialog_actions, form_dialog
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.help import help_icon
from theme.menu import SlotItem, slot_more_menu, slot_row_button
from theme.notify import notify_error
from theme.page_header import page_header
from theme.tables.admin_crud import current_actor, refresh_button, wire_tab_refresh
from theme.tables.mobile_grid import enable_mobile_grid
from theme.tables.preferences import TableKeys

_POOL_CELL = (
    '{{ props.row.pool }}<q-icon v-if="props.row.sglive_slug" name="sync" size="xs" '
    'class="q-ml-xs text-positive" aria-label="Synced from sglive">'
    '<q-tooltip>Synced from sglive</q-tooltip></q-icon>'
)

_STATUS_CHIP = '''
    <span class="wiz-chip" :class="props.row.status_chip">{{ props.row.status }}</span>
'''

_ROW_ACTIONS = slot_row_button('Prize pool', 'payments', 'pool') + slot_more_menu('props.row.name', [
    SlotItem('leaderboard', 'Edit split', 'split'),
    SlotItem('content_copy', 'Copy for Matcherino', 'copy'),
])


def _money(value: Decimal) -> str:
    return f'${value:,.2f}'


def _parse_money(raw: str | None, label: str) -> Decimal | None:
    text = (raw or '').strip().lstrip('$').replace(',', '')
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation as err:
        raise ValueError(f'{label} must be a number, or blank.') from err


def _parse_percentage(raw: str | None) -> Decimal:
    text = (raw or '').strip().rstrip('%')
    if not text:
        raise ValueError('Every place needs a share.')
    try:
        return Decimal(text)
    except InvalidOperation as err:
        raise ValueError(f'{text!r} is not a percentage.') from err


def _parse_share(raw: str | None, unit: str) -> tuple[Decimal | None, Decimal | None]:
    """A Share box as ``(percentage, amount)``, exactly one of them set."""
    if unit == '$':
        amount = _parse_money(raw, 'Each amount')
        if amount is None:
            raise ValueError('Every place needs a share.')
        return None, amount
    return _parse_percentage(raw), None


def _share_hint(raw: str | None, unit: str, total: Decimal) -> str:
    """The other unit, shown greyed inside the Share box as it's typed."""
    try:
        pct, amount = _parse_share(raw, unit)
    except ValueError:
        return ''
    if pct is not None:
        return f'= {_money(PayoutService.amount_for(total, pct))}'
    if amount is None or not total:
        return ''
    return f'{format_percentage((amount * 100 / total).quantize(Decimal("0.01")))}%'


def _allocated_label(overview) -> str:
    if overview.has_fixed:
        return f'{_money(overview.allocated_amount)} of {_money(overview.total)}'
    return f'{format_percentage(overview.allocated_percentage)}%'


def _name_key(name: str) -> str:
    words = re.sub(r'[^a-z0-9 ]', ' ', name.lower()).split()
    return ' '.join(w for w in words if w not in {'the', 'tournament', 'online'})


def _suggest_links(games, tournaments: dict[int, str]) -> dict[str, int]:
    """Pair sglive games with tournaments whose names match once normalised."""
    keys: dict[str, list[int]] = {}
    for tid, name in tournaments.items():
        keys.setdefault(_name_key(name), []).append(tid)
    suggestions: dict[str, int] = {}
    for game in games:
        match = keys.get(_name_key(game.name), [])
        if len(match) == 1 and match[0] not in suggestions.values():
            suggestions[game.slug] = match[0]
    return suggestions


def _notify_synced(missing: list[str]) -> None:
    if missing:
        ui.notify(
            f"sglive no longer lists {', '.join(missing)}; those pools kept their last amount.",
            color='warning',
        )
    else:
        ui.notify('Prize pools updated from sglive', color='positive')


def _status(overview) -> dict:
    """What is still missing on this tournament's split, in one chip."""
    if not overview.place_count:
        return {'status': 'No split', 'status_chip': ''}
    if not overview.is_complete:
        return {'status': 'Winners not named', 'status_chip': 'wiz-chip--pending'}
    return {'status': 'Ready', 'status_chip': 'wiz-chip--ok'}


async def admin_payouts_page() -> None:
    service = PayoutService()
    tournament_service = TournamentService()

    with ui.column().classes('page-container-narrow'):
        with page_header(
            'Payouts',
            'Record what each placement wins, as a share of the pool or a fixed '
            'dollar amount. Shares are worked out from the pool and the bonus every '
            'time they are read, so moving the pool moves them; dollar amounts stay put.',
        ):
            await help_icon('admin-payouts', user=await current_actor())

        columns: list[dict] = [
            {'name': 'tournament_id', 'label': 'ID', 'field': 'tournament_id', 'hidden': True},
            {'name': 'name', 'label': 'Tournament', 'field': 'name', 'sortable': True},
            # The money columns sort on their numeric ``*_raw`` field and render
            # through a cell slot, so Pool orders 9 before 10 rather than
            # alphabetically on "$10.00".
            {'name': 'pool', 'label': 'Pool', 'field': 'pool_raw', 'sortable': True},
            {'name': 'bonus', 'label': 'Bonus', 'field': 'bonus_raw', 'sortable': True},
            {'name': 'total', 'label': 'Total', 'field': 'total_raw', 'sortable': True},
            {'name': 'places', 'label': 'Places', 'field': 'place_count', 'sortable': True},
            {'name': 'allocated', 'label': 'Allocated', 'field': 'allocated_raw',
             'sortable': True},
            {'name': 'status', 'label': 'Status', 'field': 'status'},
            {'name': 'actions', 'label': '', 'field': 'actions'},
        ]
        table_container = ui.column().classes('w-full')

        async def _current():
            return await get_user_from_session(app.storage.user)

        async def refresh_table():
            actor = await _current()
            overviews = await service.list_overview(actor)
            table.rows = [
                {
                    'tournament_id': o.tournament_id,
                    'name': o.name,
                    'pool_raw': float(o.pool),
                    'bonus_raw': float(o.bonus),
                    'total_raw': float(o.total),
                    'pool': _money(o.pool),
                    'bonus': _money(o.bonus),
                    'total': _money(o.total),
                    'place_count': o.place_count,
                    'allocated_raw': float(
                        o.allocated_amount * 100 / o.total if o.has_fixed and o.total
                        else o.allocated_percentage
                    ),
                    'allocated': _allocated_label(o),
                    'sglive_slug': o.sglive_slug,
                    'synced_label': format_local_time(o.synced_at) if o.synced_at else '',
                    **_status(o),
                }
                for o in overviews
            ]
            table.update()

        async def open_pool_dialog(row, client):
            with client:
                try:
                    split = await service.get_split(row['tournament_id'], await _current())
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                synced = bool(row.get('sglive_slug'))
                with table_container:
                    with form_dialog(f"Prize pool — {row['name']}", card_classes='dialog-card dialog-card--sm') as dialog:
                        with ui.column().classes('gap-2 full-width'):
                            if synced:
                                async def do_unlink():
                                    try:
                                        await service.unlink_sglive(row['tournament_id'], await _current())
                                    except (ValueError, PermissionError) as e:
                                        notify_error(e)
                                        return
                                    dialog.close()
                                    ui.notify('Unlinked from sglive. The pool is yours to type.', color='positive')
                                    await refresh_table()

                                def unlink():
                                    ConfirmationDialog(
                                        message='Stop updating this pool from sglive? It keeps '
                                                'its current amount, and you can type it by hand.',
                                        on_confirm=do_unlink, confirm_text='Unlink',
                                        title='Unlink from sglive?', tone='primary',
                                    ).open()

                                with ui.row().classes('wiz-sglive-synced items-center no-wrap full-width gap-2'):
                                    ui.icon('sync').classes('text-positive')
                                    when = row.get('synced_label')
                                    ui.label(
                                        f'Synced from sglive at {when}' if when else 'Syncs from sglive'
                                    ).classes('col')
                                    ui.button('Unlink', on_click=unlink).props('flat dense no-caps color=primary')
                            with ui.row().classes('form-pair gap-2'):
                                pool_input = ui.input(
                                    'Prize pool', value=f'{split.pool:.2f}',
                                ).props('outlined dense prefix="$"' + (' readonly' if synced else ''))
                                if synced:
                                    with pool_input.add_slot('append'):
                                        ui.icon('lock', size='xs').tooltip('Synced from sglive')
                                bonus_input = ui.input(
                                    'Bonus', value=f'{split.bonus:.2f}',
                                ).props('outlined dense prefix="$"')
                            ui.label(
                                'Shares are taken from the pool and the bonus added '
                                'together. Leave a field blank if nobody has decided it yet.'
                            ).classes('text-caption text-grey')

                        async def submit():
                            try:
                                await service.set_pool(
                                    row['tournament_id'],
                                    _parse_money(pool_input.value, 'Prize pool'),
                                    _parse_money(bonus_input.value, 'Bonus'),
                                    await _current(),
                                )
                            except (ValueError, PermissionError) as e:
                                notify_error(e)
                                return
                            dialog.close()
                            ui.notify('Prize pool saved', color='positive')
                            await refresh_table()

                        with dialog_actions().classes('justify-end'):
                            ui.button('Cancel', on_click=dialog.close).props('flat')
                            ui.button('Save', icon='save', on_click=submit).props('color=primary')
                dialog.open()

        async def open_split_dialog(row, client):
            with client:
                actor = await _current()
                try:
                    split = await service.get_split(row['tournament_id'], actor)
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                entrants = await tournament_service.get_enrolled_players_by_tournament_id(
                    row['tournament_id']
                )
                options: dict[int | None, str] = {None: '(not decided yet)'}
                for tp in entrants:
                    options[tp.user_id] = tp.user.preferred_name
                for line in split.lines:
                    if line.entrant is not None:
                        options.setdefault(line.entrant.id, line.entrant.preferred_name)

                # Widgets keyed by an incrementing id rather than by index, so
                # removing a middle row cannot renumber the rows below it.
                widgets: dict[int, dict] = {}
                next_key = {'n': 0}

                with table_container:
                    with form_dialog(f"Split — {row['name']}") as dialog:
                        with ui.column().classes('q-pa-md gap-2 full-width'):
                            ui.label(
                                f'Sharing {_money(split.total)}. Each place takes a dollar '
                                'amount or a percent. Two rows may hold the same place; '
                                'joint placings are paid separately, not halved.'
                            ).classes('text-caption text-grey')
                            rows_column = ui.element('div').classes('wiz-edit-list')

                            def refresh_allocated():
                                spent = Decimal(0)
                                for entry in widgets.values():
                                    try:
                                        pct, amount = _parse_share(
                                            entry['share'].value, entry['unit'].value,
                                        )
                                    except ValueError:
                                        continue
                                    spent += amount if amount is not None else \
                                        PayoutService.amount_for(split.total, pct)
                                allocated_value.text = f'{_money(spent)} of {_money(split.total)}'
                                over = split.total > 0 and spent > split.total
                                allocated_value.classes(
                                    replace='text-weight-medium ' + ('text-negative' if over else 'text-positive'),
                                )

                            def add_row(place=1, share='', entrant_id=None, unit='%'):
                                key = next_key['n']
                                next_key['n'] += 1
                                with rows_column:
                                    with ui.element('div').classes('wiz-edit-row') as holder:
                                        place_input = ui.number(
                                            'Place', value=place, min=1, format='%d',
                                        ).props('outlined dense').classes('wiz-edit-row__place')
                                        share_input = ui.input('Share', value=str(share)) \
                                            .props('outlined dense').classes('wiz-edit-row__grow')
                                        with share_input.add_slot('prepend'):
                                            prefix = ui.label('$').classes('text-body2')
                                        with share_input.add_slot('append'):
                                            hint = ui.label().classes('text-caption text-grey')
                                        # url-state: exempt — a form input on one split row, not a view
                                        unit_toggle = ui.toggle({'$': '$', '%': '%'}, value=unit) \
                                            .props('no-caps dense unelevated aria-label="Dollars or percent"') \
                                            .classes('wiz-segmented wiz-edit-row__fixed')

                                        def sync(_=None):
                                            dollars = unit_toggle.value == '$'
                                            prefix.set_visibility(dollars)
                                            if dollars:
                                                share_input.props(remove='suffix')
                                            else:
                                                share_input.props('suffix="%"')
                                            hint.text = _share_hint(
                                                share_input.value, unit_toggle.value, split.total,
                                            )
                                            refresh_allocated()

                                        share_input.on_value_change(sync)
                                        unit_toggle.on_value_change(sync)

                                        def remove():
                                            holder.delete()
                                            widgets.pop(key, None)
                                            refresh_allocated()

                                        entrant_select = ui.select(
                                            options, value=entrant_id, label='Winner',
                                            with_input=True,
                                        ).props('outlined dense').classes('wiz-edit-row__lead')
                                        ui.button(icon='delete', on_click=remove) \
                                            .props('flat dense round color=negative') \
                                            .classes('wiz-edit-row__fixed') \
                                            .tooltip('Remove this place')
                                widgets[key] = {
                                    'place': place_input,
                                    'share': share_input,
                                    'unit': unit_toggle,
                                    'entrant': entrant_select,
                                }
                                sync()

                            def next_unit() -> str:
                                rows = list(widgets.values())
                                return rows[-1]['unit'].value if rows else '%'

                            ui.button('Add place', icon='add',
                                      on_click=lambda: add_row(len(widgets) + 1, '', unit=next_unit())) \
                                .props('flat color=primary')
                            with ui.row().classes('wiz-payout-allocated full-width items-center no-wrap'):
                                ui.label('Allocated').classes('col')
                                allocated_value = ui.label().classes('text-weight-medium')

                            for line in split.lines:
                                add_row(
                                    line.place,
                                    f'{line.amount:.2f}' if line.is_fixed else format_percentage(line.percentage),
                                    line.entrant.id if line.entrant else None,
                                    unit='$' if line.is_fixed else '%',
                                )
                            if not split.lines:
                                add_row(1, '')

                        async def submit():
                            payload = []
                            try:
                                for entry in widgets.values():
                                    percentage, amount = _parse_share(
                                        entry['share'].value, entry['unit'].value,
                                    )
                                    payload.append({
                                        'place': int(entry['place'].value or 0),
                                        'percentage': percentage,
                                        'amount': amount,
                                        'entrant_id': entry['entrant'].value,
                                    })
                                await service.set_split(
                                    row['tournament_id'], payload, await _current(),
                                )
                            except (ValueError, PermissionError) as e:
                                notify_error(e)
                                return
                            dialog.close()
                            ui.notify('Split saved', color='positive')
                            await refresh_table()

                        with dialog_actions().classes('justify-end'):
                            ui.button('Cancel', on_click=dialog.close).props('flat')
                            ui.button('Save', icon='save', on_click=submit).props('color=primary')
                dialog.open()

        async def open_sglive_dialog():
            actor = await _current()
            try:
                games = await service.fetch_sglive_games(actor)
                overviews = await service.list_overview(actor)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            tournaments = {o.tournament_id: o.name for o in overviews}
            current = {o.sglive_slug: o.tournament_id for o in overviews if o.sglive_slug}
            suggested = _suggest_links(
                [g for g in games if g.slug not in current],
                {tid: name for tid, name in tournaments.items() if tid not in current.values()},
            )
            options: dict[int | None, str] = {None: 'Not linked', **tournaments}
            pickers: dict[str, ui.select] = {}
            with table_container:
                with form_dialog('Link sglive pages') as dialog:
                    with ui.column().classes('gap-0 full-width'):
                        ui.label(
                            'Pick the tournament each sglive game pays out for. Linked pools '
                            'update every 15 minutes. Leave one on "Not linked" to keep typing '
                            'its pool by hand.'
                        ).classes('text-caption text-grey q-mb-sm')
                        for game in games:
                            with ui.element('div').classes('wiz-sglive-link'):
                                with ui.column().classes('gap-0 wiz-sglive-link__game'):
                                    ui.label(game.name).classes('text-weight-bold')
                                    ui.label(f'${game.total:,.2f} on sglive').classes('text-caption text-grey')
                                pickers[game.slug] = ui.select(
                                    options,
                                    value=current.get(game.slug, suggested.get(game.slug)),
                                    label='Tournament',
                                ).props('outlined dense').classes('wiz-sglive-link__pick')

                    async def submit():
                        try:
                            await service.set_sglive_links(
                                {slug: pick.value for slug, pick in pickers.items()},
                                await _current(),
                            )
                        except (ValueError, PermissionError) as e:
                            notify_error(e)
                            return
                        dialog.close()
                        ui.notify('sglive links saved', color='positive')
                        await refresh_table()

                    with dialog_actions().classes('justify-end'):
                        ui.button('Cancel', on_click=dialog.close).props('flat')
                        ui.button('Save', icon='save', on_click=busy_click('Saving…', submit)) \
                            .props('color=primary')
            dialog.open()

        async def sync_sglive_now():
            try:
                missing = await service.sync_sglive_pools(await _current())
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            _notify_synced(missing)
            await refresh_table()

        async def copy_block(row, client):
            with client:
                try:
                    block = await service.export_block(row['tournament_id'], await _current())
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                copy_to_clipboard(block, 'Payout block')

        with table_container:
            with ui.row().classes('full-width items-center'):
                with ui.button('Actions').props('flat color=primary icon-right=expand_more'):
                    with ui.menu():
                        ui.menu_item('Link sglive pages…', on_click=open_sglive_dialog)
                        ui.menu_item('Update from sglive now', on_click=sync_sglive_now)
                ui.space()
                refresh_button(refresh_table)

            table = ui.table(columns=columns, rows=[], row_key='tournament_id') \
                .classes('w-full wiz-table')

            table.add_slot('body-cell-pool', f'<q-td :props="props">{_POOL_CELL}</q-td>')
            table.add_slot('body-cell-bonus', '<q-td :props="props">{{ props.row.bonus }}</q-td>')
            table.add_slot('body-cell-total', '<q-td :props="props">{{ props.row.total }}</q-td>')
            table.add_slot('body-cell-allocated',
                           '<q-td :props="props">{{ props.row.allocated }}</q-td>')
            table.add_slot('body-cell-status', f'<q-td :props="props">{_STATUS_CHIP}</q-td>')
            table.add_slot('body-cell-actions', f'<q-td :props="props">{_ROW_ACTIONS}</q-td>')

            table.on('pool', lambda e: background_tasks.create(
                open_pool_dialog(e.args, context.client)))
            table.on('split', lambda e: background_tasks.create(
                open_split_dialog(e.args, context.client)))
            table.on('copy', lambda e: background_tasks.create(
                copy_block(e.args, context.client)))

            enable_mobile_grid(
                table, columns, actions=_ROW_ACTIONS,
                table_key=TableKeys.ADMIN_PAYOUTS,
                field_slots={
                    'pool': _POOL_CELL,
                    'bonus': '{{ props.row.bonus }}',
                    'total': '{{ props.row.total }}',
                    'allocated': '{{ props.row.allocated }}',
                    'status': _STATUS_CHIP,
                },
            )

        wire_tab_refresh('Payouts', refresh_table)
        background_tasks.create(refresh_table())
