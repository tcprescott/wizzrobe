"""Per-round chrome: the series length a round is played at, and when it runs.

Semantic, not cosmetic — ``best_of`` is what the scheduler opens game slots for
and what the clinch logic counts to. Editable in any state, because raising a
round's best-of after a stage has started opens the extra slots live.
"""

from typing import Dict, List

from nicegui import ui

from application.tenant_context import tenant_scope
from application.utils.timezone import timezone_label
from models import BracketMatch
from theme.dialog._helpers import (
    dialog_actions,  # noqa: F401  (kept for parity)
    native_datetime_input,
)
from theme.notify import notify_error

from .shared import iso_to_local_input, local_input_to_iso, round_display_names


def render_round_editor(
    matches: List[BracketMatch],
    bracket,
    *,
    service,
    actor,
    tenant_id,
    on_saved,
) -> None:
    """The best-of / scheduled-time editor for every round that exists."""
    names = round_display_names(matches, bracket.format)
    if not names:
        return
    rounds_cfg = (bracket.config or {}).get('rounds') or {}
    default_best_of = (bracket.config or {}).get('default_best_of')
    widgets: Dict[int, tuple] = {}

    with ui.expansion('Round settings — best-of & scheduled window').classes('w-full q-mt-sm'):
        ui.label(
            'Best-of is the series length the round is actually played at: it '
            'decides how many games get scheduled and how a matchup clinches, '
            f'not just what the header says. Odd numbers only; times are '
            f'{timezone_label()}.'
        ).classes('text-caption text-grey')
        ui.label(
            'Opens/Closes bound when the round runs: players scheduling a '
            'matchup in it are only suggested times that fit wholly inside the '
            'window. Leave either blank for an open-ended side.'
        ).classes('text-caption text-grey')
        if default_best_of:
            ui.label(
                f'Rounds left blank are played best of {default_best_of} '
                "(this stage's default)."
            ).classes('text-caption text-grey')
        for round_number, label in names.items():
            cfg = rounds_cfg.get(str(round_number)) or {}
            with ui.row().classes('items-center gap-2 w-full'):
                ui.label(label).classes('col-12 col-sm-3 text-bold')
                best_of = ui.number(
                    'Best of', value=cfg.get('best_of'), min=1, precision=0,
                ).props('dense inputmode=numeric').classes('col-4 col-sm-3')
                scheduled = native_datetime_input(
                    'Opens', iso_to_local_input(cfg.get('scheduled_at')), dense=True,
                ).classes('col')
                scheduled_end = native_datetime_input(
                    'Closes', iso_to_local_input(cfg.get('scheduled_end')), dense=True,
                ).classes('col')
                widgets[round_number] = (best_of, scheduled, scheduled_end)

        async def save_rounds() -> None:
            new_rounds: Dict[str, dict] = {}
            for round_number, (best_of, scheduled, scheduled_end) in widgets.items():
                entry: Dict[str, object] = {}
                if best_of.value:
                    entry['best_of'] = int(best_of.value)
                iso = local_input_to_iso(scheduled.value)
                if iso:
                    entry['scheduled_at'] = iso
                end_iso = local_input_to_iso(scheduled_end.value)
                if end_iso:
                    entry['scheduled_end'] = end_iso
                if entry:
                    new_rounds[str(round_number)] = entry
            with tenant_scope(tenant_id):
                try:
                    await service.set_round_metadata(actor, bracket.id, new_rounds or None)
                except (ValueError, PermissionError) as ex:
                    notify_error(ex)
                    return
            ui.notify('Round settings saved', color='positive')
            await on_saved()

        ui.button(
            'Save round settings', icon='save', on_click=save_rounds,
        ).props('flat color=primary')
