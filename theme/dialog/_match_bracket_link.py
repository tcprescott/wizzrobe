"""The admin match editor's 'Bracket matchup' picker.

Split out of ``match_dialog`` to keep it under the file-length guideline. Plain
functions taking the :class:`BracketService` explicitly rather than a mixin —
only :class:`AdminMatchDialog` uses them, and the seam is one select plus one
reconcile step.

This is the counterpart of booking a game from the bracket view: staff who
scheduled a match through the ordinary editor can attach it to the matchup it
actually settles, so its result advances the bracket.
"""

from typing import Optional

from nicegui import app, background_tasks, ui

from application.event_context import event_scope, get_current_event_id
from application.services import get_user_from_session
from application.tenant_context import require_tenant_id, tenant_scope


def matchup_label(bracket_match) -> str:
    """'Round 2 — Alice vs Bob', the way the bracket view names a matchup."""
    def name_of(entry):
        return entry.entrant.display_name if entry and entry.entrant else 'TBD'

    rnd = bracket_match.round
    base = f'Losers round {abs(rnd)}' if rnd < 0 else f'Round {rnd}'
    return (
        f'{base} — {name_of(bracket_match.entry1)} vs '
        f'{name_of(bracket_match.entry2)}'
    )


async def linked_matchup_id(
    bracket_service, match, *, brackets_live: bool
) -> Optional[int]:
    """The matchup this match is already linked to, or None.

    A no-op query-wise when brackets are off for the tenant or the dialog is
    creating a match — there is nothing to be linked to yet.
    """
    if not brackets_live or match is None:
        return None
    linked = await bracket_service.get_bracket_match_for_match(match.id)
    return linked.id if linked else None


async def render_matchup_panel(bracket_service, match_id: Optional[int]) -> None:
    """What this match is *for*: round, seeds, series standing, and stakes (U4).

    Rendered by :func:`render_select` above the picker, and only when the match
    already backs a bracket game — an unlinked match has nothing to say here, and
    the picker below is how it gets linked. Read-only; the link into the bracket
    view uses ``ui.navigate.to`` so path-mode multitenancy's ``root_path`` is
    applied.
    """
    if match_id is None:
        return
    summary = await bracket_service.matchup_summary(match_id)
    if summary is None:
        return

    with ui.card().classes('w-full q-pa-sm q-mb-sm'):
        with ui.row().classes('items-center justify-between w-full no-wrap'):
            with ui.column().classes('gap-0 min-w-0'):
                ui.label(
                    f"{summary['bracket_name']} · {summary['round_name']}"
                ).classes('text-bold ellipsis')
                detail = []
                if summary['best_of'] > 1:
                    detail.append(
                        f"Game {summary['game_number']} of {summary['best_of']}"
                    )
                if summary['standing']:
                    detail.append(f"Series {summary['standing']}")
                if summary['stakes']:
                    detail.append(summary['stakes'])
                if detail:
                    ui.label(' · '.join(detail)).classes('text-caption')
            ui.button(
                'Bracket', icon='account_tree',
                on_click=lambda bid=summary['bracket_id']: ui.navigate.to(
                    f'/brackets/{bid}'
                ),
            ).props('flat dense')
        with ui.row().classes('items-center gap-3 w-full'):
            for entrant in summary['entrants']:
                seed = f"#{entrant['seed']} " if entrant['seed'] is not None else ''
                ui.label(f"{seed}{entrant['name']}").classes('text-caption')


async def build_options(
    bracket_service,
    tournament_id: Optional[int],
    *,
    linked_id: Optional[int],
    match_id: Optional[int],
) -> dict:
    """The picker's ``{value: label}`` map for one tournament.

    Module-level and ORM-free so the invariant that broke in the browser is
    unit-testable: **the currently-linked matchup is always present**. It is OPEN
    but its slot is already taken, so ``list_linkable_matches`` excludes it, and
    NiceGUI's ``ChoiceElement`` raises ``ValueError: Invalid value`` for a value
    absent from its options — which took the whole edit dialog down.
    """
    options = {None: '(None)'}
    if tournament_id:
        for bm in await bracket_service.list_linkable_matches(tournament_id):
            options[bm.id] = matchup_label(bm)
    if linked_id is not None and linked_id not in options and match_id is not None:
        linked = await bracket_service.get_bracket_match_for_match(match_id)
        if linked is not None:
            options[linked_id] = f'{matchup_label(linked)} (linked)'
    return options


async def render_select(
    bracket_service,
    selected_tournament,
    *,
    brackets_live: bool,
    linked_id: Optional[int],
    match_id: Optional[int],
):
    """Optional 'Bracket matchup' picker, repopulated with the tournament.

    Returns the select, or ``None`` when brackets are off for this tenant — the
    caller then skips the link step entirely rather than reading a widget that
    isn't there.

    The options are resolved **before** the select is constructed: NiceGUI's
    ``ChoiceElement`` rejects a ``value`` that is not among ``options``, so
    building it empty and filling it afterwards raises ``ValueError: Invalid
    value`` on an already-linked match and takes the whole dialog down with it.

    The currently-linked matchup is added to the options by hand: it is OPEN but
    its slot is already taken, so ``list_linkable_matches`` excludes it and the
    select would otherwise have nothing to show for the value it holds.

    The matchup panel above it (:func:`render_matchup_panel`) is rendered from
    here rather than by the caller, so the dialog has one call for the whole
    bracket section and the panel cannot be shown when the picker is not.

    The tenant id is captured here, in request context, and rebound inside
    ``repopulate``: that runs as a ``background_tasks`` task, where the contextvar
    is unset and the client stash is out of reach, so a scoped read would raise
    ``No tenant in context`` — into the log, leaving the picker silently empty.
    """
    if not brackets_live:
        return None

    await render_matchup_panel(bracket_service, match_id)
    tenant_id = require_tenant_id()
    event_id = get_current_event_id()

    async def current_options() -> dict:
        with tenant_scope(tenant_id), event_scope(event_id):
            return await build_options(
                bracket_service, selected_tournament.value,
                linked_id=linked_id, match_id=match_id,
            )

    initial = await current_options()
    select = ui.select(
        label='Bracket matchup (optional)',
        options=initial,
        value=linked_id if linked_id in initial else None,
        with_input=True,
    ).classes('input-full-width')
    select.tooltip(
        'Link this match to a bracket matchup so its result advances the bracket.'
    )

    async def repopulate():
        options = await current_options()
        # Drop a stale selection before swapping the options, for the same
        # ChoiceElement reason: a value absent from the new options is invalid.
        if select.value not in options:
            select.value = None
        select.disable()
        select.options = options
        select.enable()

    selected_tournament.on(
        'update:model-value', lambda: background_tasks.create(repopulate()),
    )
    return select


async def apply_link(
    bracket_service, select, match_id: int, previous_id: Optional[int],
) -> None:
    """Reconcile the picker against the match's existing link, if it changed.

    Called after the match write, so the ``Match`` exists to link. Unlink first
    then link, so moving a match between matchups frees its old slot before
    claiming the new one.
    """
    if select is None:
        return
    chosen = select.value or None
    if chosen == previous_id:
        return

    actor = await get_user_from_session(app.storage.user)
    if previous_id is not None:
        await bracket_service.unlink_match(actor, match_id)  # style-guide: exempt — relinking, the picker is the confirm
    if chosen is not None:
        await bracket_service.link_match_to_bracket_match(actor, chosen, match_id)
