"""The Monitoring section of ``/platform``: what people's browsers send to Sentry.

Mid-event a noisy browser error can flood Sentry, so a super-admin pauses
browser errors, sends a share of them, turns replays off, or ignores one error
by a piece of its message, all without a deploy. Each change reaches every open
tab at once (:func:`theme.browser_monitoring.push_to_open_tabs`); switching
browser errors back on reaches each tab on its next page load, because a tab
that loaded while they were off never loaded the SDK.
"""

from nicegui import ui

from application.services import BrowserMonitoringService
from application.utils.browser_monitoring import MATCH_MESSAGE, MATCH_URL, SAMPLE_RATES
from application.utils.timezone import format_local_display
from pages.platform_shared import current_actor
from theme.browser_monitoring import push_to_open_tabs
from theme.dialog._helpers import dialog_actions, form_dialog
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.menu import SlotItem, slot_more_menu
from theme.notify import notify_error
from theme.section import section_panel
from theme.tables.mobile_grid import enable_mobile_grid
from theme.tables.preferences import TableKeys

_MATCH_LABELS = {MATCH_MESSAGE: 'Message', MATCH_URL: 'Script URL'}
_SHARE_OPTIONS = {rate: f'{round(rate * 100)}%' for rate in SAMPLE_RATES}

_PATTERN = '<code class="wiz-rule-pattern">{{ props.row.pattern }}</code>'
_MATCH_CHIP = '<span class="wiz-chip wiz-chip--neutral" style="white-space:nowrap">{{ props.row.match_label }}</span>'
_ROW_ACTIONS = (
    '<template v-if="!props.row.builtin">'
    + slot_more_menu('props.row.pattern', [
        SlotItem('delete', 'Remove rule…', 'remove_rule', negative=True),
    ])
    + '</template>'
)


async def render_monitoring_section(user) -> None:
    """Build the settings panel and the ignore-rule table, then load both."""
    with ui.element('div').classes('wiz-platform-monitoring w-full'):
        settings_panel = await section_panel(
            'Browser errors', icon='bug_report', user=user,
            subtitle='Errors in people’s browsers go to Sentry tagged surface: browser, '
                     'each with a masked recording of the minute before it.',
        )
        rules_panel = await section_panel(
            'Ignored errors', icon='filter_alt_off', user=user,
            subtitle='An error whose message or script URL contains one of these never '
                     'leaves the browser. Built-in rules cover extensions, dropped '
                     'connections and browser quirks.',
        )

    with settings_panel.body.classes('w-full gap-0'):
        status = ui.row().classes('items-center gap-2 q-mb-sm')
        enabled = _setting_row(
            'Send browser errors',
            'Pausing stops open tabs at once. Turning it back on reaches each tab on its next page load.',
        )
        replay = _setting_row(
            'Replay on error',
            'Uploads a masked recording of the minute before each error, including a tap the server failed.',
        )
        with ui.row().classes('w-full items-center no-wrap gap-3 q-py-sm'):
            with ui.column().classes('col gap-0'):
                ui.label('Share of errors sent').classes('text-weight-bold')
                ui.label('Lower this if a flood is eating the Sentry quota.').classes('text-caption')
            share = ui.select(_SHARE_OPTIONS, value=1.0, label='Share').classes('w-28')

    # set_value() fires on_value_change like a person's change does.
    loading = {'now': False}

    async def load_settings() -> None:
        view = await BrowserMonitoringService().get_settings(await current_actor())
        loading['now'] = True
        try:
            enabled.set_value(view.enabled)
            replay.set_value(view.replay_on_error)
            share.set_value(view.sample_rate)
        finally:
            loading['now'] = False
        status.clear()
        with status:
            if view.enabled:
                ui.html('<span class="wiz-chip wiz-chip--ok">Sending</span>')
            else:
                ui.html('<span class="wiz-chip wiz-chip--pending">Paused</span>')
            changed = (
                f' · changed by {view.updated_by or "unknown"}, {format_local_display(view.updated_at)}'
                if view.updated_at else ''
            )
            ui.label(
                f'Replay {"on" if view.replay_on_error else "off"} · '
                f'{_SHARE_OPTIONS.get(view.sample_rate, view.sample_rate)}{changed}'
            ).classes('text-caption')

    async def save_settings() -> None:
        try:
            await BrowserMonitoringService().update_settings(
                user, enabled=bool(enabled.value), replay_on_error=bool(replay.value),
                sample_rate=float(share.value),
            )
        except (ValueError, PermissionError) as e:
            notify_error(e)
            await load_settings()
            return
        tabs = push_to_open_tabs()
        ui.notify(f'Saved. {tabs} open tab{"" if tabs == 1 else "s"} updated.', color='positive')
        await load_settings()

    await load_settings()
    async def on_change() -> None:
        if not loading['now']:
            await save_settings()

    for control in (enabled, replay, share):
        control.on_value_change(on_change)

    columns: list[dict] = [
        {'name': 'pattern', 'label': 'Ignore when', 'field': 'pattern', 'align': 'left'},
        {'name': 'match', 'label': 'Matches', 'field': 'match_label', 'align': 'left'},
        {'name': 'added_by', 'label': 'Added by', 'field': 'added_by', 'align': 'left'},
        {'name': 'added', 'label': 'Added', 'field': 'added', 'align': 'left'},
        {'name': 'actions', 'label': '', 'field': 'actions', 'align': 'right'},
    ]
    with rules_panel.body.classes('w-full'):
        table = ui.table(columns=columns, rows=[], row_key='key').classes('w-full wiz-table')
        table.add_slot('body-cell-pattern', f'<q-td :props="props">{_PATTERN}</q-td>')
        table.add_slot('body-cell-match', f'<q-td :props="props">{_MATCH_CHIP}</q-td>')
        table.add_slot('body-cell-actions', f'<q-td :props="props">{_ROW_ACTIONS}</q-td>')
        enable_mobile_grid(
            table, columns, actions=_ROW_ACTIONS,
            table_key=TableKeys.PLATFORM_BROWSER_IGNORE_RULES,
            field_slots={'pattern': _PATTERN, 'match': _MATCH_CHIP},
            wrap=True,
        )
    with rules_panel.aside:
        ui.button('Ignore an error', icon='add', on_click=lambda: _open_rule_dialog(user, table)) \
            .props('color=primary no-caps')

    async def _on_remove(e) -> None:
        _confirm_remove(user, table, e.args)

    table.on('remove_rule', _on_remove)
    await _refresh(table)


def _setting_row(title: str, caption: str) -> ui.switch:
    with ui.row().classes('w-full items-center no-wrap gap-3 q-py-sm'):
        with ui.column().classes('col gap-0'):
            ui.label(title).classes('text-weight-bold')
            ui.label(caption).classes('text-caption')
        return ui.switch().props(f'aria-label="{title}"')


async def _refresh(table) -> None:
    rows = await BrowserMonitoringService().list_rules(await current_actor())
    table.rows = [
        {
            'key': f'builtin:{r.match}:{r.pattern}' if r.builtin else f'rule:{r.id}',
            'id': r.id,
            'builtin': r.builtin,
            'pattern': r.pattern,
            'match_label': _MATCH_LABELS.get(r.match, r.match),
            'added_by': 'Built in' if r.builtin else (r.created_by or 'unknown'),
            'added': format_local_display(r.created_at) if r.created_at else '—',
        }
        for r in rows
    ]
    table.update()


def _open_rule_dialog(actor, table) -> None:
    with form_dialog('Ignore a browser error', card_classes='dialog-card dialog-card--sm gap-2') as dialog:
        pattern = ui.input(
            'Ignore when it contains', placeholder="Cannot read properties of null (reading 'focus')",
        ).classes('w-full').props('hint="A distinctive part of the error, as Sentry shows it. Not case-sensitive."')
        match = ui.select(
            {MATCH_MESSAGE: 'The error message', MATCH_URL: 'A script URL in its stack'},
            value=MATCH_MESSAGE, label='Look in',
        ).classes('w-full')

        async def submit() -> None:
            try:
                await BrowserMonitoringService().add_rule(actor, pattern.value, match.value)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            push_to_open_tabs()
            ui.notify('Ignored in every open tab from now on.', color='positive')
            dialog.close()
            await _refresh(table)

        with dialog_actions().classes('justify-end'):
            ui.button('Cancel', on_click=dialog.close).props('flat')
            ui.button('Ignore it', on_click=submit, color='primary')
    dialog.open()


def _confirm_remove(actor, table, row) -> None:
    async def do_remove() -> None:
        try:
            await BrowserMonitoringService().remove_rule(actor, int(row['id']))
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        push_to_open_tabs()
        ui.notify('Rule removed. Matching errors reach Sentry again.', color='positive')
        await _refresh(table)

    ConfirmationDialog(
        message=f"Stop ignoring browser errors containing “{row['pattern']}”? They'll reach Sentry again.",
        on_confirm=do_remove, confirm_text='Remove', title='Remove ignore rule?',
    ).open()
