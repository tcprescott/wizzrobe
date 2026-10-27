"""Admin Brackets page — native tournament brackets management (STAFF).

Staff-facing surface over :class:`~application.services.bracket_service.BracketService`:
pick a tournament, author its bracket stages (create/edit/delete while DRAFT),
manage the tournament roster and per-stage enrollment/seeding, start a stage,
record results (with corrections), complete or cancel a stage, and advance the
field into the next.

This module owns the layout — the tournament selector, the stage table and its
row-action dispatch; the dialogs live beside it in this package. Each row carries
its own readiness ("Seed & start (0/16 enrolled)", "7/15 reported") and one
primary next step, with the rest of the actions in an overflow menu, so the table
answers *what do I do next* rather than offering four verbs and letting the
service explain which of them was legal.

Presentation-only: renders NiceGUI, calls the service for every write, and shows
service ``ValueError`` / ``PermissionError`` as toasts. Read-only ``Tournament``
lookups for the selector are the sanctioned display query. All scoped reads and
service calls run inside ``tenant_scope`` because row-action handlers and the
tournament selector fire from detached client events that have lost the tenant
contextvar.
"""

from typing import Dict, List, Optional

from nicegui import app, background_tasks, context, ui

from application.event_context import event_scope, event_scoped, get_current_event_id
from application.services import BracketService
from application.tenant_context import require_tenant_id, tenant_scope
from models import BracketMatchState, BracketState, Tournament
from theme.assets import asset_url
from theme.brackets import (
    config_summary,
    format_label,
    stage_label,
    state_color,
    state_label,
)
from theme.busy import busy_click
from theme.dialog._helpers import dialog_actions, form_dialog
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.links import app_href
from theme.notify import notify_error
from theme.page_header import page_header
from theme.tables.admin_crud import current_actor, refresh_button, wire_tab_refresh
from theme.tables.mobile_grid import enable_mobile_grid
from theme.tables.preferences import TableKeys

from .manage import open_manage
from .results import open_results
from .shared import ELIM_FORMATS, entry_name_map
from .stage_form import confirm_cancel_stage, confirm_delete_stage, open_stage_form

_TOURNAMENT_KEY = 'admin_brackets_tournament_id'

# One primary button per row — the step that stage is actually up to — plus an
# overflow menu for everything else that is legal on it. Every entry is `v-if`ed
# on the row's own state: the service guards each transition anyway, so an
# ungated button could only ever produce a toast explaining why it was never
# going to work.
_ROW_ACTIONS = '''
    <q-btn v-if="props.row.primary" flat dense no-caps color="primary"
           :icon="props.row.primary_icon"
           :label="props.row.primary"
           @click="$parent.$emit(props.row.primary_event, props.row)">
    </q-btn>
    <q-btn flat round dense icon="more_vert" color="grey-7" :aria-label="'More for ' + props.row.name">
        <q-menu auto-close>
            <q-list style="min-width: 190px">
                <q-item v-if="props.row.state !== 'complete' && props.row.state !== 'cancelled'"
                        clickable @click="$parent.$emit('manage', props.row)">
                    <q-item-section avatar><q-icon name="tune"/></q-item-section>
                    <q-item-section>Entrants &amp; seeding</q-item-section>
                </q-item>
                <q-item v-if="props.row.state !== 'draft'" clickable
                        @click="$parent.$emit('results', props.row)">
                    <q-item-section avatar><q-icon name="scoreboard"/></q-item-section>
                    <q-item-section>Results</q-item-section>
                </q-item>
                <q-item v-if="props.row.can_complete" clickable
                        @click="$parent.$emit('complete', props.row)">
                    <q-item-section avatar><q-icon name="flag"/></q-item-section>
                    <q-item-section>Complete stage</q-item-section>
                </q-item>
                <q-item v-if="props.row.can_advance" clickable
                        @click="$parent.$emit('advance', props.row)">
                    <q-item-section avatar><q-icon name="fast_forward"/></q-item-section>
                    <q-item-section>Advance into next stage</q-item-section>
                </q-item>
                <q-item v-if="props.row.state !== 'draft'" clickable tag="a"
                        :href="props.row.public_href" target="_blank" rel="noopener">
                    <q-item-section avatar><q-icon name="open_in_new"/></q-item-section>
                    <q-item-section>Open public bracket</q-item-section>
                </q-item>
                <q-item v-if="props.row.state === 'draft'" clickable
                        @click="$parent.$emit('edit', props.row)">
                    <q-item-section avatar><q-icon name="edit"/></q-item-section>
                    <q-item-section>Edit stage</q-item-section>
                </q-item>
                <q-separator v-if="props.row.state !== 'complete'"/>
                <q-item v-if="props.row.state === 'active'" clickable class="text-negative"
                        @click="$parent.$emit('cancel', props.row)">
                    <q-item-section avatar><q-icon name="block"/></q-item-section>
                    <q-item-section>Cancel stage</q-item-section>
                </q-item>
                <q-item v-if="props.row.state === 'draft' || props.row.state === 'cancelled'"
                        clickable class="text-negative" @click="$parent.$emit('remove', props.row)">
                    <q-item-section avatar><q-icon name="delete"/></q-item-section>
                    <q-item-section>Delete stage</q-item-section>
                </q-item>
            </q-list>
        </q-menu>
    </q-btn>
'''

_STATE_CELL = '''
    <q-td :props="props">
        <q-badge :color="props.row.state_color">{{ props.row.state_label }}</q-badge>
        <div class="text-caption text-grey">{{ props.row.readiness }}</div>
    </q-td>
'''


def _next_step(bracket, entry_count: int, matches) -> tuple:
    """The one thing this stage is up to, as ``(label, icon, event)``.

    A stage is only ever at one step, and which one is derivable from what it
    already has: a DRAFT with nobody in it needs entrants, a DRAFT with a field
    needs starting, an ACTIVE stage needs results, and a COMPLETE stage with a
    successor needs advancing.
    """
    if bracket.state == BracketState.DRAFT:
        if entry_count < 2:
            return 'Add entrants', 'group_add', 'manage'
        return 'Seed & start', 'play_arrow', 'manage'
    if bracket.state == BracketState.ACTIVE:
        open_now = sum(1 for m in matches if m.state == BracketMatchState.OPEN)
        if open_now:
            return 'Report results', 'scoreboard', 'results'
        if bracket.format in ELIM_FORMATS:
            return 'Results', 'scoreboard', 'results'
        return 'Complete stage', 'flag', 'complete'
    return '', '', ''


def _readiness(bracket, entry_count: int, matches, champion: Optional[str]) -> str:
    """The counts behind the next step, so the row states its own progress."""
    if bracket.state == BracketState.DRAFT:
        return f'{entry_count} enrolled'
    if bracket.state == BracketState.CANCELLED:
        return 'Abandoned — results kept, nobody ranked'
    played = sum(1 for m in matches if m.state == BracketMatchState.COMPLETE)
    total = len(matches)
    if bracket.state == BracketState.ACTIVE:
        return f'{played}/{total} matches reported'
    return f'Won by {champion}' if champion else f'{total} matches played'


async def admin_brackets_page() -> None:
    service = BracketService()
    # Captured while the request context is live; rebound around every detached
    # client-event handler (see module docstring).
    tenant_id = require_tenant_id()
    event_id = get_current_event_id()
    state: Dict[str, Optional[int]] = {'tournament_id': None}
    # The Results dialog embeds the shared bracket renderer for click-to-report.
    ui.add_head_html(f'<link rel="stylesheet" href="{asset_url("css/brackets.css")}">')

    with ui.column().classes('page-container'):
        page_header(
            'Brackets',
            'Native tournament brackets: author a stage, build its field, '
            'preview the draw, then start it. Each row shows the one step it '
            'is up to.',
        )

        # Challonge-linked tournaments are excluded, not just sorted down: a
        # tournament uses a native bracket or a Challonge link and never both, so
        # every one of them could only ever answer "Create bracket" with an error.
        # Inactive ones stay — their existing stages must remain reachable — but
        # sort last and say so.
        tournaments = await event_scoped(Tournament.filter(
            challonge_tournament_id__isnull=True,
        )).order_by('-is_active', 'name')
        options = {
            t.id: t.name if t.is_active else f'{t.name} (inactive)'
            for t in tournaments
        }

        columns = [
            {'name': 'stage', 'label': 'Stage', 'field': 'stage', 'sortable': True},
            {'name': 'name', 'label': 'Name', 'field': 'name', 'sortable': True},
            {'name': 'format', 'label': 'Format', 'field': 'format'},
            {'name': 'state', 'label': 'State', 'field': 'state_label'},
            {'name': 'config', 'label': 'Setup', 'field': 'config'},
            {'name': 'actions', 'label': '', 'field': 'actions'},
        ]

        page_container = ui.column().classes('w-full')

        # Resolved while the page builds: the refresh runs as a background task.
        public_base = app_href('/brackets/')

        # --- brackets table refresh --------------------------------------
        async def refresh_table() -> None:
            tid = state['tournament_id']
            rows: List[dict] = []
            with tenant_scope(tenant_id), event_scope(event_id):
                if tid is not None:
                    brackets = await service.list_brackets(tid)
                    loaded = {
                        b.stage_order: (b, await service.list_entries(b.id))
                        for b in brackets
                    }

                    def can_advance_from(bracket) -> bool:
                        """Every guard the advance itself enforces, checked here.

                        Offering the action and letting the service explain why it
                        was never going to work is what the row gating exists to
                        stop — and a successor that is already running or already
                        seeded is the common case on a chain mid-event.
                        """
                        if bracket.state != BracketState.COMPLETE:
                            return False
                        successor = loaded.get(bracket.stage_order + 1)
                        if successor is None:
                            return False
                        next_stage, next_entries = successor
                        return (
                            next_stage.state == BracketState.DRAFT
                            and not next_entries
                            and bool((next_stage.config or {}).get('advancement'))
                        )

                    for bracket in brackets:
                        entries = loaded[bracket.stage_order][1]
                        matches = await service.list_matches(bracket.id)
                        champion = None
                        if bracket.state == BracketState.COMPLETE:
                            winner = next(
                                (e for e in entries if e.final_rank == 1), None,
                            )
                            if winner is not None:
                                names = await entry_name_map(service, bracket.id, tid)
                                champion = names.get(winner.id)
                        label, icon, event = _next_step(bracket, len(entries), matches)
                        rows.append({
                            'id': bracket.id,
                            'public_href': f'{public_base}{bracket.id}',
                            'stage': stage_label(bracket.stage_order),
                            'stage_order': bracket.stage_order,
                            'name': bracket.name,
                            'format': format_label(bracket.format),
                            'state': bracket.state.value,
                            'state_label': state_label(bracket.state),
                            'state_color': state_color(bracket.state),
                            'config': ' · '.join(config_summary(bracket)) or '—',
                            'readiness': _readiness(
                                bracket, len(entries), matches, champion,
                            ),
                            'primary': label,
                            'primary_icon': icon,
                            'primary_event': event,
                            'can_complete': (
                                bracket.state == BracketState.ACTIVE
                                and bracket.format not in ELIM_FORMATS
                            ),
                            'can_advance': can_advance_from(bracket),
                        })
            table.rows = rows
            table.update()
            # An empty table is replaced outright rather than left to Quasar's
            # "No data available", which is the same sentence for "you have not
            # chosen a tournament yet" and "this one has no stages" — the two
            # states a first-time organizer most needs told apart.
            table.set_visibility(bool(rows))
            empty_hint.set_visibility(not rows)
            empty_hint.set_text(
                'Pick a tournament above to see its bracket stages.' if tid is None
                else 'No stages yet. "Create stage" builds the first one — a '
                     'single-elimination stage 0 is the usual starting point.'
            )

        def on_tournament_change(e) -> None:
            state['tournament_id'] = e.value
            # Remembered per user: staff work one tournament for weeks, and the
            # page opened on a blank selector every single time.
            app.storage.user[_TOURNAMENT_KEY] = e.value
            background_tasks.create(refresh_table())

        def next_stage_order() -> int:
            """The stage order a new stage should default to."""
            if not table.rows:
                return 0
            return max(r['stage_order'] for r in table.rows) + 1

        # --- complete stage ----------------------------------------------
        async def complete_stage(row, client) -> None:
            """Confirm, then finalize the stage.

            The tie-resolving path is in the Results dialog, next to the
            standings it rules on; this is the quick one for a stage with no
            ties to settle.
            """
            with client:
                async def do_complete() -> None:
                    actor = await current_actor()
                    with tenant_scope(tenant_id), event_scope(event_id):
                        try:
                            await service.complete_stage(actor, row['id'])
                        except (ValueError, PermissionError) as ex:
                            notify_error(ex)
                            return
                    ui.notify('Stage completed', color='positive')
                    await refresh_table()

                confirm = ConfirmationDialog(
                    f"Complete “{row['name']}”? This writes every entrant's final "
                    'rank from the current standings and locks the stage — it '
                    'cannot be undone. To rule on a tie first, use Results → '
                    'Standings.',
                    on_confirm=do_complete,
                    confirm_text='Complete stage', busy_text='Completing…',
                )
                confirm.open()

        # --- advance stage (plan + confirm) ------------------------------
        async def open_advance(row, client) -> None:
            tid = state['tournament_id']
            from_stage_order = row['stage_order']
            with client:
                actor = await current_actor()
                with tenant_scope(tenant_id), event_scope(event_id):
                    try:
                        plan = await service.advancement_plan(tid, from_stage_order)
                        names = await entry_name_map(service, row['id'], tid)
                    except (ValueError, PermissionError) as ex:
                        notify_error(ex)
                        return

                title = (
                    f'{stage_label(from_stage_order)} → '
                    f'{stage_label(plan.destination.stage_order)}'
                )
                with form_dialog(f'Advance — {title}', card_classes='dialog-card dialog-card--sm') as dialog:
                    if plan.already_seeded:
                        ui.label(
                            f'“{plan.destination.name}” has already been seeded from '
                            'this stage. Nothing further to do here — un-enroll its '
                            'entries from Manage if you need to redraw it.'
                        ).classes('text-caption text-warning')
                    else:
                        rule = plan.advancement
                        scope = 'per group' if rule.per_group else 'overall'
                        ui.label(
                            f'Top {rule.count} {scope} carry into '
                            f'“{plan.destination.name}”'
                            + (f' ({rule.seeding} seeding)' if rule.per_group else '')
                        ).classes('text-caption text-grey')
                        for entry, seed, group in plan.rows:
                            origin = (
                                f'was #{entry.final_rank}'
                                + (f', Group {group}' if group is not None else '')
                            )
                            ui.label(
                                f'Seed {seed} — {names.get(entry.id, f"Entry {entry.id}")}'
                                f'  ({origin})'
                            ).classes('ellipsis w-full')

                    async def do_advance() -> None:
                        with tenant_scope(tenant_id), event_scope(event_id):
                            try:
                                await service.advance_stage(actor, tid, from_stage_order)
                            except (ValueError, PermissionError) as ex:
                                notify_error(ex)
                                # Terminal: the guard that refused will refuse the
                                # same way on a second click, so the dialog closes
                                # instead of staying open and clickable.
                                dialog.close()
                                return
                        ui.notify('Field advanced into next stage', color='positive')
                        dialog.close()
                        await refresh_table()

                    with dialog_actions().classes('justify-end'):
                        ui.button('Cancel', on_click=dialog.close).props('flat')
                        if not plan.already_seeded:
                            ui.button(
                                'Advance', icon='fast_forward',
                                on_click=busy_click('Advancing…', do_advance),
                            ).props('color=primary')
                dialog.open()

        # --- dialog dispatch ---------------------------------------------
        def _dialog_kwargs() -> dict:
            return {
                'service': service,
                'tenant_id': tenant_id,
                'tournament_id': state['tournament_id'],
                'on_change': refresh_table,
            }

        async def create_stage(client) -> None:
            await open_stage_form(
                client, default_stage_order=next_stage_order(), **_dialog_kwargs(),
            )

        async def edit_stage(row, client) -> None:
            await open_stage_form(client, row=row, **_dialog_kwargs())

        # --- layout ------------------------------------------------------
        with page_container:

            # Resolved before the widget exists: assigning `.value` afterwards
            # loads the table but leaves a `with_input` select displaying nothing,
            # so the page looks unselected while showing a tournament's stages.
            remembered = app.storage.user.get(_TOURNAMENT_KEY)
            # One choice is not a choice: pick it rather than making the first
            # thing anyone does on this page be confirming the obvious.
            initial = (
                remembered if remembered in options
                else (next(iter(options)) if len(options) == 1 else None)
            )
            state['tournament_id'] = initial

            # Picker, Create stage and refresh on one toolbar row: stacked, the
            # refresh button sat alone 1100px from the button it belongs with.
            toolbar = ui.row().classes('wiz-toolbar full-width items-center')
            with toolbar:
                selector = ui.select(
                    options, label='Tournament', with_input=True, value=initial,
                    on_change=on_tournament_change,
                ).classes('wiz-toolbar__grow').props('outlined dense')
            selector.add_slot('no-option', '''
                <q-item><q-item-section class="text-grey">
                    No tournament matches
                </q-item-section></q-item>
            ''')
            if not options:
                ui.label(
                    'No tournament is eligible for a native bracket — every one in '
                    'this community is linked to a Challonge bracket, which a '
                    'native bracket cannot coexist with. Unlink one from its ⋮ '
                    'on Tournaments to move it across.'
                ).classes('text-caption text-warning')

            with toolbar:
                ui.button(
                    'Create stage', icon='add',
                    on_click=lambda: background_tasks.create(create_stage(context.client))
                    if state['tournament_id'] else
                    ui.notify('Select a tournament first', color='warning'),
                ).props('color=primary')
                ui.space()
                refresh_button(refresh_table)

            table = ui.table(columns=columns, rows=[], row_key='id').classes('w-full wiz-table')
            table.add_slot('body-cell-actions', f'<q-td :props="props">{_ROW_ACTIONS}</q-td>')
            table.add_slot('body-cell-state', _STATE_CELL)
            enable_mobile_grid(
                table, columns, actions=_ROW_ACTIONS,
                table_key=TableKeys.ADMIN_BRACKETS,
                field_slots={
                    'state': (
                        '<q-badge :color="props.row.state_color">'
                        '{{ props.row.state_label }}</q-badge>'
                        '<div class="text-caption text-grey">{{ props.row.readiness }}</div>'
                    ),
                },
            )
            empty_hint = ui.label('').classes('wiz-empty-panel')
            empty_hint.set_visibility(False)

            table.on('manage', lambda e: background_tasks.create(
                open_manage(e.args, context.client, **_dialog_kwargs())))
            table.on('results', lambda e: background_tasks.create(
                open_results(e.args, context.client, **_dialog_kwargs())))
            table.on('complete', lambda e: background_tasks.create(
                complete_stage(e.args, context.client)))
            table.on('advance', lambda e: background_tasks.create(
                open_advance(e.args, context.client)))
            table.on('edit', lambda e: background_tasks.create(
                edit_stage(e.args, context.client)))
            table.on('cancel', lambda e: background_tasks.create(confirm_cancel_stage(
                e.args, context.client, service=service, tenant_id=tenant_id,
                on_change=refresh_table)))
            table.on('remove', lambda e: background_tasks.create(confirm_delete_stage(
                e.args, context.client, service=service, tenant_id=tenant_id,
                on_change=refresh_table)))

        wire_tab_refresh('Brackets', refresh_table)
        background_tasks.create(refresh_table())
