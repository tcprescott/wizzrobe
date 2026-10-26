from typing import Optional

from nicegui import app, background_tasks, context, ui

from application.services import (
    AuthService,
    BracketSetService,
    EventService,
    FeatureFlagService,
    MatchStreamVolunteerService,
    RaceRoomService,
    RacetimeRoomService,
    get_user_from_session,
)
from application.tenant_context import require_tenant_id, tenant_scope
from models import FeatureFlag, RaceRoomStatus
from theme.busy import busy_click
from theme.dialog import _match_bracket_link as bracket_link
from theme.dialog._helpers import (
    dialog_header,
    mobile_sheet,
)
from theme.dialog._set_join_notice import SetJoinNotice
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.dialog.match_dialog_base import (
    BaseMatchDialog,
    enrolment_preview,
)
from theme.help import resolve_help
from theme.markdown_editor import MarkdownEditor
from theme.pickers import CHIP_PROPS
from theme.tables.admin_crud import capture_render_context, scoped_background

# The empty-Tournament hints differ by audience: an admin can go and make one, a
# player requesting a match cannot.
_ADMIN_NO_TOURNAMENTS_HINT = 'No tournaments yet — create one on the Tournaments tab.'
_PLAYER_NO_TOURNAMENTS_HINT = 'No tournaments are open for requests right now.'

# A race room's cached status, read by a human rather than by the bot that wrote
# it — the section used to render the bare enum value ("in_progress").
_ROOM_STATUS_LABEL = {
    RaceRoomStatus.OPEN: 'Open',
    RaceRoomStatus.IN_PROGRESS: 'Racing',
    RaceRoomStatus.FINISHED: 'Finished',
    RaceRoomStatus.CANCELLED: 'Cancelled',
}
_ROOM_STATUS_CHIP = {
    RaceRoomStatus.OPEN: 'wiz-chip--pending',
    RaceRoomStatus.IN_PROGRESS: 'wiz-chip--live',
    RaceRoomStatus.FINISHED: 'wiz-chip--ok',
    RaceRoomStatus.CANCELLED: 'wiz-chip--cancelled',
}
# The two states there is still something to call off in.
_CANCELLABLE_ROOM_STATUSES = (RaceRoomStatus.OPEN, RaceRoomStatus.IN_PROGRESS)


def _no_requestable_message(*, enrolled: bool, enrolled_running: bool) -> str:
    """Why Request Match has no tournament to offer, said truthfully.

    Nothing requestable while enrolled in a *running* tournament means every one
    of them is bracket-run. Enrolled only in finished ones is a different answer:
    pointing that player at a bracket sends them looking for a matchup that will
    never come.
    """
    if enrolled_running:
        return ('Your tournaments are scheduled from their bracket — '
                'schedule your matchup from My Schedule instead.')
    if enrolled:
        return ("The tournaments you're in have finished, so there's nothing to "
                'request a match in. Sign up for a running one on the Tournaments tab.')
    return 'You have not opted into any tournaments. Please opt in before submitting a match.'

class AdminMatchDialog(BaseMatchDialog):
    """Admin view for creating/editing matches with full control."""

    def __init__(self, match=None, on_submit=None):
        super().__init__(match, on_submit)

    async def _render_racetime_room_section(self):
        """Show the match's racetime room, or a manual-create button for it.

        Manual creation is gated on ``can_manage_sync`` (STAFF / SYNC_ADMIN) and
        works regardless of the tournament's auto-open toggle. The section is
        omitted entirely when the community does not have racetime rooms turned
        on, or when the tournament has no racetime bot configured. The flag check
        is not redundant with the bot: a tournament configured while the feature
        was on keeps its bot id afterwards, and without this the dialog would
        offer a Create button that ``RaceRoomService`` then refuses.

        An *existing* room used to be a dead end: the slug rendered as plain
        text, the status as its raw enum value, and ``cancel_room`` — reachable
        over REST and tested — had no web surface at all, so calling off a room
        opened by mistake meant the API or racetime.gg itself.
        """
        if not await FeatureFlagService().is_enabled(FeatureFlag.RACETIME_ROOMS):
            return
        tournament = await self.tournament_service.get_tournament_by_id(self.match.tournament_id)
        if tournament is None or tournament.racetime_bot_id is None:
            return
        existing = await RacetimeRoomService().get_for_match(self.match)
        actor = await get_user_from_session(app.storage.user)
        can_sync = await AuthService.can_manage_sync(actor)
        with ui.column().classes('gap-1'):
            ui.label('Racetime Room').classes('text-bold')
            if existing is not None:
                self._render_existing_room(existing, can_sync)
                return
            if not can_sync:
                ui.label('No room yet.').classes('text-grey-6')
                return
            match_id = self.match.id

            async def create_room():
                try:
                    await RaceRoomService().manual_create_room(actor, match_id)
                except (ValueError, PermissionError) as e:
                    ui.notify(str(e), color='warning')
                    return
                ui.notify('Racetime room created', color='positive')
                self.dialog.close()
                if self.on_submit:
                    await self.on_submit(self.match)

            ui.button(
                'Create racetime room', icon='sports_esports',
                on_click=busy_click('Opening room…', create_room),
            ).props('outline color=secondary')

    def _render_existing_room(self, room, can_sync: bool) -> None:
        """The room a match already has: where it is, how it is, and the way out."""
        with ui.row().classes('items-center gap-2'):
            ui.link(room.slug, room.url, new_tab=True).classes('text-link')
            with ui.element('span').classes(f'wiz-chip {_ROOM_STATUS_CHIP[room.status]}'):
                ui.label(_ROOM_STATUS_LABEL[room.status])
        # Only a live room can be called off; a finished or already-cancelled one
        # has nothing left to stop.
        if not (can_sync and room.status in _CANCELLABLE_ROOM_STATUSES):
            return

        async def do_cancel(client) -> None:
            with client:
                actor = await get_user_from_session(app.storage.user)
                try:
                    await RaceRoomService().cancel_room_as(room, actor=actor)
                except (ValueError, PermissionError) as e:
                    ui.notify(str(e), color='warning')
                    return
                ui.notify(f'Race room {room.slug} cancelled', color='positive')
                # Set in open() before this section renders; mypy only sees the
                # `= None` in __init__.
                self.dialog.close()  # type: ignore[attr-defined]
                if self.on_submit:
                    await self.on_submit(self.match)

        ui.button(
            'Cancel race room', icon='cancel',
            on_click=lambda: ConfirmationDialog(
                title='Cancel race room',
                message=(
                    f'Cancel {room.slug}?\n\n'
                    'Wizzrobe stops treating the room as live and the match keeps '
                    'its schedule. The room itself stays open on racetime.gg — '
                    'close it there too if nobody should race in it.'
                ),
                confirm_text='Cancel the room',
                cancel_text='Leave it open',
                on_confirm=lambda: background_tasks.create(do_cancel(context.client)),
            ).open(),
        ).props('outline color=negative')

    async def open(self):
        # include_inactive: a SpeedGaming placeholder entrant is inactive by
        # construction, and a synced match's roster already contains it — leaving
        # it out of the options would blank an existing player's chip.
        users = await self.user_service.get_community_people_summaries(include_inactive=True)
        stages = await self.stage_service.get_all_stages()
        tournaments, entrant_counts = await self.tournament_service.list_schedulable(
            await get_user_from_session(app.storage.user),
            keep_id=self.match.tournament_id if self.match else None,
        )

        defaults = self._get_default_values()

        if self.match:
            players = await self.match_service.get_match_players(self.match)
            player_ids = [p.user_id for p in players]
            commentator_ids = [c.user_id for c in await self.match.commentators]
            tracker_ids = [t.user_id for t in await self.match.trackers]
            broadcaster_ids = [b.user_id for b in await self.match.broadcasters]
            broadcasters_on = await EventService.broadcasters_on(self.match.event_id)
        else:
            players = []
            player_ids = []
            commentator_ids = []
            tracker_ids = []
            broadcaster_ids = []
            broadcasters_on = await EventService.current_event_broadcasters()

        brackets_live = await FeatureFlagService().is_enabled(FeatureFlag.BRACKETS)
        linked_bracket_match_id = await bracket_link.linked_matchup_id(
            self.bracket_service, self.match, brackets_live=brackets_live,
        )

        is_create = self.match is None
        title = 'Create Match' if is_create else 'Edit Match'

        with ui.dialog() as dialog, ui.card().classes('dialog-card'):
            self.dialog = dialog
            mobile_sheet(dialog)
            dialog_header(title, dialog)
            with ui.column().classes('q-pa-md gap-2'):
                ui.label('* required').classes('required-legend')

                # SpeedGaming read-only contract (PR 7): a match materialized from
                # an SG episode has read-only ETL-owned fields (schedule / players
                # / tournament). Surface a badge and disable those inputs; the
                # service-layer guard is the actual enforcement.
                is_sg_sourced = self.match is not None and getattr(
                    self.match, 'speedgaming_episode_id', None
                ) is not None
                if is_sg_sourced:
                    with ui.row().classes('items-center gap-2'):
                        ui.badge('Synced from SpeedGaming').props('color=primary')
                        ui.label(
                            'Schedule, players, and tournament are read-only — '
                            'updated by the next SpeedGaming sync.'
                        ).classes('text-caption text-grey')

                # Lead with what the match *is*. Everything below edits it.
                if self.match:
                    self._render_state_summary(players)

                selected_tournament = self._render_tournament_select(
                    tournaments, defaults['tournament'], _ADMIN_NO_TOURNAMENTS_HINT,
                    entrant_counts=entrant_counts)

                stage_options = {None: '(None)'}
                stage_options.update({s.id: s.name for s in stages})
                selected_stage = ui.select(
                    label='Stage',
                    options=stage_options,
                    value=defaults['stage'],
                    with_input=True,
                ).classes('input-full-width')

                # Rebound: update_selection_options re-runs as a background task on
                # every tournament change, where the contextvar is unset — the
                # scoped read raised "No tenant in context" and left Players empty.
                tenant_id = require_tenant_id()

                async def get_opted_in_users(tournament_id):
                    with tenant_scope(tenant_id):
                        enrolled = await self.tournament_service.get_enrolled_players_by_tournament_id(tournament_id)
                    user_ids = [tp.user_id for tp in enrolled]
                    return [u for u in users if u.id in user_ids]

                with ui.row().classes('form-pair items-start gap-2'):
                    selected_players = ui.select(
                        label='Players',
                        options={},
                        value=player_ids,
                        multiple=True,
                        with_input=True,
                    ).props(CHIP_PROPS)
                    selected_players.disable()
                    # "Choose any players" meant "any account on the platform"
                    # and now means "any member of this community", which is not
                    # what it did either way: the list it opens is the community,
                    # and what the box changes is the *enrolment* filter.
                    choose_any_players = ui.checkbox(
                        'Include players not enrolled in this tournament', value=False,
                    )

                players_note = ui.label('').classes('text-caption st-pending')
                players_note.set_visibility(False)

                # Scheduling someone into a tournament they are not in does enrol
                # them, which is right — it was just invisible. Name it before the
                # save, and again in the success notification. No confirmation
                # dialog: enrolment is reversible from the tournament's own
                # entrants dialog.
                enrolment_note = ui.label('').classes('text-caption text-grey')
                ui.label(
                    'Leave Players empty to hold the slot, and add them later. '
                    'The title shows in their place until then.'
                ).classes('text-caption text-grey')

                title_input = ui.input(
                    'Title', value=(self.match.title or '') if self.match else '',
                ).props('maxlength=255').classes('input-full-width')
                description_input = MarkdownEditor(
                    'Description', value=(self.match.description or '') if self.match else '',
                    placeholder='What the slot is for, shown with the match on the schedules.',
                    compact=True,
                ).classes('full-width')

                async def update_enrolment_note():
                    tournament_id = selected_tournament.value
                    chosen = selected_players.value or []
                    if not tournament_id or not chosen:
                        enrolment_note.text = ''
                        return
                    enrolled = {u.id for u in await get_opted_in_users(tournament_id)}
                    by_id = {u.id: u for u in users}
                    names = [
                        by_id[uid].preferred_name
                        for uid in chosen if uid in by_id and uid not in enrolled
                    ]
                    with tenant_scope(tenant_id):
                        tournament = await self.tournament_service.get_tournament_by_id(tournament_id)
                    enrolment_note.text = enrolment_preview(
                        names, tournament.name if tournament else 'this tournament',
                    )

                with ui.row().classes('form-pair form-pair--wide items-start gap-2'):
                    selected_commentators = ui.select(
                        label='Commentators',
                        options={u.id: u.preferred_name for u in users},
                        value=commentator_ids,
                        multiple=True,
                        with_input=True,
                    ).props(CHIP_PROPS)

                    selected_trackers = ui.select(
                        label='Trackers',
                        options={u.id: u.preferred_name for u in users},
                        value=tracker_ids,
                        multiple=True,
                        with_input=True,
                    ).props(CHIP_PROPS)

                # Only on an event with broadcasters, or a match that still has
                # some from before they were turned off, so staff can clear them.
                selected_broadcasters = None
                if broadcasters_on or broadcaster_ids:
                    with ui.row().classes('form-pair form-pair--wide items-start gap-2'):
                        selected_broadcasters = ui.select(
                            label='Broadcasters',
                            options={u.id: u.preferred_name for u in users},
                            value=broadcaster_ids,
                            multiple=True,
                            with_input=True,
                        ).props(CHIP_PROPS)

                async def update_selection_options():
                    tournament_id = selected_tournament.value
                    empty_reason = ''
                    if choose_any_players.value and tournament_id:
                        selected_players.disable()
                        selected_players.options = {u.id: u.preferred_name for u in users}
                        selected_players.enable()
                    elif tournament_id:
                        selected_players.disable()
                        opted_in_users = await get_opted_in_users(tournament_id)
                        selected_players.options = {u.id: u.preferred_name for u in opted_in_users}
                        selected_players.enable()
                        # An empty menu with no message is how the old dialog
                        # reported "nobody is enrolled" — after Create had been
                        # pressed, as "Match must have at least one player",
                        # which names neither the cause nor either fix.
                        if not opted_in_users:
                            empty_reason = (
                                'Nobody is enrolled in this tournament. Enrol '
                                'entrants on the Tournaments tab, or tick the '
                                'box beside this list to schedule anyway.'
                            )
                    else:
                        selected_players.options = {}
                        selected_players.disable()
                    players_note.text = empty_reason
                    players_note.set_visibility(bool(empty_reason))

                async def refresh_selection_and_note():
                    await update_selection_options()
                    await update_enrolment_note()

                await refresh_selection_and_note()
                selected_tournament.on('update:model-value', lambda: background_tasks.create(refresh_selection_and_note()))
                choose_any_players.on('update:model-value', lambda: background_tasks.create(refresh_selection_and_note()))
                selected_players.on('update:model-value', lambda: background_tasks.create(update_enrolment_note()))

                date, time = self._render_date_time_inputs(defaults['date'], defaults['time'])

                # Lock the ETL-owned inputs on a sourced match (after the initial
                # option population, which re-enables the players select).
                if is_sg_sourced:
                    selected_tournament.disable()
                    selected_players.disable()
                    choose_any_players.disable()
                    date.disable()
                    time.disable()

                def get_player_ids():
                    pids = selected_players.value
                    if isinstance(pids, list):
                        return [p for p in pids if p is not None]
                    return [pids] if pids else []

                self._render_suggest_time_button(
                    get_tournament_id=lambda: selected_tournament.value,
                    get_player_ids=get_player_ids,
                    date=date,
                    time=time,
                    missing_message='Select a tournament and at least one player first.',
                    exclude_match_id=self.match.id if self.match else None,
                )

                set_notice: Optional[SetJoinNotice] = None
                if is_create:
                    set_notice = SetJoinNotice(
                        tenant_id, await get_user_from_session(app.storage.user),
                        {u.id: u.preferred_name for u in users},
                    )
                    set_notice.watch(
                        selected_tournament, selected_players,
                        get_state=lambda: (selected_tournament.value, get_player_ids()),
                    )
                    await set_notice.refresh(selected_tournament.value, get_player_ids())

                comment_input = ui.textarea(
                    label='Comment (optional)',
                    value=defaults['comment'],
                    placeholder='Add any notes or comments about this match...',
                ).classes('full-width')

                stream_candidate_checkbox = ui.checkbox(
                    'Stream candidate (flag as potential stream match)',
                    value=self.match.is_stream_candidate if self.match else False,
                )

                # The players' own offer, read right where the decision is made.
                # Named as advisory: it is not a second checkbox and ticking the
                # one above is still what puts the match on the stream list.
                if self.match:
                    volunteers = await MatchStreamVolunteerService().names_for_match(self.match)
                    if volunteers:
                        ui.label(
                            f'Offered for stream by {", ".join(volunteers)} — '
                            'advisory, tick the box above to make it a candidate.'
                        ).classes('text-caption st-pending')

                # Staff's own answer to "which preset does this match roll",
                # offered only where there is a second preset to choose between
                # and the seed is still unrolled. Deliberately says nothing about
                # who opted in: the players' answers are theirs, and forcing the
                # harder preset here is a decision about the match rather than a
                # reply to them.
                preset_override_select = await self._render_preset_override()

                selected_bracket_match = await bracket_link.render_select(
                    self.bracket_service, selected_tournament,
                    brackets_live=brackets_live,
                    linked_id=linked_bracket_match_id,
                    match_id=self.match.id if self.match else None,
                )

                if self.match:
                    await self._render_racetime_room_section()

            async def submit():
                tournament_id = selected_tournament.value
                stage_id = selected_stage.value
                date_value = date.value
                time_value = time.value
                comment_value = comment_input.value
                title_value = title_input.value or ''
                description_value = description_input.value or ''
                new_player_ids = selected_players.value if isinstance(selected_players.value, list) else [selected_players.value]
                new_commentator_ids = selected_commentators.value if isinstance(selected_commentators.value, list) else [selected_commentators.value]
                new_tracker_ids = selected_trackers.value if isinstance(selected_trackers.value, list) else [selected_trackers.value]
                new_broadcaster_ids = None
                if selected_broadcasters is not None:
                    new_broadcaster_ids = (selected_broadcasters.value if isinstance(selected_broadcasters.value, list)
                                           else [selected_broadcasters.value])

                async def do_update():
                    actor = await get_user_from_session(app.storage.user)
                    await self.match_service.update_match(
                        match_id=self.match.id,
                        tournament_id=tournament_id,
                        scheduled_date=date_value,
                        scheduled_time=time_value,
                        player_ids=new_player_ids,
                        commentator_ids=new_commentator_ids,
                        tracker_ids=new_tracker_ids,
                        broadcaster_ids=new_broadcaster_ids,
                        comment=comment_value,
                        title=title_value,
                        description=description_value,
                        actor=actor,
                        allow_outside_round=getattr(self, 'allow_outside_round', False),
                    )
                    if (stage_id or None) != self.match.stage_id:
                        await self.match_service.assign_stage(
                            self.match.id, stage_id or None, actor=actor,
                        )
                    if stream_candidate_checkbox.value != self.match.is_stream_candidate:
                        await self.match_service.set_stream_candidate(
                            self.match.id, stream_candidate_checkbox.value, actor=actor,
                        )
                    await bracket_link.apply_link(
                        self.bracket_service, selected_bracket_match,
                        self.match.id, linked_bracket_match_id,
                    )
                    # Last: it DMs both players about the match's settings, so
                    # it must not fire for an edit that then fails to save.
                    await self._save_preset_override(preset_override_select)

                async def do_create():
                    actor = await get_user_from_session(app.storage.user)
                    new_match = await self.match_service.create_match(
                        tournament_id=tournament_id,
                        scheduled_date=date_value,
                        scheduled_time=time_value,
                        comment=comment_value,
                        title=title_value,
                        description=description_value,
                        player_ids=new_player_ids,
                        commentator_ids=new_commentator_ids,
                        tracker_ids=new_tracker_ids,
                        broadcaster_ids=new_broadcaster_ids or [],
                        is_stream_candidate=stream_candidate_checkbox.value,
                        actor=actor,
                    )
                    if stage_id:
                        await self.match_service.assign_stage(new_match.id, stage_id, actor=actor)
                    first_id = set_notice.joining() if set_notice else None
                    if first_id is not None:
                        await BracketSetService().join_set(new_match.id, first_id, actor)
                        return
                    await bracket_link.apply_link(
                        self.bracket_service, selected_bracket_match, new_match.id, None,
                    )

                await self._run_submit(
                    dialog,
                    required_fields={'Tournament': tournament_id, 'Date': date_value, 'Time': time_value},
                    tournament_id=tournament_id,
                    player_ids=new_player_ids,
                    stale_message='Someone else on staff saved this match while you had it open.',
                    do_update=do_update,
                    do_create=do_create,
                    create_success_message='Match created successfully',
                    scheduled=(date_value, time_value),
                    confirm_label='Save anyway' if self.match else 'Create anyway',
                )

            self._render_submit_footer(dialog, submit, create_label='Create')


class UserMatchDialog(BaseMatchDialog):
    """User view for submitting match requests."""

    def __init__(self, discord_id, match=None, on_submit=None):
        super().__init__(match, on_submit)
        self.discord_id = discord_id

    async def open(self):
        # include_inactive: a SpeedGaming placeholder entrant is inactive by
        # construction, and a synced match's roster already contains it — leaving
        # it out of the options would blank an existing player's chip.
        users = await self.user_service.get_community_people_summaries(include_inactive=True)

        user = await self.user_service.get_user_by_discord_id(self.discord_id)
        if not user:
            ui.notify("We couldn't find your account. Try logging in again.", color='negative')
            return

        # Bracket-run tournaments are excluded: their matches come from the
        # bracket, not from a request. The service rejects them either way — this
        # keeps the dropdown from offering a choice that can only fail.
        tournaments = await self.tournament_service.list_player_requestable(user)
        enrolments = await self.tournament_service.get_enrolled_players_by_user(user)
        enrolled_running = any(e.tournament.is_active for e in enrolments)

        defaults = self._get_default_values()

        if self.match:
            players = await self.match_service.get_match_players(self.match)
            opponent_id = next((p.user_id for p in players if p.user_id != user.id), None)
        else:
            opponent_id = None

        is_create = self.match is None
        title = 'Submit Match' if is_create else 'Edit Match'

        with ui.dialog() as dialog, ui.card().classes('dialog-card dialog-card--sm'):
            self.dialog = dialog
            mobile_sheet(dialog)
            # Both dialogs offer Suggest a time, but only this one carries its
            # help: the article is written for a player, about their own and
            # their opponent's availability windows. The admin header has none.
            dialog_header(
                title, dialog,
                help=await resolve_help([('match-request', 'Requesting a match'), 'suggest-time']),
            )
            with ui.column().classes('q-pa-md gap-2'):
                if not tournaments:
                    ui.label(_no_requestable_message(
                        enrolled=bool(enrolments), enrolled_running=enrolled_running,
                    )).classes('text-negative')
                    dialog.open()
                    return

                if self.match:
                    await self._render_watch_switch(user)
                    await self._render_stream_volunteer_switch(user)

                ui.label('* required').classes('required-legend')

                selected_tournament = self._render_tournament_select(
                    tournaments, defaults['tournament'], _PLAYER_NO_TOURNAMENTS_HINT)
                show_all_tournaments = ui.checkbox('Show all tournaments', value=False)

                async def get_opted_in_users(tournament_id):
                    enrolled = await self.tournament_service.get_enrolled_players_by_tournament_id(tournament_id)
                    user_ids = [tp.user_id for tp in enrolled]
                    return [u for u in users if u.id in user_ids and u.discord_id != self.discord_id]

                selected_opponent = ui.select(
                    label='Opponent *',
                    options={},
                    value=opponent_id,
                    with_input=True,
                ).props('required').classes('input-full-width')
                selected_opponent.disable()

                async def update_selection_options():
                    tournament_id = selected_tournament.value

                    tournaments_list = (
                        await self.tournament_service.list_player_requestable()
                        if show_all_tournaments.value else tournaments
                    )
                    selected_tournament.disable()
                    selected_tournament.options = {t.id: t.name for t in tournaments_list}
                    selected_tournament.enable()

                    if tournament_id:
                        selected_opponent.disable()
                        opted_in_users = await get_opted_in_users(tournament_id)
                        selected_opponent.options = {u.id: u.preferred_name for u in opted_in_users}
                        selected_opponent.enable()
                    else:
                        selected_opponent.options = {}
                        selected_opponent.disable()

                await update_selection_options()
                # Sentry SGLMAN-16: the re-run is a background task with no tenant
                # bound, so picking a tournament raised and left Opponent empty.
                render_context = capture_render_context()
                selected_tournament.on(
                    'update:model-value',
                    lambda: scoped_background(render_context, update_selection_options()),
                )
                show_all_tournaments.on(
                    'update:model-value',
                    lambda: scoped_background(render_context, update_selection_options()),
                )

                date, time = self._render_date_time_inputs(defaults['date'], defaults['time'])

                self._render_suggest_time_button(
                    get_tournament_id=lambda: selected_tournament.value,
                    get_player_ids=lambda: [user.id, selected_opponent.value] if selected_opponent.value else [],
                    date=date,
                    time=time,
                    missing_message='Select a tournament and opponent first.',
                )

                comment_input = ui.textarea(
                    label='Comment (optional)',
                    value=defaults['comment'],
                    placeholder='Add any notes or comments about this match...',
                ).classes('full-width')

            async def submit():
                tournament_id = selected_tournament.value
                opp_id = selected_opponent.value
                date_value = date.value
                time_value = time.value
                comment_value = comment_input.value

                new_player_ids = [user.id, opp_id]

                async def do_update():
                    await self.match_service.update_match(
                        match_id=self.match.id,
                        tournament_id=tournament_id,
                        scheduled_date=date_value,
                        scheduled_time=time_value,
                        player_ids=new_player_ids,
                        commentator_ids=[],
                        tracker_ids=[],
                        broadcaster_ids=[],
                        comment=comment_value,
                        actor=user,
                    )

                async def do_create():
                    await self.match_service.submit_match_request(
                        tournament_id=tournament_id,
                        scheduled_date=date_value,
                        scheduled_time=time_value,
                        comment=comment_value,
                        player_ids=new_player_ids,
                        actor=user,
                    )

                await self._run_submit(
                    dialog,
                    required_fields={
                        'Tournament': tournament_id,
                        'Opponent': opp_id,
                        'Date': date_value,
                        'Time': time_value,
                    },
                    tournament_id=tournament_id,
                    player_ids=new_player_ids,
                    stale_message='This match was changed while you had it open.',
                    do_update=do_update,
                    do_create=do_create,
                    create_success_message='Match submitted successfully',
                    scheduled=(date_value, time_value),
                    confirm_label='Save anyway' if self.match else 'Submit anyway',
                )

            self._render_submit_footer(dialog, submit, create_label='Submit')
