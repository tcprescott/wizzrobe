"""Dialog-backed lifecycle callbacks for a ``MatchTableView``.

Presentation-layer glue: opens the shared dialogs, calls the match services,
reports failures through ``notify_error``, and refreshes the row it touched.
The admin Schedule tab and the proctor board both build their view from an
instance of this, so their lifecycle behaviour cannot drift.

Construction is two-phase because the dependency is circular — the view needs
the callbacks, and the callbacks need the view to refresh a row::

    handlers = MatchLifecycleHandlers(page_container, access=access)
    table_view = MatchTableView(columns=..., get_query=..., access=access, **handlers.callbacks())
    handlers.table_view = table_view

The read-only ``Match.get(id=..., tenant_id=require_tenant_id())`` loads here are
the sanctioned presentation-layer load-or-404 shape (CLAUDE.md, entry surfaces);
every *write* goes through a service.
"""

from typing import Any

from nicegui import app, ui

from application.services import MatchScheduleService, MatchService, get_user_from_session
from application.services.match.match_status import closes_without_winner
from application.tenant_context import require_tenant_id
from application.utils.match_labels import match_model_label
from models import Match
from theme.dialog import ConfirmationDialog, MatchResultDialog, StationAssignmentDialog
from theme.dialog.match_dialog import AdminMatchDialog
from theme.notify import notify_error
from theme.tables.match_access import MatchBoardAccess
from theme.tables.match_slots import CANDIDATE_STAGE


def open_race_finish_message(match: Match) -> str:
    """The body of the Finish prompt for an open race (more than four players)."""
    return (f'Finish this {len(match.players)}-player race? No winner is recorded, '  # type: ignore[attr-defined]
            'and finishing also confirms the result.')


def confirm_result_message(match: Match) -> str:
    """The body of the admin's confirm-a-result dialog. Needs ``players__user``.

    Names the winner rather than just listing both players: this is the step that
    settles the match and advances the bracket, and an admin working a queue of
    them needs to check the name against what they were told without closing the
    dialog to read the row behind it. A flagged result says so for the same
    reason: it is why this match is in front of them.
    """
    winner = next((p for p in match.players if p.finish_rank == 1), None)
    # Only a head-to-head has a single loser to name; with three or more the
    # "X beat Y" line would pick one arbitrarily, so list the field instead.
    others = [p for p in match.players if p is not winner]

    def name(player) -> str:
        return player.user.preferred_name if player else ''

    if winner is not None:
        lines = [f'Record {name(winner)} as the winner of {match_model_label(match)}?']
        if len(others) == 1:
            lines.append(f'{name(winner)} beat {name(others[0])}.')
        elif others:
            lines.append('Against: ' + ', '.join(name(p) for p in others) + '.')
    else:
        # Reachable from a stale row (someone else cleared the result between the
        # render and the click); confirm_match refuses it, so say so up front.
        lines = [f'No winner is recorded for {match_model_label(match)}.',
                 'Record one before confirming.']
    if match.needs_review:
        lines.append('Flagged for review by the proctor. Confirming clears the flag.')
    return '\n\n'.join(lines)


class MatchLifecycleHandlers:
    """The ``on_*`` callbacks a match table needs to run a match's lifecycle."""

    def __init__(self, page_container, *, access: MatchBoardAccess):
        self.page_container = page_container
        self.access = access
        # Assigned by the caller after the view exists (the two-phase construction
        # above). Untyped because the view imports these handlers' module — naming
        # ``MatchTableView`` here would close the import cycle.
        self.table_view: Any = None
        self.schedule_service = MatchScheduleService()
        self.match_service = MatchService()

    def callbacks(self) -> dict:
        """The ``on_*`` kwargs for ``MatchTableView``, one group per capability.

        A callback the viewer's capability does not cover is omitted entirely
        rather than passed as ``None``: ``MatchTableView`` keys its event wiring
        and part of its slot registration off callback presence, so omission is
        what stops the control from reaching a service that would refuse it.

        These five groups mirror ``MatchBoardAccess`` field for field; the board
        offering a control the service then refuses is precisely the failure this
        replaced.
        """
        cb: dict = {}
        if self.access.run:
            cb['on_generate_seed'] = self.on_generate_seed
            cb['on_seat'] = self.on_seat
            cb['on_start'] = self.on_start
            cb['on_finish'] = self.on_finish
            cb['on_assign_stations'] = self.on_assign_stations
        if self.access.edit:
            cb['on_edit'] = self.on_edit
        if self.access.confirm:
            cb['on_confirm'] = self.on_confirm
            cb['on_edit_result'] = self.on_edit_result
        if self.access.assign_stream:
            cb['on_set_stage'] = self.on_set_stage
        return cb

    async def _actor(self):
        return await get_user_from_session(app.storage.user)

    async def on_edit(self, match_id: int):
        match = await Match.get(id=match_id, tenant_id=require_tenant_id())

        async def after_edit(_):
            await self.table_view.update_row_by_id(match_id)
        with self.page_container:
            dialog = AdminMatchDialog(match=match, on_submit=after_edit)
            await dialog.open()

    async def on_generate_seed(self, match_id: int):
        actor = await self._actor()
        success, message, _ = await self.schedule_service.generate_seed(match_id, actor=actor)

        with self.page_container:
            if success:
                ui.notify(message, color='positive')
            else:
                if "already in progress" in message.lower():
                    # Said, not swallowed: a roll stranded by a restart blocks
                    # re-rolls for a while, and silence read as a dead button.
                    ui.notify(message, color='info')
                else:
                    ui.notify(message, color='warning' if "already been generated" in message else 'negative')

        # Always refresh the row to clear spinner
        await self.table_view.update_row_by_id(match_id)

    async def on_clear_seed(self, match_id: int):
        """Clear a rolled seed from the match sheet, after a confirm.

        Not in :meth:`callbacks`: the board has no clear-seed control, only the
        sheet does, and it offers this only where the viewer may edit the match.
        """
        async def confirm():
            try:
                actor = await self._actor()
                await self.match_service.clear_match_seed(match_id, actor=actor)
                await self.table_view.update_row_by_id(match_id)
                with self.page_container:
                    ui.notify('Seed cleared', color='positive')
            except (ValueError, PermissionError) as e:
                with self.page_container:
                    notify_error(e)

        with self.page_container:
            ConfirmationDialog(
                title='Clear seed?',
                message="Clear this match's seed? Players will need a new one rolled.",
                on_confirm=confirm,
                confirm_text='Clear seed',
                cancel_text='Back',
            ).open()

    async def on_seat(self, match_id: int):
        match = await Match.get(id=match_id, tenant_id=require_tenant_id()).prefetch_related('tournament', 'players', 'players__user')

        # A stage match is played on the stage, so there is nobody to seat.
        if match.stage_id is not None:
            player_names = ', '.join(p.user.preferred_name for p in match.players)
            with self.page_container:
                ConfirmationDialog(
                    title='Check in match',
                    message=f'Check in {match_model_label(match)}?\n\n{player_names}',
                    confirm_text='Check in',
                    tone='primary',
                    on_confirm=lambda _: self.confirm_seating(match),
                ).open()
            return

        async def handle_confirm(_):
            dialog.dialog.close()
            await self.confirm_seating(match)
        with self.page_container:
            dialog = StationAssignmentDialog(
                match=match,
                on_submit=handle_confirm,
                purpose='checkin',
            )
            await dialog.open()

    async def confirm_seating(self, match: Match):
        try:
            actor = await self._actor()
            await self.schedule_service.seat_match(match, actor=actor)
            await self.table_view.update_row_by_id(match.id)
            with self.page_container:
                ui.notify(
                    f'{match_model_label(match, with_context=False)} checked in.',
                    color='positive',
                )
        except (PermissionError, ValueError) as e:
            with self.page_container:
                notify_error(e)

    async def on_start(self, match_id: int):
        match = await Match.get(id=match_id, tenant_id=require_tenant_id()).prefetch_related('tournament', 'players', 'players__user')
        player_names = ', '.join(
            [p.user.preferred_name for p in match.players])

        # Stations are optional at check-in, so starting asks again for any
        # still missing. A stage match is played where the stage is.
        if match.stage_id is None and any(not p.assigned_station for p in match.players):  # type: ignore[attr-defined]
            async def handle_seated(_):
                await self.confirm_starting(match)
            with self.page_container:
                await StationAssignmentDialog(
                    match=match, on_submit=handle_seated, purpose='start',
                ).open()
            return

        async def handle_confirm(_):
            await self.confirm_starting(match)
        with self.page_container:
            dialog = ConfirmationDialog(
                title='Start match',
                message=f'Start {match_model_label(match)}?\n\n{player_names}',
                confirm_text='Start match',
                tone='primary',
                on_confirm=handle_confirm,
            )
            dialog.open()

    async def confirm_starting(self, match: Match):
        try:
            actor = await self._actor()
            await self.schedule_service.start_match(match, actor=actor)
            await self.table_view.update_row_by_id(match.id)
        except (PermissionError, ValueError) as e:
            with self.page_container:
                notify_error(e)

    async def on_finish(self, match_id: int):
        match = await Match.get(id=match_id, tenant_id=require_tenant_id()).prefetch_related('tournament', 'players', 'players__user')

        if closes_without_winner(match.players):  # type: ignore[attr-defined]
            with self.page_container:
                ConfirmationDialog(
                    title='Finish race',
                    message=open_race_finish_message(match),
                    confirm_text='Finish race',
                    tone='primary',
                    on_confirm=lambda _: self.confirm_finishing(match),
                ).open()
            return

        async def handle_confirm(_):
            dialog.dialog.close()
            await self.confirm_finishing(match)
        with self.page_container:
            dialog = MatchResultDialog(
                match=match,
                on_submit=handle_confirm
            )
            await dialog.open()

    async def confirm_finishing(self, match: Match):
        try:
            actor = await self._actor()
            await self.schedule_service.finish_match(match, actor=actor)
            await self.table_view.update_row_by_id(match.id)
        except (PermissionError, ValueError) as e:
            with self.page_container:
                notify_error(e)

    async def on_confirm(self, match_id: int):
        match = await Match.get(id=match_id, tenant_id=require_tenant_id()).prefetch_related('tournament', 'players', 'players__user')

        async def handle_confirm(_):
            await self.confirm_confirming(match)
        with self.page_container:
            dialog = ConfirmationDialog(
                title='Confirm result',
                message=confirm_result_message(match),
                confirm_text='Confirm result',
                tone='primary',
                on_confirm=handle_confirm,
            )
            dialog.open()

    async def confirm_confirming(self, match: Match):
        try:
            actor = await self._actor()
            await self.schedule_service.confirm_match(match, actor=actor)
            await self.table_view.update_row_by_id(match.id)
        except (PermissionError, ValueError) as e:
            with self.page_container:
                notify_error(e)

    async def on_edit_result(self, match_id: int):
        """Correct the winner already recorded on a finished match.

        Deliberately has no ``finish_match`` step: the match is already
        finished, and re-finishing it would rewrite ``finished_at`` and fire a
        second round of notifications for a match nobody just played. Recording
        the result is the whole of the change. A settled bracket game is
        refused by the service, which is the one correction path that
        re-advances the bracket.
        """
        match = await Match.get(
            id=match_id, tenant_id=require_tenant_id(),
        ).prefetch_related('tournament', 'players', 'players__user')

        async def after_edit(_):
            await self.table_view.update_row_by_id(match_id)

        with self.page_container:
            dialog = MatchResultDialog(match=match, on_submit=after_edit, mode='edit')
            await dialog.open()

    async def on_set_stage(self, match_id: int, stage):
        """Write the row's Stage select: a stage, ``Candidate``, or neither.

        ``Candidate`` is not a stage — it is ``is_stream_candidate``, the answer
        for "this is going out, we have not picked a stage yet". Choosing a real
        stage leaves that flag alone: the cell stops *showing* candidate because
        the stage is the more specific answer, but the coverage reports still
        count the match, which clearing the flag would silently stop.

        The select is driven by the row, so every path ends in a row refresh —
        that is also what snaps it back when a service refuses the write.
        """
        match = await Match.get(id=match_id, tenant_id=require_tenant_id())
        want_candidate = stage == CANDIDATE_STAGE
        stage_id = None if (stage is None or want_candidate) else int(stage)
        try:
            actor = await self._actor()
            if match.stage_id != stage_id:  # type: ignore[attr-defined]
                await self.match_service.assign_stage(match_id, stage_id, actor=actor)
            if stage_id is None and match.is_stream_candidate != want_candidate:
                await self.match_service.set_stream_candidate(match_id, want_candidate, actor=actor)
        except (PermissionError, ValueError) as e:
            with self.page_container:
                notify_error(e)
        await self.table_view.update_row_by_id(match_id)

    async def on_assign_stations(self, match_id: int):
        match = await Match.get(id=match_id, tenant_id=require_tenant_id()).prefetch_related('tournament', 'players', 'players__user')

        async def after_assign(_):
            await self.table_view.update_row_by_id(match_id)
        with self.page_container:
            dialog = StationAssignmentDialog(match=match, on_submit=after_assign, purpose='stations')
            await dialog.open()
