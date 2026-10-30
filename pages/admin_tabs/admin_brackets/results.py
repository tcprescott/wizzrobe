"""Results dialog — report and correct match results for a started stage.

Elimination stages get the real bracket renderer embedded; points-ranked formats
get the standings table that decides their placement. Every reporting path — the
bracket card, the flat list, the standings — opens the same match dialog, so
scores and the forfeit flag are enterable and preserved everywhere rather than on
the one surface that happened to be wired for them.
"""

from typing import Dict, List, Optional

from nicegui import ui

from application.tenant_context import tenant_scope
from models import BracketFormat, BracketState
from theme.brackets import (
    assign_match_numbers,
    build_context,
    build_match_dialog,
    entry_records,
    match_nodes,
    render_elimination,
    render_elimination_mobile,
)
from theme.brackets.tables import render_standings
from theme.dialog._helpers import form_dialog
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.notify import notify_error
from theme.steady import steady_refreshable
from theme.tables.admin_crud import current_actor

from .shared import ELIM_FORMATS, entry_display_maps, match_label, round_display_names


class _StandingView:
    """Adapts a service ``StandingsRow`` to what ``render_standings`` reads.

    The renderer is fed by the pure engine's ``Standing`` (an opaque ``ref``);
    the service resolves that into a row carrying names and both drop levels.
    Rather than fork the renderer for the admin, the resolved row is presented
    back under the engine's field names.
    """

    __slots__ = (
        'byes',
        'draws',
        'losses',
        'points',
        'rank',
        'ref',
        'tiebreakers',
        'tied_with',
        'wins',
    )

    def __init__(self, row) -> None:
        self.ref = row.entry_id
        self.rank = row.rank
        self.points = row.points
        self.wins = row.wins
        self.draws = row.draws
        self.losses = row.losses
        self.byes = row.byes
        self.tiebreakers = row.tiebreakers
        self.tied_with = row.tied_with


async def open_results(row, client, *, service, tenant_id, tournament_id, on_change) -> None:
    """Open the results dialog for one stage. ``on_change`` refreshes the table."""
    bracket_id = row['id']
    tid = tournament_id
    with client:
        actor = await current_actor()
        with form_dialog(f"Results — {row['name']}", card_classes='dialog-card dialog-card--lg') as dialog:

            @steady_refreshable
            async def body() -> None:
                with tenant_scope(tenant_id):
                    bracket = await service.get_bracket(bracket_id)
                    matches = await service.list_matches(bracket_id)
                    entries = await service.list_entries(bracket_id)
                    names, avatars = await entry_display_maps(service, bracket_id, tid)
                    live_state = await service.matchup_live_state(matches)
                    standings = (
                        await service.standings(bracket_id)
                        if bracket is not None and bracket.format not in ELIM_FORMATS
                        else []
                    )
                if bracket is None:
                    ui.label('Bracket not found.').classes('text-error')
                    return

                round_names = round_display_names(matches, bracket.format)
                records = entry_records([e.id for e in entries], matches)
                entry_seed = {e.id: e.seed for e in entries}
                numbers = assign_match_numbers(match_nodes(matches))

                async def after_write() -> None:
                    await body.refresh()
                    await on_change()

                def open_match(match_id: int) -> None:
                    """The one reporting surface, opened from every list.

                    Routing the flat lists through this dialog is what gives
                    Swiss and round robin score and forfeit entry at all: the
                    dialog used to be gated on elimination, so the only override
                    path those two formats had was the one that silently nulled
                    both.
                    """
                    match = next((m for m in matches if m.id == match_id), None)
                    if match is None:
                        return
                    build_match_dialog(
                        match, names, entry_seed, records, numbers.get(match_id),
                        best_of=service.resolve_best_of(bracket, match),
                        is_staff=True, actor=actor, tenant_id=tenant_id,
                        service=service, on_saved=after_write,
                        live=live_state.get(match_id),
                    )

                embedded = bracket.format in ELIM_FORMATS and bool(matches)
                if embedded:
                    ctx = build_context(
                        bracket.config, entries, matches, names,
                        entry_avatar=avatars,
                        on_card_click=open_match, live_state=live_state,
                    )
                    ui.label('Bracket — click a match to report a result') \
                        .classes('section-title')
                    double = bracket.format == BracketFormat.DOUBLE_ELIM
                    with ui.element('div').classes('bracket-embed-scroll'):
                        with ui.element('div').classes('bracket-2d w-full'):
                            render_elimination(matches, ctx, double=double)
                        with ui.element('div').classes('bracket-mobile-list w-full'):
                            render_elimination_mobile(matches, ctx, double=double)
                    ui.separator()

                if standings:
                    _render_standings(
                        bracket, standings,
                        service=service, actor=actor, tenant_id=tenant_id,
                        on_done=after_write,
                    )
                    ui.separator()

                _render_match_lists(matches, names, round_names, embedded, open_match)

            await body()
        dialog.open()


def _render_standings(bracket, standings, *, service, actor, tenant_id, on_done) -> None:
    """The points table that decides this stage's placement, and the tie ruling.

    ``complete_stage`` has always accepted ``tie_breaks`` so staff can rule before
    ``final_rank`` is written — and every caller in the product passed ``None``,
    against a table no admin surface displayed. A tie for the last advancing slot
    was unresolvable by any human using any surface of this product.
    """
    advancement = (bracket.config or {}).get('advancement') or {}
    cut = advancement.get('count')
    tiebreakers = (bracket.config or {}).get('tiebreakers') or (
        'buchholz', 'omw', 'head_to_head'
    )
    ui.label('Standings').classes('section-title')

    tied_rows: List = []
    for group in standings:
        if group.group_number is not None:
            ui.label(f'Group {group.group_number}').classes('text-caption text-grey')
        dropped = {
            r.entry_id for r in group.rows
            if r.status.value == 'dropped' or r.entrant_status.value == 'dropped'
        }
        render_standings(
            [_StandingView(r) for r in group.rows],
            {r.entry_id: r.display_name for r in group.rows},
            {r.entry_id: r.seed for r in group.rows},
            tiebreakers,
            advancing=cut,
            show_elim=bracket.state == BracketState.COMPLETE,
            dropped_ids=dropped,
        )
        tied_rows.extend(r for r in group.rows if r.tied_with)

    if cut:
        ui.label(
            f'The top {cut} advance into the next stage (green, above the line).'
        ).classes('text-caption text-grey')

    if bracket.state != BracketState.ACTIVE:
        return

    inputs: Dict[int, object] = {}
    if tied_rows:
        ui.label(
            f'{len(tied_rows)} entrant(s) are tied on every configured tiebreaker. '
            'Completing as-is lets the tie resolve itself; rule on it here first '
            'if it decides an advancing slot.'
        ).classes('text-caption text-warning')
        with ui.expansion('Resolve ties before completing').classes('w-full'):
            for r in tied_rows:
                with ui.row().classes('items-center gap-2 w-full no-wrap'):
                    ui.label(f'{r.display_name} — tied at #{r.rank}') \
                        .classes('col min-w-0 ellipsis')
                    inputs[r.entry_id] = ui.number(
                        'Final rank', value=r.final_rank or r.rank, min=1, precision=0,
                    ).props('dense inputmode=numeric').classes('w-28')

    async def do_complete() -> None:
        tie_breaks = {
            entry_id: int(widget.value)
            for entry_id, widget in inputs.items()
            if widget.value is not None
        }
        with tenant_scope(tenant_id):
            try:
                await service.complete_stage(actor, bracket.id, tie_breaks or None)
            except (ValueError, PermissionError) as ex:
                notify_error(ex)
                return
        ui.notify('Stage completed', color='positive')
        await on_done()

    confirm = ConfirmationDialog(
        f"Complete “{bracket.name}”? This writes every entrant's final rank from "
        'the standings above and locks the stage — it cannot be undone.',
        on_confirm=do_complete,
        confirm_text='Complete stage', busy_text='Completing…',
    )
    ui.button(
        'Complete stage from these standings', icon='flag', on_click=confirm.open,
    ).props('flat color=secondary')


def _render_match_lists(
    matches, names: Dict[int, str], round_names, embedded: bool, open_match,
) -> None:
    """Open and completed matches, both opening the shared match dialog."""
    def slot_label(entry_id: Optional[int]) -> str:
        if entry_id is None:
            return '—'
        return names.get(entry_id, f'Entry {entry_id}')

    open_matches = [m for m in matches if m.state.value == 'open']
    complete_matches = [
        m for m in matches
        if m.state.value == 'complete'
        and m.entry1_id is not None and m.entry2_id is not None
    ]

    def result_row(m) -> None:
        """One match as a single line that opens the shared dialog.

        The stacked winner buttons are gone: they were the score-wiping path,
        and the dialog replacing them reports the same result with the scores
        and the forfeit flag intact.
        """
        summary = (
            f'{match_label(m, round_names)}: '
            f'{slot_label(m.entry1_id)} vs {slot_label(m.entry2_id)}'
        )
        if m.winner_id is not None:
            summary += f' → {slot_label(m.winner_id)}'
        if m.entry1_score is not None and m.entry2_score is not None:
            summary += f' ({m.entry1_score}–{m.entry2_score})'
        if m.forfeit:
            summary += ' · forfeit'
        with ui.row().classes('items-center gap-2 w-full no-wrap q-py-xs'):
            ui.label(summary).classes('text-caption col min-w-0 ellipsis')
            ui.button(
                icon='edit_note', on_click=lambda _=None, mid=m.id: open_match(mid),
            ).props('flat round dense color=primary').tooltip('Report / correct')

    def open_list() -> None:
        if not open_matches:
            ui.label(
                'No open matches — every playable matchup is either finished or '
                'still waiting on a feeder result.'
            ).classes('text-muted')
        for m in open_matches:
            result_row(m)

    def complete_list() -> None:
        if not complete_matches:
            ui.label('Nothing reported yet.').classes('text-muted')
        for m in complete_matches:
            result_row(m)

    if embedded:
        with ui.expansion(f'Open matches ({len(open_matches)})').classes('w-full'):
            open_list()
        with ui.expansion(
            f'Completed matches — correct a result ({len(complete_matches)})',
        ).classes('w-full'):
            complete_list()
    else:
        ui.label('Open matches').classes('section-title')
        open_list()
        ui.separator()
        ui.label('Completed matches — correct a result').classes('section-title')
        complete_list()
