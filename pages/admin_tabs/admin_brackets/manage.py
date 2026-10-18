"""Manage dialog — roster, per-stage enrollment, seeding, round chrome, start.

The authoring half of the bracket admin. Everything here is presentation: it
reads through :class:`~application.services.bracket_service.BracketService` and
writes through it too, surfacing service ``ValueError`` / ``PermissionError`` as
toasts. Every scoped read runs inside ``tenant_scope`` because the dialog opens
from a detached client event that has lost the tenant contextvar.
"""

import random
from typing import Dict

from nicegui import ui

from application.services import FeatureFlagService, UserService
from application.tenant_context import tenant_scope
from models import BracketEntryStatus, BracketState, FeatureFlag
from theme.brackets import state_color, state_label
from theme.busy import busy_click
from theme.dialog._helpers import dialog_actions, form_dialog, submit_on_enter
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.notify import notify_error
from theme.steady import steady_refreshable
from theme.tables.admin_crud import current_actor

from .draw_preview import render_draw_preview
from .round_editor import render_round_editor


async def open_manage(row, client, *, service, tenant_id, tournament_id, on_change) -> None:
    """Open the manage dialog for one stage. ``on_change`` refreshes the table."""
    bracket_id = row['id']
    tid = tournament_id
    with client:
        actor = await current_actor()
        # Fetched once for the whole dialog: the link picker is rebuilt on every
        # refresh, and re-reading the user list per refresh would be a query per
        # entrant added.
        users = await UserService().get_community_people_summaries()
        user_names = {u.id: u.preferred_name or u.username for u in users}
        with tenant_scope(tenant_id):
            qualifiers_live = await FeatureFlagService().is_enabled(
                FeatureFlag.ASYNC_QUALIFIERS
            )
        # Survives a body refresh, unlike the widgets: filtering the roster must
        # not reset every time an entrant is added.
        view: Dict[str, object] = {'search': '', 'show_dropped': False}

        # form_dialog, not a bare ui.dialog: it brings the house chrome — a
        # full-screen sheet on a phone plus a sticky header — and the action bar
        # below is sticky with it. Hand-rolled, this dialog put 'Start bracket'
        # and 'Close' ~1200-4400px below the fold.
        with form_dialog(f"Manage — {row['name']} · {row['stage']}", card_classes='dialog-card dialog-card--lg') as dialog:

            @steady_refreshable
            async def body() -> None:
                with tenant_scope(tenant_id):
                    bracket = await service.get_bracket(bracket_id)
                    entrants = await service.list_entrants(tid)
                    entries = await service.list_entries(bracket_id)
                    matches = await service.list_matches(bracket_id)
                    preview = (
                        await service.preview_draw(bracket_id)
                        if bracket is not None and bracket.state == BracketState.DRAFT
                        else None
                    )
                if bracket is None:
                    ui.label('Bracket not found.').classes('text-error')
                    return
                is_draft = bracket.state == BracketState.DRAFT
                enrolled_entrant_ids = {e.entrant_id for e in entries}
                entrant_by_id = {en.id: en for en in entrants}
                name_of = {
                    e.id: (
                        entrant_by_id[e.entrant_id].display_name
                        if e.entrant_id in entrant_by_id else f'Entry {e.id}'
                    )
                    for e in entries
                }
                unlinked = [
                    entrant_by_id[e.entrant_id]
                    for e in entries
                    if e.entrant_id in entrant_by_id
                    and entrant_by_id[e.entrant_id].user_id is None
                ]

                _render_state_banner(bracket, is_draft)
                _render_roster(
                    bracket, entrants, entries, enrolled_entrant_ids, view,
                    service=service, actor=actor, tenant_id=tenant_id, tid=tid,
                    user_names=user_names, is_draft=is_draft, body=body,
                )
                seed_widgets = _render_entries(
                    bracket, entries, name_of, unlinked, is_draft,
                    service=service, actor=actor, tenant_id=tenant_id,
                    qualifiers_live=qualifiers_live, body=body,
                )

                if preview is not None:
                    render_draw_preview(preview, bracket, name_of)

                render_round_editor(
                    matches, bracket, service=service, actor=actor,
                    tenant_id=tenant_id, on_saved=body.refresh,
                )

                async def save_seeds() -> None:
                    # A blanked box submits None — the documented "clears the
                    # seed" contract, which the old filter made unreachable, so
                    # blanking one appeared to work and silently kept the value.
                    seeds = {
                        entry_id: (int(w.value) if w.value is not None else None)
                        for entry_id, w in seed_widgets.items()
                    }
                    with tenant_scope(tenant_id):
                        try:
                            await service.set_seeds(actor, bracket_id, seeds)
                        except (ValueError, PermissionError) as ex:
                            notify_error(ex)
                            return
                    ui.notify('Seeds saved', color='positive')
                    await body.refresh()

                async def do_start() -> None:
                    with tenant_scope(tenant_id):
                        try:
                            await service.start_bracket(actor, bracket_id)
                        except (ValueError, PermissionError) as ex:
                            notify_error(ex)
                            return
                    ui.notify('Bracket started', color='positive')
                    await body.refresh()
                    await on_change()

                async def start() -> None:
                    """Confirm before the one authoring step that cannot be undone.

                    Start is the draw, the publish and the notification at once:
                    afterwards the stage can no longer be edited, reseeded or
                    deleted, and every entrant has already been told their
                    opening matchup.
                    """
                    message = (
                        f'Start “{bracket.name}”? This generates the match graph from '
                        f'the current seeding, publishes the stage publicly, and '
                        f'notifies {len(entries)} entrant(s) of their opening matchup. '
                        'Seeds cannot be changed and the stage cannot be edited or '
                        'deleted afterwards.'
                    )
                    if unlinked:
                        message += (
                            f' {len(unlinked)} of them have no linked user account, so '
                            'their matches cannot be scheduled or announced.'
                        )

                    async def confirmed() -> None:
                        await do_start()

                    confirm = ConfirmationDialog(
                        message, on_confirm=confirmed, confirm_text='Start bracket',
                        busy_text='Starting…',
                    )
                    confirm.open()

                actions.clear()
                actions.set_visibility(bool(is_draft and entries))
                with actions:
                    if is_draft and entries:
                        ui.button('Save seeds', icon='save', on_click=save_seeds) \
                            .props('flat color=primary')
                        ui.button('Start bracket', icon='play_arrow', on_click=start) \
                            .props('color=primary')

            # Both containers exist before anything fills them, so the DOM order
            # is body-then-actions while the action bar still sits *outside* the
            # refreshable: the sheet CSS scopes the sticky footer to a
            # direct child of the card, which a nested refresh container is not —
            # inside, the bar rendered as a floating strip on a phone.
            body_container = ui.column().classes('w-full gap-2')
            actions = dialog_actions().classes('justify-end')
            with body_container:
                await body()
        dialog.open()


def _render_state_banner(bracket, is_draft: bool) -> None:
    """What state this dialog is in, and therefore which half of it is live."""
    with ui.row().classes('items-center gap-2 w-full'):
        ui.badge(state_label(bracket.state), color=state_color(bracket.state))
        ui.label(
            'Enrollment and seeding are open. Nothing is public yet.' if is_draft else
            'The match graph is generated — enrollment and seeding are locked. '
            'Round settings and user links can still be changed.'
        ).classes('text-caption text-grey col min-w-0')


def _render_roster(
    bracket, entrants, entries, enrolled_entrant_ids, view, *,
    service, actor, tenant_id, tid, user_names, is_draft, body,
) -> None:
    """The tournament roster, and the way entrants get into this stage."""
    async def link_user(entrant) -> None:
        """Attach (or detach) one entrant's user account."""
        with form_dialog(f'Link user — {entrant.display_name}', card_classes='dialog-card dialog-card--sm') as link_dialog:
            picker = ui.select(
                user_names, value=entrant.user_id, label='User', with_input=True,
            ).props('clearable').classes('w-full')
            ui.label(
                'A linked entrant can be scheduled, DMed their matchup and joined '
                'to a race room. An unlinked one is a name on a bracket and '
                'nothing else.'
            ).classes('text-caption text-grey')

            async def save_link() -> None:
                with tenant_scope(tenant_id):
                    try:
                        await service.set_entrant_user(
                            actor, entrant.id,
                            int(picker.value) if picker.value else None,
                        )
                    except (ValueError, PermissionError) as ex:
                        notify_error(ex)
                        return
                ui.notify('Entrant link updated', color='positive')
                link_dialog.close()
                await body.refresh()

            with dialog_actions().classes('justify-end'):
                ui.button('Cancel', on_click=link_dialog.close).props('flat')
                ui.button('Save', icon='link', on_click=save_link).props('color=primary')
            submit_on_enter(link_dialog, save_link)
        link_dialog.open()

    async def import_roster() -> None:
        with tenant_scope(tenant_id):
            try:
                created = await service.import_entrants_from_roster(actor, tid)
            except (ValueError, PermissionError) as ex:
                notify_error(ex)
                return
        if created:
            ui.notify(f'Imported {len(created)} entrant(s)', color='positive')
        else:
            ui.notify('Every enrolled player is already on the roster', color='info')
        await body.refresh()

    with ui.row().classes('items-center gap-2 w-full'):
        ui.label('Add entrant to tournament').classes('section-title col min-w-0')
        ui.button('Import roster', icon='group_add', on_click=busy_click('Importing…', import_roster)) \
            .props('flat dense color=primary').tooltip(
                "Adds every player enrolled in this tournament who isn't already "
                'an entrant, each linked to their account'
            )

    with ui.row().classes('items-end gap-2 w-full'):
        name_in = ui.input('Display name').classes('col-12 col-sm')
        # A picker over preferred names, not the raw User.id this field used to
        # ask for: that id is displayed by no screen in the app, so only someone
        # with database access could fill it.
        user_in = ui.select(
            user_names, label='User (optional)', with_input=True,
        ).props('clearable').classes('col-12 col-sm')

        async def add_entrant() -> None:
            picked = int(user_in.value) if user_in.value else None
            with tenant_scope(tenant_id):
                try:
                    await service.add_entrant(
                        actor, tid,
                        (name_in.value or '').strip() or user_names.get(picked, ''),
                        picked,
                    )
                except (ValueError, PermissionError) as ex:
                    notify_error(ex)
                    return
            ui.notify('Entrant added', color='positive')
            await body.refresh()

        ui.button('Add', icon='person_add', on_click=add_entrant).props('color=primary')
    ui.label(
        'Entrants belong to the tournament, not to this stage — the same entrant '
        'carries into every stage they are enrolled in.'
    ).classes('text-caption text-grey')

    ui.separator()
    ui.label(f'Roster ({len(entrants)}) — enroll into this stage').classes('section-title')

    if not entrants:
        ui.label(
            'No entrants yet. Use "Import roster" above to bring in everyone '
            'enrolled in the tournament, or add one by hand.'
        ).classes('text-muted')
        return

    @ui.refreshable
    def roster_list() -> None:
        needle = str(view['search']).strip().lower()
        shown = [
            en for en in entrants
            if (not needle or needle in en.display_name.lower())
            and (view['show_dropped'] or en.status.value != 'dropped')
        ]
        if not shown:
            ui.label('No entrant matches that filter.').classes('text-muted')
            return
        for entrant in shown:
            _roster_row(
                entrant, enrolled_entrant_ids,
                service=service, actor=actor, tenant_id=tenant_id,
                bracket=bracket, is_draft=is_draft, user_names=user_names,
                link_user=link_user, body=body,
            )

    def set_search(value) -> None:
        view['search'] = value or ''
        roster_list.refresh()

    def set_dropped(value) -> None:
        view['show_dropped'] = bool(value)
        roster_list.refresh()

    # Unbounded and unsearchable is fine at 8 entrants and unusable at 64 — the
    # volunteer picker has had both since it existed.
    with ui.row().classes('items-center gap-2 w-full'):
        ui.input(
            'Filter roster', value=view['search'],
            on_change=lambda e: set_search(e.value),
        ).props('dense clearable').classes('col')
        ui.switch(
            'Show retired', value=bool(view['show_dropped']),
            on_change=lambda e: set_dropped(e.value),
        ).props('dense')

    roster_list()


def _roster_row(
    entrant, enrolled_entrant_ids, *,
    service, actor, tenant_id, bracket, is_draft, user_names, link_user, body,
) -> None:
    """One roster line: who they are, whether they are linked, their way in."""
    dropped = entrant.status.value == 'dropped'
    with ui.row().classes('items-center gap-2 w-full no-wrap'):
        # `col min-w-0 ellipsis`: a long display name otherwise pushed the seed
        # field and Enroll onto a second line on every phone-width row.
        label = ui.label(entrant.display_name).classes('text-bold col min-w-0 ellipsis')
        if dropped:
            label.classes('text-grey')
        linked = entrant.user_id is not None
        ui.button(
            icon='link' if linked else 'link_off',
            on_click=lambda _=None, e=entrant: link_user(e),
        ).props(
            'flat round dense color=' + ('positive' if linked else 'warning')
        ).tooltip(
            f'Linked to {user_names.get(entrant.user_id, "a user")}' if linked
            else 'Not linked to a user — cannot be scheduled or notified'
        )

        if entrant.id in enrolled_entrant_ids:
            ui.badge('enrolled', color='positive')
        elif dropped:
            # A dropped entrant kept a live Enroll button, which is an invitation
            # to put someone back into a field they have withdrawn from.
            ui.badge('retired', color='grey')
        else:
            seed_in = ui.number('Seed', min=1, precision=0) \
                .props('inputmode=numeric dense' + ('' if is_draft else ' disable')) \
                .classes('w-24')

            async def enroll(_=None, entrant_id=entrant.id, seed_widget=seed_in) -> None:
                with tenant_scope(tenant_id):
                    try:
                        await service.enroll(
                            actor, bracket.id, entrant_id,
                            int(seed_widget.value) if seed_widget.value else None,
                        )
                    except (ValueError, PermissionError) as ex:
                        notify_error(ex)
                        return
                ui.notify('Enrolled', color='positive')
                await body.refresh()

            ui.button('Enroll', icon='how_to_reg', on_click=enroll).props(
                'flat color=primary' + ('' if is_draft else ' disable')
            )

        async def do_drop() -> None:
            with tenant_scope(tenant_id):
                try:
                    await service.drop_entrant(actor, entrant.id)
                except (ValueError, PermissionError) as ex:
                    notify_error(ex)
                    return
            ui.notify('Entrant retired from the roster', color='positive')
            await body.refresh()

        def drop(_=None) -> None:
            ConfirmationDialog(
                message=f'Retire {entrant.display_name} from the tournament roster? '
                        'Their stage entries are left alone.',
                on_confirm=do_drop, confirm_text='Retire', title='Retire from roster?',
            ).open()

        if not dropped:
            ui.button(icon='person_off', on_click=drop) \
                .props('flat round dense color=grey').tooltip(
                    'Retire from the tournament roster (their stage entries are '
                    'left alone)'
                )


def _render_entries(
    bracket, entries, name_of, unlinked, is_draft, *,
    service, actor, tenant_id, qualifiers_live, body,
) -> Dict[int, object]:
    """The stage's field and its seeding. Returns the seed widgets by entry id."""
    ui.separator()
    ui.label(f'Enrolled entries ({len(entries)})').classes('section-title')
    if not entries:
        ui.label(
            'Nobody enrolled yet — enroll at least two entrants from the roster '
            'above and Start becomes available.'
        ).classes('text-muted')
        return {}

    if not is_draft:
        ui.label('Seeding is fixed once the graph is generated.') \
            .classes('text-caption text-grey')
    if unlinked:
        ui.label(
            f'{len(unlinked)} enrolled entrant(s) have no linked user: '
            + ', '.join(en.display_name for en in unlinked)
            + '. Their matches cannot be scheduled, announced or run in a race '
            'room until they are linked.'
        ).classes('text-caption text-warning')

    seed_widgets: Dict[int, object] = {}
    if is_draft:
        _render_seed_tools(
            bracket, entries,
            service=service, actor=actor, tenant_id=tenant_id,
            qualifiers_live=qualifiers_live, body=body,
        )
        ui.label(
            'Left blank, a seed is filled in at Start from the remaining numbers '
            'in enrollment order.'
        ).classes('text-caption text-grey')

    for entry in entries:
        retired = entry.status == BracketEntryStatus.DROPPED
        with ui.row().classes('items-center gap-2 w-full no-wrap'):
            label = ui.label(name_of.get(entry.id, f'Entry {entry.id}')) \
                .classes('col min-w-0 ellipsis')
            if retired:
                label.classes('text-grey')
                ui.badge('retired', color='grey')
            seed_widgets[entry.id] = ui.number(
                'Seed', value=entry.seed, min=1, precision=0,
            ).props(
                'inputmode=numeric dense' + ('' if is_draft else ' disable')
            ).classes('w-24')
            _render_entry_actions(
                entry, retired, is_draft, name_of.get(entry.id, f'Entry {entry.id}'),
                service=service, actor=actor, tenant_id=tenant_id, body=body,
            )
    return seed_widgets


def _render_entry_actions(
    entry, retired, is_draft, name, *, service, actor, tenant_id, body,
) -> None:
    """Leaving the stage: un-enrolled while DRAFT, retired once it is running."""
    async def do_unenroll() -> None:
        with tenant_scope(tenant_id):
            try:
                await service.unenroll(actor, entry.id)
            except (ValueError, PermissionError) as ex:
                notify_error(ex)
                return
        ui.notify('Un-enrolled', color='positive')
        await body.refresh()

    def unenroll(_=None) -> None:
        ConfirmationDialog(
            message=f'Un-enroll {name} from this stage?',
            on_confirm=do_unenroll, confirm_text='Un-enroll', title='Un-enroll?',
        ).open()

    async def do_retire() -> None:
        with tenant_scope(tenant_id):
            try:
                await service.retire_entry(actor, entry.id)
            except (ValueError, PermissionError) as ex:
                notify_error(ex)
                return
        ui.notify('Entry retired', color='positive')
        await body.refresh()

    def retire(_=None) -> None:
        ConfirmationDialog(
            message=f'Retire {name} from this stage? Their played results stand, but '
                    'Swiss stops pairing them and advancement skips them.',
            on_confirm=do_retire, confirm_text='Retire', title='Retire from stage?',
        ).open()

    if is_draft:
        ui.button(icon='remove_circle_outline', on_click=unenroll) \
            .props('flat round dense color=negative') \
            .tooltip('Un-enroll from this stage')
    elif not retired:
        ui.button(icon='logout', on_click=retire) \
            .props('flat round dense color=warning').tooltip(
                'Retire from this stage — their played results stand, but Swiss '
                'stops pairing them and advancement skips them'
            )


def _render_seed_tools(
    bracket, entries, *, service, actor, tenant_id, qualifiers_live, body,
) -> None:
    """Bulk seeding, so a 32-entrant field is not 32 boxes typed by hand."""
    async def apply(seeds: Dict[int, object], message: str) -> None:
        with tenant_scope(tenant_id):
            try:
                await service.set_seeds(actor, bracket.id, seeds)
            except (ValueError, PermissionError) as ex:
                notify_error(ex)
                return
        ui.notify(message, color='positive')
        await body.refresh()

    ordered = list(entries)

    async def number_as_listed() -> None:
        await apply(
            {e.id: i for i, e in enumerate(ordered, start=1)}, 'Seeded in list order',
        )

    async def reverse() -> None:
        await apply(
            {e.id: i for i, e in enumerate(reversed(ordered), start=1)},
            'Seeding reversed',
        )

    async def shuffle() -> None:
        shuffled = list(ordered)
        random.shuffle(shuffled)
        await apply(
            {e.id: i for i, e in enumerate(shuffled, start=1)}, 'Seeding randomized',
        )

    def clear() -> None:
        async def do_clear() -> None:
            await apply({e.id: None for e in ordered}, 'Seeds cleared')

        ConfirmationDialog(
            message='Clear every seed in this stage? Seeds you typed by hand are lost.',
            on_confirm=do_clear, confirm_text='Clear seeds', title='Clear seeds?',
        ).open()

    async def from_qualifier() -> None:
        await _open_qualifier_seeding(
            bracket, entries, service=service, actor=actor,
            tenant_id=tenant_id, apply=apply,
        )

    with ui.row().classes('items-center gap-1 w-full'):
        ui.button('Number 1..N', icon='format_list_numbered', on_click=number_as_listed) \
            .props('flat dense color=primary').tooltip('Seed in the order listed here')
        ui.button('Reverse', icon='swap_vert', on_click=reverse) \
            .props('flat dense color=primary')
        ui.button('Shuffle', icon='casino', on_click=shuffle) \
            .props('flat dense color=primary')
        ui.button('Clear', icon='backspace', on_click=clear) \
            .props('flat dense color=grey')
        if qualifiers_live:
            ui.button('From qualifier', icon='leaderboard', on_click=from_qualifier) \
                .props('flat dense color=primary').tooltip(
                    'Seed by an async qualifier leaderboard, matching on linked users'
                )


async def _open_qualifier_seeding(
    bracket, entries, *, service, actor, tenant_id, apply,
) -> None:
    """Seed from a qualifier leaderboard rather than by transcription.

    A qualifier-seeded event is the standard shape here, and doing it by hand
    means reading a leaderboard in another tab and typing the order in. Entrants
    are matched on their linked user; anyone the leaderboard does not cover is
    reported by name rather than guessed at.
    """
    from application.services.async_qualifier import AsyncQualifierService

    qualifier_service = AsyncQualifierService()
    with tenant_scope(tenant_id):
        try:
            qualifiers = await qualifier_service.list_qualifiers(actor)
            entrants = {
                en.id: en for en in await service.list_entrants(bracket.tournament_id)
            }
        except (ValueError, PermissionError) as ex:
            notify_error(ex)
            return

    if not qualifiers:
        ui.notify('No qualifiers yet. Create one first to link entrants.', color='info')
        return

    with form_dialog('Seed from a qualifier', card_classes='dialog-card dialog-card--sm') as dialog:
        picker = ui.select(
            {q.id: q.name for q in qualifiers}, label='Qualifier',
            value=qualifiers[0].id, with_input=True,
        ).classes('w-full')
        ui.label(
            'Entrants are matched to leaderboard positions by their linked user '
            'account, best score first. Entrants the qualifier does not cover keep '
            'their current seed and are listed back to you.'
        ).classes('text-caption text-grey')

        async def do_seed() -> None:
            with tenant_scope(tenant_id):
                try:
                    board = await qualifier_service.get_leaderboard(actor, picker.value)
                except (ValueError, PermissionError) as ex:
                    notify_error(ex)
                    return
            rank_of_user = {row.user_id: i for i, row in enumerate(board)}
            ranked, unmatched = [], []
            for entry in entries:
                entrant = entrants.get(entry.entrant_id)
                user_id = entrant.user_id if entrant is not None else None
                if user_id is not None and user_id in rank_of_user:
                    ranked.append((rank_of_user[user_id], entry))
                else:
                    unmatched.append(
                        entrant.display_name if entrant else f'Entry {entry.id}'
                    )
            if not ranked:
                notify_error(ValueError(
                    'No enrolled entrant is linked to a user on that leaderboard.'
                ))
                return
            ranked.sort(key=lambda pair: pair[0])
            seeds = {entry.id: i for i, (_rank, entry) in enumerate(ranked, start=1)}
            dialog.close()
            await apply(seeds, f'Seeded {len(seeds)} entrant(s) from the qualifier')
            if unmatched:
                ui.notify(
                    f'{len(unmatched)} not on the leaderboard, left unseeded: '
                    + ', '.join(unmatched),
                    color='warning', multi_line=True, close_button='Dismiss',
                    timeout=15000,
                )

        with dialog_actions().classes('justify-end'):
            ui.button('Cancel', on_click=dialog.close).props('flat')
            ui.button('Seed', icon='leaderboard', on_click=do_seed) \
                .props('color=primary')
    dialog.open()
