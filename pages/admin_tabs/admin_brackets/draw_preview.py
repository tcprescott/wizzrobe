"""The draw a DRAFT stage would produce, rendered before it is committed.

Start is the draw, the publish and the notification at once, so without a
rehearsal the only way to find out what the current seeding produces is to
publish it. ``BracketService.preview_draw`` runs the real engine in memory and
hands back unsaved ``BracketMatch`` rows shaped exactly like the ones generation
would persist — so the ordinary renderer draws them, and what an organizer
approves here is what they get.
"""

from typing import Dict

from nicegui import ui

from models import BracketFormat
from theme.brackets import build_context, render_elimination, render_elimination_mobile
from theme.brackets.tables import render_pairings

_ELIM = (BracketFormat.SINGLE_ELIM, BracketFormat.DOUBLE_ELIM)


def render_draw_preview(preview, bracket, entry_name: Dict[int, str]) -> None:
    """Render one ``DrawPreview`` inside the manage dialog."""
    with ui.expansion('Preview the draw', icon='visibility', value=True) \
            .classes('w-full q-mt-sm'):
        if preview.error:
            ui.label(preview.error).classes('text-caption text-warning')
            return
        if not preview.matches:
            for line in preview.summary:
                ui.label(line).classes('text-caption text-grey')
            return

        ui.label(' · '.join(preview.summary)).classes('text-caption text-grey')
        ui.label(
            'Nothing here is saved. Starting the stage is what commits it.'
        ).classes('text-caption text-grey')

        if bracket.format in _ELIM:
            ctx = build_context(
                bracket.config, [], preview.matches, entry_name, on_card_click=None,
            )
            ctx.entry_seed.update({
                entry_id: seed for entry_id, seed in preview.seed_of.items()
            })
            double = bracket.format == BracketFormat.DOUBLE_ELIM
            with ui.element('div').classes('bracket-embed-scroll'):
                with ui.element('div').classes('bracket-2d w-full'):
                    render_elimination(preview.matches, ctx, double=double)
                with ui.element('div').classes('bracket-mobile-list w-full'):
                    render_elimination_mobile(preview.matches, ctx, double=double)
            return

        if bracket.format == BracketFormat.ROUND_ROBIN:
            by_group: Dict[object, list] = {}
            for m in preview.matches:
                by_group.setdefault(m.group_number, []).append(m)
            for group in sorted(by_group, key=lambda g: (g is None, g)):
                if group is not None:
                    ui.label(f'Group {group}').classes('section-title')
                render_pairings(by_group[group], entry_name, {})
            return

        ui.label('Round 1 pairings').classes('section-title')
        render_pairings(preview.matches, entry_name, {})
