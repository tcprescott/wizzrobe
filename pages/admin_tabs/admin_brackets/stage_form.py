"""Create / edit / delete one bracket stage.

One form serves both create and edit: a DRAFT stage has no match graph, so every
field on it — including the format — is still safe to change, and the same dialog
that authored it can correct it.

The options panel is bound to the format select, so a stage is asked only the
questions its own engine will answer. Every knob the engines actually read has a
control here, because config is DRAFT-only: whatever is not decided in this
dialog is decided by the defaults, permanently.
"""

from typing import Dict, List

from nicegui import ui

from application.services.bracket_engines.standings import KNOWN_TIEBREAKERS
from application.tenant_context import tenant_scope
from models import BracketFormat
from theme.brackets import FORMAT_OPTIONS, stage_label
from theme.dialog._helpers import dialog_actions, form_dialog, submit_on_enter
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.notify import notify_error
from theme.pickers import CHIP_PROPS
from theme.tables.admin_crud import current_actor

# Only these keys are replaced; any other config (the manage dialog's per-round metadata) must pass through.
_FORM_CONFIG_KEYS = (
    'swiss_rounds', 'group_count', 'advancement', 'grand_final_reset',
    'default_best_of', 'tiebreakers', 'win_points', 'draw_points', 'loss_points',
    'bye_points',
)

_TIEBREAKER_LABELS = {
    'buchholz': "Buchholz — sum of opponents' points",
    'omw': 'Opponent match-win %',
    'head_to_head': 'Head-to-head result',
}

_POINTS_FIELDS = (
    ('win_points', 'Win', 1.0),
    ('draw_points', 'Draw', 0.5),
    ('loss_points', 'Loss', 0.0),
    ('bye_points', 'Bye', 1.0),
)

_POINTS_FORMATS = (BracketFormat.SWISS.value, BracketFormat.ROUND_ROBIN.value)


async def open_stage_form(
    client,
    *,
    service,
    tenant_id,
    tournament_id,
    row=None,
    default_stage_order: int = 0,
    on_change,
) -> None:
    """Open the create dialog, or the edit dialog when ``row`` is given."""
    is_edit = row is not None
    with client:
        bracket = None
        if is_edit:
            with tenant_scope(tenant_id):
                bracket = await service.get_bracket(row['id'])
            if bracket is None:
                notify_error(ValueError('Bracket not found.'))
                return
        config = dict(bracket.config or {}) if bracket else {}
        advancement = config.get('advancement') or {}

        title = f'Edit stage — {bracket.name}' if is_edit else 'Create bracket stage'
        with form_dialog(title) as dialog:
            name_in = ui.input(
                'Name', value=bracket.name if bracket else '',
                placeholder='Group Stage, Playoff, Round 1…',
            ).props('autofocus').classes('w-full')
            fmt_in = ui.select(
                FORMAT_OPTIONS,
                value=bracket.format.value if bracket else BracketFormat.SINGLE_ELIM.value,
                label='Format',
            ).classes('w-full')
            stage_in = ui.number(
                'Stage order (0 = first stage)',
                value=bracket.stage_order if bracket else default_stage_order,
                min=0, precision=0,
            ).props('inputmode=numeric').classes('w-full')
            stage_hint = ui.label('').classes('text-caption text-grey')

            fields: Dict[str, object] = {}
            # Carried across rebuilds, or a format change collapses the panels being filled in.
            panels: Dict[str, bool] = {
                'options': True, 'scoring': False, 'advancement': bool(advancement),
            }

            def keep_open(key: str):
                return lambda e: panels.__setitem__(key, bool(e.value))

            def stage_order_value() -> int:
                try:
                    return int(stage_in.value or 0)
                except (TypeError, ValueError):
                    return 0

            @ui.refreshable
            def options_panel() -> None:
                """Only the knobs this format's engine reads.

                Rebuilt on every format change: the union of all formats' fields
                is what made a "(optional)" Swiss-rounds box look like a decision
                on a single-elimination stage, and the service now refuses to
                store one anyway.
                """
                fields.clear()
                fmt = fmt_in.value
                order = stage_order_value()

                options = ui.expansion(
                    'Format & scoring options', value=panels['options'],
                ).classes('w-full')
                options.on_value_change(keep_open('options'))
                with options:
                    fields['default_best_of'] = ui.number(
                        'Default best-of (series length for every round)',
                        value=config.get('default_best_of'), min=1, precision=0,
                    ).props('inputmode=numeric').classes('w-full')
                    ui.label(
                        'Semantic, not a label: it is how many games a matchup is '
                        'played over. Odd numbers only. A round can override it '
                        'later from Manage → Round settings.'
                    ).classes('text-caption text-grey')

                    if fmt == BracketFormat.SWISS.value:
                        fields['swiss_rounds'] = ui.number(
                            'Swiss rounds', value=config.get('swiss_rounds'),
                            min=1, precision=0,
                        ).props('inputmode=numeric').classes('w-full')
                        ui.label(
                            'Left blank, the round count is derived from the field '
                            'size on every round — so a stage announced as 5 rounds '
                            'ends after 4 if enough players drop.'
                        ).classes('text-caption text-grey')
                    elif fmt == BracketFormat.ROUND_ROBIN.value:
                        fields['group_count'] = ui.number(
                            'Groups', value=config.get('group_count'), min=1, precision=0,
                        ).props('inputmode=numeric').classes('w-full')
                        ui.label(
                            'One pool unless set. The field is split evenly and '
                            'every entrant plays everyone in their own group.'
                        ).classes('text-caption text-grey')
                    elif fmt == BracketFormat.DOUBLE_ELIM.value:
                        fields['grand_final_reset'] = ui.switch(
                            'Grand final reset',
                            value=bool(config.get('grand_final_reset', True)),
                        )
                        ui.label(
                            'On: the losers-bracket finalist must win twice — '
                            'taking the first grand final forces a deciding rematch.'
                        ).classes('text-caption text-grey')

                    if fmt in _POINTS_FORMATS:
                        scoring = ui.expansion(
                            'Scoring & tiebreakers', value=panels['scoring'],
                        ).classes('w-full')
                        scoring.on_value_change(keep_open('scoring'))
                        with scoring:
                            ui.label(
                                'What ranks this stage — and therefore who advances '
                                'out of it.'
                            ).classes('text-caption text-grey')
                            fields['tiebreakers'] = ui.select(
                                _TIEBREAKER_LABELS,
                                value=list(config.get('tiebreakers') or KNOWN_TIEBREAKERS),
                                label='Tiebreakers, in order', multiple=True,
                            ).props(CHIP_PROPS).classes('w-full')
                            with ui.row().classes('items-end gap-2 w-full'):
                                for key, label, fallback in _POINTS_FIELDS:
                                    fields[key] = ui.number(
                                        label, value=config.get(key, fallback),
                                        min=0, step=0.5,
                                    ).props('dense').classes('col')

                    if order > 0:
                        adv_panel = ui.expansion(
                            'Advancement — the field this stage draws in',
                            value=panels['advancement'],
                        ).classes('w-full')
                        adv_panel.on_value_change(keep_open('advancement'))
                        with adv_panel:
                            ui.label(
                                'Set on THIS stage, describing what it takes from '
                                f'{stage_label(order - 1)}. Leave blank to enroll '
                                'this stage by hand instead.'
                            ).classes('text-caption text-grey')
                            fields['adv_count'] = ui.number(
                                'Entrants to draw in', value=advancement.get('count'),
                                min=1, precision=0,
                            ).props('inputmode=numeric').classes('w-full')
                            fields['adv_per_group'] = ui.switch(
                                'That many per group (rather than overall)',
                                value=bool(advancement.get('per_group', False)),
                            )
                            fields['adv_seeding'] = ui.select(
                                {
                                    'snake': 'Snake — keep group-mates apart',
                                    'preserve': 'Preserve — seed in rank order',
                                },
                                value=advancement.get('seeding', 'snake'),
                                label='Seeding',
                            ).classes('w-full')
                            ui.label(
                                'Seeding only applies when drawing per group.'
                            ).classes('text-caption text-grey')
                    else:
                        ui.label(
                            'The first stage has nothing behind it to draw from, so '
                            'it carries no advancement rule — set one on the stage '
                            'that follows this one.'
                        ).classes('text-caption text-grey')

            def sync_stage_hint() -> None:
                order = stage_order_value()
                stage_hint.set_text(
                    f'Shown everywhere as {stage_label(order)}'
                    + ('' if order else ' — the first stage')
                )

            fmt_in.on_value_change(lambda _: options_panel.refresh())
            stage_in.on_value_change(
                lambda _: (sync_stage_hint(), options_panel.refresh())
            )
            sync_stage_hint()
            options_panel()

            def _number(key):
                widget = fields.get(key)
                return widget.value if widget is not None else None

            async def submit() -> None:
                new_config: Dict[str, object] = {
                    k: v for k, v in config.items() if k not in _FORM_CONFIG_KEYS
                }
                order = stage_order_value()
                fmt = fmt_in.value

                if _number('default_best_of'):
                    new_config['default_best_of'] = int(_number('default_best_of'))
                if fmt == BracketFormat.SWISS.value and _number('swiss_rounds'):
                    new_config['swiss_rounds'] = int(_number('swiss_rounds'))
                if fmt == BracketFormat.ROUND_ROBIN.value and _number('group_count'):
                    new_config['group_count'] = int(_number('group_count'))
                if fmt == BracketFormat.DOUBLE_ELIM.value and 'grand_final_reset' in fields:
                    new_config['grand_final_reset'] = bool(
                        fields['grand_final_reset'].value
                    )
                if fmt in _POINTS_FORMATS and 'tiebreakers' in fields:
                    chosen: List[str] = list(fields['tiebreakers'].value or [])
                    if chosen:
                        new_config['tiebreakers'] = chosen
                    for key, _label, _fallback in _POINTS_FIELDS:
                        value = _number(key)
                        if value is not None:
                            new_config[key] = float(value)
                if order > 0 and _number('adv_count'):
                    new_config['advancement'] = {
                        'count': int(_number('adv_count')),
                        'per_group': bool(fields['adv_per_group'].value),
                        'seeding': fields['adv_seeding'].value,
                    }

                with tenant_scope(tenant_id):
                    actor = await current_actor()
                    try:
                        if is_edit:
                            await service.update_bracket(
                                actor, bracket.id,
                                name=name_in.value or '',
                                stage_order=order,
                                # A dict, never None: None means "leave the blob alone", so a rule could never be cleared.
                                config=new_config,
                                format=fmt,
                            )
                        else:
                            await service.create_bracket(
                                actor, tournament_id, name_in.value or '', fmt,
                                order, new_config or None,
                            )
                    except (ValueError, PermissionError) as ex:
                        notify_error(ex)
                        return
                ui.notify('Stage saved' if is_edit else 'Stage created', color='positive')
                dialog.close()
                await on_change()

            with dialog_actions().classes('justify-end'):
                ui.button('Cancel', on_click=dialog.close).props('flat')
                ui.button(
                    'Save' if is_edit else 'Create',
                    icon='save' if is_edit else 'add', on_click=submit,
                ).props('color=primary')
            submit_on_enter(dialog, submit)
        dialog.open()


async def confirm_delete_stage(row, client, *, service, tenant_id, on_change) -> None:
    """Delete a DRAFT or CANCELLED stage, after asking."""
    with client:
        async def do_delete() -> None:
            actor = await current_actor()
            with tenant_scope(tenant_id):
                try:
                    await service.delete_bracket(actor, row['id'])
                except (ValueError, PermissionError) as ex:
                    notify_error(ex)
                    return
            ui.notify('Stage deleted', color='positive')
            await on_change()

        confirm = ConfirmationDialog(
            f"Delete “{row['name']}”? Its enrollments and seeding go with it. The "
            'tournament roster is untouched — entrants belong to the tournament, '
            'not to this stage.',
            on_confirm=do_delete,
            confirm_text='Delete stage',
        )
        confirm.open()


async def confirm_cancel_stage(row, client, *, service, tenant_id, on_change) -> None:
    """Abandon a started stage."""
    with client:
        async def do_cancel() -> None:
            actor = await current_actor()
            with tenant_scope(tenant_id):
                try:
                    await service.cancel_stage(actor, row['id'])
                except (ValueError, PermissionError) as ex:
                    notify_error(ex)
                    return
            ui.notify('Stage cancelled', color='positive')
            await on_change()

        confirm = ConfirmationDialog(
            f"Cancel “{row['name']}”? The stage stops here: its played results stay "
            'as history, but nobody is ranked, no champion is recorded, and no '
            'later stage can draw a field out of it. It disappears from the public '
            'bracket views and can then be deleted outright.',
            on_confirm=do_cancel,
            confirm_text='Cancel stage',
        )
        confirm.open()
