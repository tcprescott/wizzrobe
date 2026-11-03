"""The Preferences modal behind every customizable table's gear.

Staged, not live: every control edits a local copy and **Cancel** discards it, so
half-finished experiments never reach the database — the AWS console's
`CollectionPreferences` shape, which is what this was modelled on.

The ▲/▼ buttons and the width steppers are the keyboard and touch equivalents of
drag-to-reorder and drag-to-resize. They are not a fallback to be removed once
dragging exists: a dialog whose whole purpose is making a table usable must not
itself be mouse-only.
"""

from dataclasses import dataclass

from nicegui import ui

from application.services import TablePreferenceService
from theme.dialog._helpers import dialog_actions, dialog_more_menu, form_dialog
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.menu import menu_action
from theme.tables.admin_crud import current_actor
from theme.tables.preferences import apply_saved_config, customization_of

DENSITY_LABELS = {'comfortable': 'Comfortable', 'compact': 'Compact'}


@dataclass
class _Staged:
    """The dialog's working copy of the scalar preferences.

    A dataclass rather than a dict so each field keeps its own type: the radios
    and the switch hand back whatever Quasar sends, and a shared dict would erase
    the difference between a page size and a density.
    """

    density: str
    wrap: bool

    def set_density(self, value) -> None:
        self.density = str(value)

    def set_wrap(self, value) -> None:
        self.wrap = bool(value)


FALLBACK_LABELS = {'actions': 'Row actions', 'edit': 'Edit'}


def _label_for(column) -> str:
    label = str(column.get('label') or '').strip()
    if label:
        return label
    name = str(column.get('name', ''))
    return FALLBACK_LABELS.get(name) or name.replace('_', ' ').title() or '(unlabelled)'


def _staged_columns(custom) -> list[dict]:
    """The dialog's working copy: one entry per column, in the plan's order."""
    visible = set(custom.plan.visible)
    return [
        {
            'name': col.get('name', ''),
            'label': _label_for(col),
            'visible': col.get('name', '') in visible,
            'width': custom.plan.widths.get(col.get('name', '')),
        }
        for col in custom.plan.columns
    ]


def _config_from(staged: list[dict], page_size: int, density: str, wrap: bool) -> dict:
    return {
        'columns': [
            {'name': c['name'], 'visible': bool(c['visible']),
             'width': int(c['width']) if c['width'] else None}
            for c in staged
        ],
        'page_size': page_size,
        'density': density,
        'wrap': wrap,
    }


async def open_table_preferences(table: ui.table) -> None:
    """Open the Preferences dialog for a table already passed to ``customize_table``."""
    custom = customization_of(table)
    if custom is None:
        ui.notify('This table is not customizable.', color='warning')
        return
    actor = await current_actor()
    if actor is None:
        ui.notify('Sign in to save table preferences.', color='warning')
        return

    service = TablePreferenceService()
    staged = _staged_columns(custom)
    state = _Staged(density=custom.plan.density, wrap=custom.plan.wrap)

    with form_dialog('Table preferences') as dialog:
        with ui.column().classes('q-pa-md gap-3 full-width'):
            with ui.column().classes('gap-1'):
                ui.label('Density').classes('text-caption text-grey-7')
                ui.radio(DENSITY_LABELS, value=state.density,
                         on_change=lambda e: state.set_density(e.value)) \
                    .props('inline dense')
            ui.switch('Wrap long values', value=state.wrap,
                      on_change=lambda e: state.set_wrap(e.value))

            ui.separator()
            ui.label('Columns').classes('text-caption text-grey-7')
            column_list = ui.column().classes('full-width gap-0')

        dragging = {'index': -1}

        def move(index: int, delta: int) -> None:
            target = index + delta
            if 0 <= target < len(staged):
                staged[index], staged[target] = staged[target], staged[index]
                render_columns()

        def drop(source_index: int, target_index: int) -> None:
            if source_index == target_index or not 0 <= source_index < len(staged):
                return
            entry = staged.pop(source_index)
            staged.insert(min(target_index, len(staged)), entry)
            render_columns()

        def render_columns() -> None:
            column_list.clear()
            with column_list:
                for index, entry in enumerate(staged):
                    required = entry['name'] in custom.required
                    row = ui.row().classes(
                        'full-width items-center gap-2 q-py-xs wiz-prefs-column-row')
                    row.props('draggable="true"')
                    row.on('dragstart', lambda _, i=index: dragging.update(index=i))
                    row.on('dragover.prevent', lambda: None)
                    row.on('drop', lambda _, i=index: drop(dragging['index'], i))
                    with row:
                        ui.icon('drag_indicator').classes('text-grey-6 cursor-move') \
                            .tooltip('Drag to reorder')
                        checkbox = ui.checkbox(
                            value=True if required else entry['visible'],
                            on_change=lambda e, c=entry: c.update(visible=e.value),
                        ).props('dense')
                        if required:
                            checkbox.props('disable')
                        ui.label(entry['label']).classes('col-grow')
                        if required:
                            ui.label('Always shown').classes('text-caption text-grey-7')
                        ui.number(
                            value=entry['width'], placeholder='auto',
                            min=TablePreferenceService.MIN_WIDTH,
                            max=TablePreferenceService.MAX_WIDTH,
                            on_change=lambda e, c=entry: c.update(width=e.value),
                        ).props('dense outlined suffix=px').classes('w-24') \
                            .tooltip('Column width in pixels; blank for automatic')
                        ui.button(icon='arrow_upward', on_click=lambda _, i=index: move(i, -1)) \
                            .props('flat dense round').tooltip('Move up') \
                            .set_enabled(index > 0)
                        ui.button(icon='arrow_downward', on_click=lambda _, i=index: move(i, 1)) \
                            .props('flat dense round').tooltip('Move down') \
                            .set_enabled(index < len(staged) - 1)

        render_columns()

        async def confirm() -> None:
            # Re-read at Confirm: the footer may have saved a new size meanwhile.
            config = _config_from(staged, custom.plan.page_size, state.density, state.wrap)
            try:
                saved = await service.save(actor, custom.key, config)
            except ValueError as e:
                ui.notify(str(e), color='warning')
                return
            apply_saved_config(table, saved)
            ui.notify('Preferences saved', color='positive')
            dialog.close()

        async def do_reset() -> None:
            await service.reset(actor, custom.key)
            apply_saved_config(table, None)
            ui.notify('Table reset to defaults', color='positive')
            dialog.close()

        def reset() -> None:
            ConfirmationDialog(
                message='Put this table’s columns, order, widths and page size back to '
                        'the defaults?',
                on_confirm=do_reset, confirm_text='Reset', title='Reset table?',
            ).open()

        with dialog_actions():
            with dialog_more_menu():
                menu_action('restart_alt', 'Reset to defaults…', reset, negative=True)
            ui.space()
            ui.button('Cancel', on_click=dialog.close).props('flat')
            ui.button('Confirm', on_click=confirm).props('color=primary')

    dialog.open()
