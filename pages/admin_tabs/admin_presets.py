"""Admin Presets Page — tenant-authored seed-rolling presets (PRESET_MANAGER/STAFF)."""

import json
from itertools import groupby

from nicegui import app, background_tasks, context, ui

from application.services import (
    PresetService,
    RandomizerCredentialService,
    SeedGenerationService,
    get_user_from_session,
)
from application.utils.discord_messages import is_seed_link
from theme.busy import busy_click
from theme.clipboard import copy_to_clipboard
from theme.dialog._helpers import dialog_actions, form_dialog
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.empty_state import empty_state
from theme.help import help_icon
from theme.menu import menu_action
from theme.notify import notify_error
from theme.page_header import page_header
from theme.pickers import CHIP_PROPS
from theme.tables.admin_crud import current_actor, refresh_button, wire_tab_refresh
from theme.waiting import waiting_panel

# Randomizers whose settings are one string copied out of the randomizer:
# (settings key, field label, placeholder, hint under the field).
_PASTE_FIELDS = {
    'wwr': (
        'permalink', 'Permalink', "Paste the permalink from the randomizer's Permalink box",
        'Each roll keeps the build and settings and only changes the seed.',
    ),
    'ff1r': (
        'url', 'Flags link', 'https://4-9-7.finalfantasyrandomizer.com/?s=00000000&f=…',
        "Copy the link from the randomizer's address bar after setting the flags. "
        'Each roll keeps the flags and site version and only changes the seed.',
    ),
    'z1r': (
        'flags', 'Flags', 'The flag string from the randomizer',
        'Each roll adds a fresh seed number to these flags.',
    ),
    'z2r': (
        'flags', 'Flags', 'The flag string from the randomizer',
        'Each roll adds a fresh seed number to these flags.',
    ),
    'smb3r': (
        'flags', 'Flags', 'The flag string from the randomizer',
        'Each roll adds a fresh seed number to these flags.',
    ),
    'aosr': (
        'url', 'Randomizer link', 'https://aosrando.surge.sh/?…',
        'The aosrando.surge.sh link with your options in it. Each roll keeps the options and only changes the seed.',
    ),
    'avianart': (
        'preset', 'AVIANART preset', 'crosskeys',
        'The preset name from avianart.games, or owner/name for a user preset. '
        'Import → From a randomizer… lists the published ones.',
    ),
}


async def admin_presets_page() -> None:
    service = PresetService()
    # Randomizers whose API key this community has not configured are dropped from
    # the create/edit select (mirrors the tournament dialog); a stored preset on
    # such a randomizer stays valid and editable (its value is re-added below).
    configured = await RandomizerCredentialService().configured_randomizers()
    available_randomizers = SeedGenerationService.available_randomizers(configured)

    with ui.column().classes('page-container-narrow'):
        with page_header(
            'Seed Presets',
            'Named randomizer settings a tournament rolls its seeds from. '
            "Set one on a tournament and it wins over the tournament's Seed Generator.",
        ):
            await help_icon('admin-preset-test-roll', user=await current_actor())

        state: dict = {'rows': None, 'usage': {}}

        async def _current():
            return await get_user_from_session(app.storage.user)

        async def refresh_table():
            # Re-read the configured set, not just the rows: the admin tabs are
            # built once per page load, so a key entered on the Randomizer Keys
            # tab would otherwise not reach this select until a full reload —
            # and "set the key, then author the preset" is the expected flow.
            # ``wire_tab_refresh`` runs this on every switch back to this tab.
            nonlocal available_randomizers
            available_randomizers = SeedGenerationService.available_randomizers(
                await RandomizerCredentialService().configured_randomizers()
            )
            actor = await _current()
            presets = await service.list_presets(actor)
            state['usage'] = await service.usage(actor)
            state['rows'] = [
                {
                    'id': p.id,
                    'randomizer': p.randomizer,
                    'name': p.name,
                    'description': p.description or '',
                    'settings': json.dumps(p.settings, indent=2),
                }
                for p in presets
            ]
            preset_list.refresh()

        def used_by(row) -> str:
            count = len(state['usage'].get(row['id'], []))
            if not count:
                return 'Not used'
            return f"{count} tournament{'s' if count != 1 else ''}"

        @ui.refreshable
        def preset_list() -> None:
            """One line per preset, grouped under its randomizer.

            The rows arrive ordered by randomizer then name, so ``groupby`` is
            the grouping. Every action sits behind one menu per line: three icon
            buttons wrapped onto a second line and doubled every row's height.
            """
            if state['rows'] is None:
                return
            if not state['rows']:
                empty_state('No presets yet. Import the built-ins to start from a working set.')
                return
            with ui.element('div').classes('preset-list'):
                for randomizer, group in groupby(state['rows'], key=lambda r: r['randomizer']):
                    rows = list(group)
                    with ui.element('section').classes('preset-list__group'):
                        with ui.element('div').classes('preset-list__head'):
                            ui.label(SeedGenerationService.randomizer_name(randomizer)) \
                                .classes('preset-list__title')
                            ui.label(
                                f"{randomizer} · {len(rows)} preset{'s' if len(rows) != 1 else ''}"
                            ).classes('preset-list__count')
                        for row in rows:
                            preset_line(row)

        def preset_line(row) -> None:
            with ui.element('div').classes('preset-list__line'):
                ui.button(row['name'], on_click=lambda r=row: open_preset_dialog(r), color=None) \
                    .props('flat no-caps dense align=left') \
                    .classes('preset-list__name')
                desc = ui.label(row['description']).classes('preset-list__desc')
                if row['description']:
                    desc.props['title'] = row['description']
                ui.label(used_by(row)).classes('preset-list__used')
                # No tooltip: it opened over the menu's first item.
                menu_button = ui.button(icon='more_vert', color=None).props('flat round dense') \
                    .classes('preset-list__menu')
                menu_button.props['aria-label'] = f"More for {row['name']}"
                with menu_button:
                    with ui.menu().props('anchor="bottom right" self="top right"'):
                        menu_action('science', 'Test roll', lambda r=row: open_test_roll_dialog(r))
                        menu_action('edit', 'Edit', lambda r=row: open_preset_dialog(r))
                        ui.separator()
                        menu_action('delete', 'Delete…', lambda r=row: confirm_delete(r), negative=True)

        def confirm_delete(row) -> None:
            names = state['usage'].get(row['id'], [])
            message = f"Delete {row['name']}?"
            if names:
                listed = '\n'.join(f'• {n}' for n in names)
                message += (
                    f"\n\n{'These tournaments roll' if len(names) > 1 else 'This tournament rolls'} "
                    f"from it and will need another preset picked:\n{listed}"
                )
            ConfirmationDialog(
                message=message, title='Delete preset', confirm_text='Delete',
                on_confirm=lambda: background_tasks.create(delete_preset(row, context.client)),
            ).open()

        async def delete_preset(row, client):
            with client:
                try:
                    await service.delete_preset(await _current(), row['id'])
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                ui.notify('Preset deleted', color='positive')
                await refresh_table()

        async def import_builtins(client):
            with client:
                try:
                    created = await service.import_builtins(await _current())
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                if created:
                    ui.notify(f'Imported {len(created)} preset(s)', color='positive')
                else:
                    ui.notify('No new presets to import', color='info')
                await refresh_table()

        def open_test_roll_dialog(row) -> None:
            with list_container:
                with form_dialog(f"Test roll: {row['name']}", card_classes='dialog-card dialog-card--sm') as dialog:
                    ui.label(
                        "Rolls one seed the way a match would, so you can check the "
                        "randomizer works. It isn't attached to any match."
                    ).classes('text-caption text-grey')
                    body = ui.column().classes('w-full')
                    with body:
                        waiting_panel(
                            'Rolling… DK64 can take a few minutes.'
                            if row['randomizer'] in SeedGenerationService.ASYNC_RANDOMIZERS
                            else 'Rolling…',
                            size='md',
                        )
            dialog.open()
            background_tasks.create(run_test_roll(row, body, context.client))

        async def run_test_roll(row, body, client):
            # Same shape as the handlers above: the actor and the tenant both
            # resolve from the client's context, so the whole roll runs inside it.
            with client:
                try:
                    result = await service.test_roll(await _current(), row['id'])
                except (ValueError, PermissionError) as e:
                    body.clear()
                    with body:
                        with ui.row().classes('items-center no-wrap'):
                            ui.icon('error', color='negative')
                            ui.label('The roll failed.').classes('text-weight-medium')
                        ui.label(str(e)).classes('text-body2')
                    return
                body.clear()
                with body:
                    with ui.row().classes('items-center no-wrap'):
                        ui.icon('check_circle', color='positive')
                        ui.label('The randomizer rolled a seed.').classes('text-weight-medium')
                    if is_seed_link(result.seed_url):
                        ui.link(result.seed_url, result.seed_url, new_tab=True).classes('break-all')
                    else:
                        with ui.row().classes('items-center no-wrap w-full'):
                            ui.label(result.seed_url).classes('seed-value break-all')
                            ui.button(
                                icon='content_copy',
                                on_click=lambda: copy_to_clipboard(result.seed_url, 'Seed'),
                            ).props('flat round dense aria-label="Copy seed"').tooltip('Copy seed')
                    if not result.used_preset_settings:
                        ui.label(
                            f"{result.randomizer} rolls its bundled settings and ignores "
                            "this preset's, so this checks the randomizer, not this row."
                        ).classes('text-caption text-grey')

        def open_remote_dialog() -> None:
            """Browse a randomizer's own published presets and keep the useful ones.

            The alternative to hand-authoring a settings blob: the community
            picks from what the randomizer's site already offers its players.
            """
            remote_choices = [
                r for r in available_randomizers
                if SeedGenerationService.offers_remote_presets(r)
            ]
            if not remote_choices:
                ui.notify(
                    'No randomizer with a preset catalogue is configured. '
                    'Add its API key under Randomizer Keys first.',
                    color='warning',
                )
                return

            existing_names = {(r['randomizer'], r['name']) for r in state['rows'] or []}
            selected: set[str] = set()
            catalogue_state: dict = {}

            with list_container:
                with form_dialog('Import from Randomizer', card_classes='dialog-card dialog-card--md') as dialog:
                    ui.label(
                        "Fetches the randomizer's published presets. Importing one "
                        'saves it as a preset of your own — later changes upstream '
                        'do not follow it.'
                    ).classes('text-caption text-grey')

                    first_branches = SeedGenerationService.remote_preset_branches(
                        remote_choices[0]
                    )
                    randomizer_input = ui.select(
                        remote_choices, label='Randomizer', value=remote_choices[0],
                    ).classes('w-full')
                    branch_input = ui.select(
                        first_branches, label='Branch',
                        value=first_branches[0] if first_branches else None,
                    ).classes('w-full')
                    branch_input.set_visibility(bool(first_branches))

                    def reset_catalogue() -> None:
                        selected.clear()
                        catalogue_state.clear()
                        results.refresh()

                    def on_randomizer_change() -> None:
                        branches = SeedGenerationService.remote_preset_branches(
                            randomizer_input.value
                        )
                        branch_input.options = branches
                        branch_input.value = branches[0] if branches else None
                        branch_input.set_visibility(bool(branches))
                        branch_input.update()
                        reset_catalogue()

                    randomizer_input.on_value_change(lambda _: on_randomizer_change())
                    branch_input.on_value_change(lambda _: reset_catalogue())

                    @ui.refreshable
                    def results() -> None:
                        """The picker itself, rebuilt whenever the catalogue changes.

                        A searchable multi-select rather than a checkbox list:
                        DK64R publishes dozens of presets, and a list that long
                        is a scroll-and-squint exercise. Typing filters it.
                        """
                        if not catalogue_state:
                            ui.label(
                                'Fetch the catalogue to choose presets.'
                            ).classes('text-caption text-grey')
                            return

                        catalogue = catalogue_state['entries']
                        if not catalogue:
                            ui.label(
                                'This randomizer published no presets.'
                            ).classes('text-caption text-grey')
                            return

                        importable = {
                            e.name: e for e in catalogue
                            if (randomizer_input.value, e.name) not in existing_names
                        }
                        already = len(catalogue) - len(importable)
                        if not importable:
                            ui.label(
                                f'All {len(catalogue)} published presets are already imported.'
                            ).classes('text-caption text-grey')
                            return

                        picker = ui.select(
                            # Description in the option label so the search box
                            # matches on it too — "sprint" finds the sprint
                            # rulesets whatever they happen to be called.
                            {
                                name: (f'{name} — {entry.description}'
                                       if entry.description else name)
                                for name, entry in importable.items()
                            },
                            label='Presets to import',
                            multiple=True, with_input=True, clearable=True,
                            value=sorted(selected),
                        ).classes('w-full').props(CHIP_PROPS)

                        def on_pick(event) -> None:
                            selected.clear()
                            selected.update(event.value or [])

                        picker.on_value_change(on_pick)

                        note = f'{len(importable)} available'
                        if already:
                            note += f', {already} already imported'
                        ui.label(note).classes('text-caption text-grey')

                    ui.button(
                        'Fetch presets', icon='cloud_download',
                        on_click=busy_click('Fetching…', lambda: fetch()),
                    ).props('flat color=primary')
                    results()

                    async def fetch() -> None:
                        try:
                            catalogue = await service.list_remote_presets(
                                await _current(), randomizer_input.value,
                                branch=branch_input.value,
                            )
                        except (ValueError, PermissionError) as e:
                            notify_error(e)
                            return
                        selected.clear()
                        catalogue_state['entries'] = catalogue
                        results.refresh()

                    async def do_import() -> None:
                        try:
                            created = await service.import_remote_presets(
                                await _current(), randomizer_input.value,
                                sorted(selected), branch=branch_input.value,
                            )
                        except (ValueError, PermissionError) as e:
                            notify_error(e)
                            return
                        if created:
                            ui.notify(f'Imported {len(created)} preset(s)', color='positive')
                        else:
                            ui.notify('Those presets are already imported', color='info')
                        dialog.close()
                        await refresh_table()

                    with dialog_actions().classes('justify-end'):
                        ui.button('Cancel', on_click=dialog.close).props('flat')
                        ui.button(
                            'Import', icon='download', on_click=busy_click('Importing…', do_import),
                        ).props('color=primary')

                    on_randomizer_change()
            dialog.open()

        def open_preset_dialog(existing=None) -> None:
            is_edit = existing is not None
            with list_container:
                with form_dialog('Edit Preset' if is_edit else 'Add Preset', card_classes='dialog-card dialog-card--md') as dialog:
                    name_input = ui.input(
                        'Name', value=existing['name'] if is_edit else '',
                    ).classes('w-full')
                    randomizer_choices = list(available_randomizers)
                    # Never drop the row's current randomizer from its own editor.
                    if is_edit and existing['randomizer'] not in randomizer_choices:
                        randomizer_choices = [*randomizer_choices, existing['randomizer']]
                    randomizer_input = ui.select(
                        randomizer_choices,
                        label='Randomizer',
                        value=existing['randomizer'] if is_edit else 'alttpr',
                    ).classes('w-full')
                    description_input = ui.input(
                        'Description', value=existing['description'] if is_edit else '',
                    ).classes('w-full')
                    settings_input = ui.textarea(
                        'Settings (JSON)',
                        value=existing['settings'] if is_edit else '{}',
                    ).classes('w-full font-mono').props('rows=12')
                    # Settings that are one string copied out of the randomizer (a
                    # permalink, flags, a link, a preset name) get a field for that
                    # instead of JSON.
                    existing_paste = ''
                    if is_edit and existing['randomizer'] in _PASTE_FIELDS:
                        key = _PASTE_FIELDS[existing['randomizer']][0]
                        existing_paste = json.loads(existing['settings'] or '{}').get(key, '')
                    paste_input = ui.textarea(value=existing_paste).classes('w-full seed-value').props(
                        'rows=2 autogrow',
                    )
                    paste_hint = ui.label().classes('text-body2 text-grey')

                    @ui.refreshable
                    def paste_summary() -> None:
                        value = (paste_input.value or '').strip()
                        if not value:
                            return
                        randomizer = randomizer_input.value
                        if randomizer in ('aosr', 'avianart'):
                            return
                        try:
                            if randomizer == 'wwr':
                                info = SeedGenerationService.describe_wwr_permalink(value)
                            elif randomizer == 'ff1r':
                                version = SeedGenerationService.describe_ff1r_link(value)
                            else:
                                example = SeedGenerationService.example_flag_seed(randomizer, value)
                        except ValueError as e:
                            ui.label(str(e)).classes('text-negative text-body2')
                            return
                        if randomizer == 'ff1r':
                            with ui.row().classes('preset-check w-full items-center no-wrap gap-3'):
                                with ui.column().classes('gap-0 col'):
                                    ui.label(f'Site version {version}').classes('text-weight-medium')
                                    ui.label(
                                        "Open it to check the flags on the randomizer's own page",
                                    ).classes('text-body2')
                                ui.link('Open on site', value, new_tab=True).classes(
                                    'text-weight-medium no-wrap',
                                )
                            return
                        if SeedGenerationService.rolls_from_flags(randomizer):
                            with ui.column().classes('preset-check w-full gap-1'):
                                ui.label('Players will get something like').classes('text-body2 text-grey')
                                ui.label(example).classes('seed-value break-all')
                            return
                        ui.label(f'Build {info.build}').classes('text-weight-medium')
                        if info.summary is None:
                            ui.label(
                                "Settings summary not available for this build. It still rolls: "
                                "players' permalinks keep this build and these settings."
                            ).classes('text-grey text-body2')
                            return
                        for line in info.summary:
                            ui.label(line).classes('text-body2')

                    with ui.column().classes('gap-1 w-full') as summary_box:
                        paste_summary()
                    paste_input.on_value_change(lambda _: paste_summary.refresh())

                    def show_settings_editor() -> None:
                        field = _PASTE_FIELDS.get(randomizer_input.value)
                        settings_input.set_visibility(field is None)
                        paste_input.set_visibility(field is not None)
                        paste_hint.set_visibility(field is not None)
                        summary_box.set_visibility(field is not None)
                        if field is not None:
                            _, label, placeholder, hint = field
                            paste_input.props['label'] = label
                            paste_input.props['placeholder'] = placeholder
                            paste_input.update()
                            paste_hint.set_text(hint)
                        paste_summary.refresh()

                    randomizer_input.on_value_change(lambda _: show_settings_editor())
                    show_settings_editor()

                    async def submit():
                        field = _PASTE_FIELDS.get(randomizer_input.value)
                        if field is not None:
                            settings = {field[0]: paste_input.value or ''}
                        else:
                            try:
                                settings = json.loads(settings_input.value or '{}')
                            except json.JSONDecodeError as e:
                                ui.notify(f'Settings must be valid JSON: {e}', color='warning')
                                return
                        try:
                            current = await _current()
                            if is_edit:
                                await service.update_preset(
                                    current, existing['id'],
                                    name=name_input.value,
                                    randomizer=randomizer_input.value,
                                    settings=settings,
                                    description=description_input.value,
                                )
                                ui.notify('Preset updated', color='positive')
                            else:
                                await service.create_preset(
                                    current,
                                    name=name_input.value,
                                    randomizer=randomizer_input.value,
                                    settings=settings,
                                    description=description_input.value,
                                )
                                ui.notify('Preset created', color='positive')
                            dialog.close()
                            await refresh_table()
                        except (ValueError, PermissionError) as e:
                            notify_error(e)

                    with dialog_actions().classes('justify-end'):
                        ui.button('Cancel', on_click=dialog.close).props('flat')
                        ui.button(
                            'Save' if is_edit else 'Add', icon='save' if is_edit else 'add',
                            on_click=submit,
                        ).props('color=primary')
            dialog.open()

        with ui.row().classes('w-full items-center'):
            ui.button('Add Preset', icon='add', on_click=lambda: open_preset_dialog()).props('color=primary')
            with ui.button('Import').props('outline color=primary icon-right=expand_more'):
                with ui.menu():
                    menu_action(
                        'download', 'Built-in presets',
                        lambda: background_tasks.create(import_builtins(context.client)),
                    )
                    menu_action('cloud_download', 'From a randomizer…', open_remote_dialog)
            ui.space()
            refresh_button(refresh_table)

        list_container = ui.column().classes('w-full')
        with list_container:
            preset_list()

        wire_tab_refresh('Seed Presets', refresh_table)
        background_tasks.create(refresh_table())

