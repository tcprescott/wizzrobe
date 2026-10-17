"""Admin Async Qualifiers — the Pools tab.

Where a qualifier's seeds come from: name the pools, point each at a preset, and
fill it by pasting permalinks or rolling them. The two things this tab has to get
right are both consequences of **reveal being start** — a permalink is handed to a
runner at the moment their slot is spent, so a mangled URL costs a run rather than
a click:

- every entry path validates the URL, and a paste reports the lines it skipped
  rather than only the count it took;
- one seed can be edited or removed on its own, which the service and REST have
  allowed since PR 9 with no control calling them (a single bad permalink used to
  mean deleting the pool it sat in).

Built as a factory for the same reason as ``reviewers``/``live_races``: the page
owns ``state`` and the loaders, and this tab reads the former and triggers the
latter.
"""

from typing import Any, Awaitable, Callable

from nicegui import ui

from application.services.async_qualifier.async_qualifier_pools import (
    MAX_ROLL_COUNT,
    roll_refusal,
)
from application.utils.duration import format_hms
from pages.admin_tabs.admin_qualifiers.shared import (
    BOARD_TAB,
    LIVE_TAB,
    POOL_PERMALINK_PREVIEW,
    POOLS_TAB,
    RUNS_TAB,
)
from theme.busy import busy_click, current_busy
from theme.dialog._helpers import dialog_actions, form_dialog
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.empty_state import empty_panel
from theme.menu import menu_action, more_menu


def build_pools_tab(
    *,
    state: dict,
    service: Any,
    current: Callable[[], Awaitable[Any]],
    reload_tabs: Callable[..., Awaitable[None]],
    placeholder: Callable[[str], bool],
    notify_error: Callable[[Exception], None],
) -> Callable[[], None]:
    """Return the refreshable Pools view."""

    @ui.refreshable
    def pools_view() -> None:
        if state.get('shell') is None or placeholder(POOLS_TAB):
            return
        qid = state['managing']
        preset_options = {p.id: f'{p.randomizer}/{p.name}' for p in state['presets']}
        with ui.row().classes('items-center'):
            ui.button('Add Pool', icon='add',
                      on_click=lambda: _open_pool_dialog(qid, preset_options)).props('color=primary')
        if not state['pools']:
            empty_panel('No pools yet. Add one, then paste or roll permalinks.', icon='inventory_2')
        with ui.element('div').classes('wiz-tile-grid wiz-tile-grid--md'):
            for pool in state['pools']:
                permalinks = list(pool.permalinks)
                with ui.card():
                    with ui.row().classes('items-center full-width'):
                        ui.label(pool.name).classes('text-subtitle1')
                        ui.badge(f'{len(permalinks)} permalink(s)', color='blue')
                        if pool.preset:
                            ui.badge(f'preset: {pool.preset.randomizer}/{pool.preset.name}', color='grey')
                        ui.space()
                        refusal = roll_refusal(pool.preset) if pool.preset else None
                        rollable = pool.preset is not None and refusal is None
                        if rollable:
                            ui.button('Roll', icon='casino',
                                      on_click=lambda pid=pool.id: _open_roll_dialog(pid)
                                      ).props('flat color=primary')
                        else:
                            ui.button('Add permalinks', icon='playlist_add',
                                      on_click=lambda pid=pool.id: _open_permalinks_dialog(pid)
                                      ).props('flat color=primary')
                        with more_menu(pool.name):
                            if rollable:
                                menu_action('playlist_add', 'Add permalinks',
                                            lambda pid=pool.id: _open_permalinks_dialog(pid))
                                ui.separator()
                            menu_action('delete', 'Delete pool', lambda p=pool: _delete_pool(p), negative=True)
                    # Said here rather than in the dialog the button used to open: that
                    # dialog held a count field and nothing that could change the preset,
                    # so the only exit was Cancel. A disabled flat button would not do
                    # either — Quasar keeps its colour at 0.7 opacity, which reads as live.
                    if refusal is not None:
                        with ui.row().classes('items-center text-caption text-orange'):
                            ui.icon('info')
                            ui.label(f'Cannot roll: {refusal}')
                    for pl in permalinks[:POOL_PERMALINK_PREVIEW]:
                        _permalink_row(pl)
                    # A pool holds as many seeds as the organiser rolled, and every one
                    # of them is a row of widgets. The tail is there when it is wanted.
                    rest = permalinks[POOL_PERMALINK_PREVIEW:]
                    if rest:
                        with ui.expansion(f'Show {len(rest)} more permalink(s)'
                                          ).classes('w-full text-caption'):
                            for pl in rest:
                                _permalink_row(pl)

    def _permalink_row(pl) -> None:
        with ui.row().classes('items-center'):
            ui.badge('live' if pl.live_race else 'async',
                     color='purple' if pl.live_race else 'teal')
            ui.link(pl.url, pl.url, new_tab=True).classes('text-caption')
            if pl.par_time:
                ui.badge(f'par {format_hms(pl.par_time)}', color='green')
            ui.space()
            with more_menu(pl.url):
                menu_action('edit', 'Edit permalink', lambda p=pl: _open_permalink_dialog(p))
                ui.separator()
                menu_action('delete', 'Remove permalink', lambda p=pl: _delete_permalink(p), negative=True)

    def _open_permalink_dialog(pl) -> None:
        with form_dialog('Edit Permalink', card_classes='dialog-card dialog-card--sm') as dialog:
            url_in = ui.input('URL', value=pl.url).classes('w-full font-mono')
            notes_in = ui.input('Notes (optional)', value=pl.notes or '').classes('w-full')
            live_in = ui.switch('Live race only', value=pl.live_race)
            ui.label('A live-race seed is never drawn by a self-paced runner, and its '
                     'pool counts as a slot only for whoever raced it.'
                     ).classes('text-caption text-grey')

            async def submit():
                try:
                    await service.update_permalink(
                        await current(), pl.id, url=url_in.value,
                        notes=notes_in.value, live_race=live_in.value,
                    )
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                ui.notify('Permalink updated', color='positive')
                dialog.close()
                # The live-race flag decides whether the pool is a slot a self-paced
                # runner was ever offered, so the board moves too.
                await reload_tabs(POOLS_TAB, LIVE_TAB, BOARD_TAB)

            with dialog_actions().classes('justify-end'):
                ui.button('Cancel', on_click=dialog.close).props('flat')
                ui.button('Save', icon='save', on_click=submit).props('color=primary')
        dialog.open()

    def _delete_permalink(pl) -> None:
        async def do_delete() -> None:
            try:
                await service.delete_permalink(await current(), pl.id)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            ui.notify('Permalink removed', color='positive')
            await reload_tabs(POOLS_TAB, LIVE_TAB, BOARD_TAB, RUNS_TAB)

        ConfirmationDialog(
            message=f'Remove this permalink from the pool?\n\n{pl.url}',
            on_confirm=do_delete, confirm_text='Remove', title='Remove permalink?',
        ).open()

    def _open_pool_dialog(qid: int, preset_options: dict) -> None:
        with form_dialog('Add Pool', card_classes='dialog-card dialog-card--sm') as dialog:
            name_in = ui.input('Pool name').classes('w-full')
            options = {None: '(no preset)', **preset_options}
            preset_in = ui.select(options, label='Preset (optional)', value=None).classes('w-full')

            async def submit():
                try:
                    await service.create_pool(await current(), qid,
                                              name=name_in.value, preset_id=preset_in.value)
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                ui.notify('Pool added', color='positive')
                dialog.close()
                # A pool is a slot per entrant, so the board's totals move too.
                await reload_tabs(POOLS_TAB, LIVE_TAB, BOARD_TAB)

            with dialog_actions().classes('justify-end'):
                ui.button('Cancel', on_click=dialog.close).props('flat')
                ui.button('Add', icon='add', on_click=submit).props('color=primary')
        dialog.open()

    def _open_permalinks_dialog(pool_id: int) -> None:
        # Whether anything landed, so the reload happens once, on close. This dialog
        # is built inside the refreshable's slot, so refreshing the tab **destroys
        # it** — which is fine when it has already closed and fatal when it is meant
        # to stay open holding the lines that were refused.
        added = {'any': False}

        with form_dialog('Add Permalinks', card_classes='dialog-card dialog-card--sm') as dialog:
            ui.label('One http(s) URL per line.').classes('text-caption text-grey')
            urls_in = ui.textarea('Permalink URLs').classes('w-full font-mono').props('rows=8')

            async def submit():
                lines = (urls_in.value or '').splitlines()
                try:
                    result = await service.add_permalinks_bulk(await current(), pool_id, urls=lines)
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                # A skipped line is reported line by line, and the dialog stays open
                # holding just those lines: the admin fixes the two that came across
                # mangled without re-finding the twenty that did not.
                ui.notify(result.summary,
                          color='warning' if result.rejected else 'positive',
                          multi_line=True, timeout=0 if result.rejected else None,
                          close_button='OK' if result.rejected else False)
                added['any'] = added['any'] or bool(result.created)
                if result.rejected:
                    urls_in.value = '\n'.join(line for _, line, _ in result.rejected)
                    return
                dialog.close()

            async def closed():
                # A pool that had only live-race seeds becomes runnable, which is a
                # board change as well as a pool one.
                if added['any']:
                    await reload_tabs(POOLS_TAB, LIVE_TAB, BOARD_TAB)

            dialog.on('hide', closed)

            with dialog_actions().classes('justify-end'):
                ui.button('Cancel', on_click=dialog.close).props('flat')
                ui.button('Add', icon='add', on_click=submit).props('color=primary')
        dialog.open()

    def _open_roll_dialog(pool_id: int) -> None:
        with form_dialog('Roll Permalinks', card_classes='dialog-card dialog-card--sm') as dialog:
            count_in = ui.number(f'How many (1–{MAX_ROLL_COUNT})', value=5,
                                 min=1, max=MAX_ROLL_COUNT, precision=0).classes('w-full')

            def clamp() -> None:
                """``ui.number(max=…)`` keeps 40 when 40 is typed — it marks the field
                invalid and hands the value over anyway, so the bound is enforced here
                rather than left to the service's refusal after the click."""
                try:
                    value = int(count_in.value or 1)
                except (TypeError, ValueError):
                    return
                if value > MAX_ROLL_COUNT:
                    count_in.value = MAX_ROLL_COUNT
                    ui.notify(f'{MAX_ROLL_COUNT} is the most one batch can roll — each '
                              'seed is a separate call to the generator.', color='warning')
                elif value < 1:
                    count_in.value = 1

            count_in.on_value_change(lambda _: clamp())
            # Every roll is a call to the generator, made one after another inside
            # this request: instant against a mock, minutes against a live one.
            ui.label('Rolling happens now, one seed at a time — a large batch against '
                     'a live randomizer takes a while. Leave this open until it '
                     'finishes.').classes('text-caption text-grey')

            async def submit():
                try:
                    created = await service.roll_permalinks(
                        await current(), pool_id, count=int(count_in.value or 1),
                        on_progress=lambda n, total: current_busy().progress('Rolling', n, total))
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                ui.notify(f'Rolled {len(created)} permalink(s)', color='positive')
                dialog.close()
                await reload_tabs(POOLS_TAB, LIVE_TAB, BOARD_TAB)

            with dialog_actions().classes('justify-end'):
                ui.button('Cancel', on_click=dialog.close).props('flat')
                ui.button('Roll', icon='casino', on_click=busy_click('Rolling…', submit)).props('color=primary')
        dialog.open()

    def _delete_pool(pool) -> None:
        async def do_delete() -> None:
            try:
                await service.delete_pool(await current(), pool.id)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            ui.notify('Pool deleted', color='positive')
            # Deleting a pool cascades its permalinks and detaches their runs, so the
            # runs list and the board both move.
            await reload_tabs(POOLS_TAB, LIVE_TAB, BOARD_TAB, RUNS_TAB)

        ConfirmationDialog(
            message=f'Delete the {pool.name} pool and all its permalinks? '
                    'Runs already played on it stay, but lose their pool.',
            on_confirm=do_delete, confirm_text='Delete', title='Delete pool?',
        ).open()

    return pools_view
