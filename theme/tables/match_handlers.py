"""Event-handler coroutines for the match table.

``MatchTableView`` mixes this in; the handlers operate on the view's ``self``
(``self.service``, ``self.table``, ``self.update_row_by_id``, the ``on_*``
callbacks, …). ``match.py`` keeps the event *wiring* (``table.on(...)``); the
handler *bodies* live here. Presentation layer: services are reached via the
view's injected instances / lazy imports, never repositories.
"""

from nicegui import app, ui

from application.services import get_user_from_session
from application.utils.match_labels import match_row_label
from theme.dialog import ConfirmationDialog, UserDialog
from theme.notify import notify_error
from theme.tables.hard_preset_rows import apply_hard_preset_state
from theme.undo_toast import undo_toast


class MatchTableHandlersMixin:
    """Coroutine handlers for the match-table events, bound to a MatchTableView."""

    def _event_match_id(self, event):
        if hasattr(event, 'args'):
            args = event.args
            if isinstance(args, dict):
                if 'key' in args:
                    return args['key']
                if 'row' in args and isinstance(args['row'], dict) and 'id' in args['row']:
                    return args['row']['id']
        return None

    async def _handle_acknowledge_match(self, row, client):
        with client:
            user = await get_user_from_session(app.storage.user)
            if not user:
                ui.notify('You must be logged in to acknowledge.', color='warning')
                return
            match_id = row['id']
            try:
                await self.service.acknowledge_match(match_id, user)
                ui.notify(f'You acknowledged {match_row_label(row, with_context=False)}.', color='positive')
                await self.update_row_by_id(match_id)
            except ValueError as e:
                ui.notify(str(e), color='warning')

    async def _handle_edit_role(self, role, event):
        row = event.args['row']
        idx = event.args['idx']
        match_id = row['id']
        match_query = self.get_query()
        prefetch_map = {
            'commentator': ('commentators', 'commentators__user'),
            'tracker': ('trackers', 'trackers__user'),
            'broadcaster': ('broadcasters', 'broadcasters__user'),
        }
        attr_map = {
            'commentator': 'commentators',
            'tracker': 'trackers',
            'broadcaster': 'broadcasters',
        }
        if role not in prefetch_map:
            ui.notify(f'Unknown role: {role}', color='warning')
            return
        m = await match_query.filter(id=match_id).prefetch_related(*prefetch_map[role]).first()
        items = getattr(m, attr_map[role], []) if m else []
        if not m or idx >= len(items):
            ui.notify(f'That {role} could not be found. The page may be out of date.', color='warning')
            return

        user = items[idx].user
        with self.table_container:
            dialog = UserDialog(user)
            await dialog.open()

    async def _handle_toggle_approval(self, role, event):
        row = event.args['row']
        idx = event.args['idx']
        match_id = row['id']
        match_query = self.get_query()
        prefetch_map = {
            'commentator': ('commentators', 'commentators__user'),
            'tracker': ('trackers', 'trackers__user'),
            'broadcaster': ('broadcasters', 'broadcasters__user'),
        }
        attr_map = {
            'commentator': 'commentators',
            'tracker': 'trackers',
            'broadcaster': 'broadcasters',
        }
        if role not in prefetch_map:
            ui.notify(f'Unknown role: {role}', color='warning')
            return
        m = await match_query.filter(id=match_id).prefetch_related(*prefetch_map[role]).first()
        items = getattr(m, attr_map[role], []) if m else []
        if not m or idx >= len(items):
            ui.notify(f'That {role} could not be found. The page may be out of date.', color='warning')
            return

        await self._confirm_crew_approval(role, row, m, items[idx])

    async def _handle_quick_approve(self, args):
        """Approve from the run sheet's Approve button.

        One tap, with Undo in the toast; the dialog only comes back when the
        person is committed elsewhere at the same time.
        """
        row = args['row']
        role = {'commentators': 'commentator', 'trackers': 'tracker', 'broadcasters': 'broadcaster'}.get(args.get('role'))
        if role is None:
            ui.notify(f"Unknown role: {args.get('role')}", color='warning')
            return
        attr = f'{role}s'
        m = await self.get_query().filter(id=row['id']).prefetch_related(attr, f'{attr}__user').first()
        crew_member = next((c for c in getattr(m, attr, []) if c.id == args.get('crew_id')), None) if m else None
        if crew_member is None:
            ui.notify(f'That {role} could not be found. The page may be out of date.', color='warning')
            return
        if crew_member.approved:
            ui.notify(f'{crew_member.user.preferred_name} is already approved.', color='info')
            await self.update_row_by_id(row['id'])
            return
        await self._confirm_crew_approval(role, row, m, crew_member)

    async def _confirm_crew_approval(self, role, row, m, crew_member):
        from application.services import CrewService

        match_id = row['id']
        name = crew_member.user.preferred_name
        approving = not crew_member.approved

        async def apply():
            try:
                actor = await get_user_from_session(app.storage.user)
                await CrewService().update_crew_approval(
                    crew_member=crew_member,
                    crew_type=role,
                    approved=approving,
                    actor=actor,
                )
                ui.notify(
                    f'{name} {"approved as" if approving else "un-approved as"} {role} '
                    f'for {match_row_label(row, with_context=False)}.',
                    color='positive',
                )
                await self.update_row_by_id(match_id)
            except (ValueError, PermissionError) as e:
                notify_error(e)

        if approving:
            clashes = await CrewService().find_scheduling_conflicts(
                crew_member.user, m, role,
            )
            if not clashes:
                await self._approve_with_undo(role, row, crew_member)
                return
            message = (f'Approve {name} as {role} for {match_row_label(row)}?\n\n'
                       "They'll get a Discord message asking them to acknowledge the assignment.")
            if clashes:
                listed = '\n'.join(f'• {clash}' for clash in clashes)
                message += (f'\n\n⚠ {name} is already committed at the same time:\n'
                            f'{listed}')
            title, confirm_text, tone = f'Approve {role}', 'Approve', 'primary'
        else:
            message = (f'Remove approval for {name} as {role} for {match_row_label(row)}?\n\n'
                       "This also clears their acknowledgment. They'll get a Discord message "
                       'letting them know.')
            title, confirm_text, tone = f'Un-approve {role}', 'Un-approve', 'negative'

        with self.table_container:
            dialog = ConfirmationDialog(
                message,
                title=title,
                confirm_text=confirm_text,
                cancel_text='Cancel',
                tone=tone,
                on_confirm=apply,
            )
            dialog.open()

    async def _approve_with_undo(self, role, row, crew_member):
        """Approve on one tap. The write, and the DM and webhook it sets off,
        wait out the toast's Undo, so an undone approval never reaches anyone."""
        from application import pending_actions
        from application.services import CrewService

        match_id = row['id']
        name = crew_member.user.preferred_name
        client = self.table_container.client
        actor = await get_user_from_session(app.storage.user)

        async def commit() -> None:
            await CrewService().update_crew_approval(
                crew_member=crew_member, crew_type=role, approved=True, actor=actor,
            )

        async def refused(error: Exception) -> None:
            if not client.is_deleted:
                with client:
                    ui.notify(f"{name} wasn't approved: {error}", color='negative')

        try:
            token = pending_actions.hold(
                ('crew', f'{role}s', crew_member.id), 'approving', commit,
                match_id=match_id, on_error=refused,
            )
        except ValueError as e:
            notify_error(e)
            return
        with self.table_container:
            undo_toast(
                f'{name} approved', token,
                caption=f'{role.capitalize()} · {match_row_label(row, with_context=False)}',
                undone=f'Approval for {name} undone.',
                on_undone=lambda: self.update_row_by_id(match_id),
            )

    async def _handle_signup_or_undo_role(self, action, role, row):
        """Handle crew signup/undo using service layer."""
        from application.services import CrewService
        user = await get_user_from_session(app.storage.user)
        if not user:
            ui.notify(f'You must be logged in to {action}.', color='warning')
            return

        match_id = row['id']
        crew_service = CrewService()

        if action == 'undo':
            mine = next(
                (c for c in (row.get(f'{role}s') or [])
                 if isinstance(c, dict) and c.get('user_id') == user.id),
                None,
            )
            was_approved = bool(mine and mine.get('approved'))

            async def perform_undo():
                try:
                    await crew_service.undo_crew_signup(match_id, user, role)
                    ui.notify(
                        f'You have been removed as a {role} for '
                        f'{match_row_label(row, with_context=False)}.',
                        color='positive',
                    )
                    await self.update_row_by_id(match_id)
                except ValueError as e:
                    ui.notify(str(e), color='warning')

            message = f'Remove yourself as a {role} for {match_row_label(row)}?'
            waiting = next((i for i in row.get('partner_invites') or []
                            if role == 'commentator' and i.get('inviter_id') == user.id), None)
            if waiting:
                message += f"\n\nYour invite to {waiting.get('invitee')} is cancelled too."
            if was_approved:
                message += ('\n\nStaff approved you for this slot — they will be told '
                            'you have withdrawn so they can find cover.')

            dialog = ConfirmationDialog(
                message,
                title=f'Withdraw as {role}',
                confirm_text='Withdraw',
                cancel_text='Cancel',
                tone='negative' if was_approved else 'primary',
                on_confirm=perform_undo,
            )
            dialog.open()

        elif action == 'signup':
            if isinstance(match_id, str) and match_id.startswith('future:'):
                await self._sign_up_ahead(match_id, user, role, row)
                return
            try:
                await crew_service.signup_crew(match_id, user, role)
                ui.notify(_signed_up_copy(role, row), color='positive')
                await self.update_row_by_id(match_id)
            except ValueError as e:
                ui.notify(str(e), color='warning')

    async def _sign_up_ahead(self, row_id: str, user, role: str, row) -> None:
        """Sign up on a bracket matchup nobody has booked; the line becomes its hold."""
        from application.services import CrewHoldService

        _, source, key = row_id.split(':')
        try:
            await CrewHoldService().signup(source, int(key), user, role)
        except ValueError as e:
            ui.notify(str(e), color='warning')
            return
        ui.notify(_signed_up_copy(role, row), color='positive')
        await self.refresh()  # type: ignore[attr-defined]

    async def _handle_invite_partner(self, row) -> None:
        from theme.dialog.partner_invite_dialog import open_partner_invite_dialog

        user = await get_user_from_session(app.storage.user)
        if not user:
            ui.notify('You must be logged in to invite a partner.', color='warning')
            return
        await open_partner_invite_dialog(row, user, lambda: self.update_row_by_id(row['id']))  # type: ignore[attr-defined]

    async def _handle_cancel_partner_invite(self, args) -> None:
        from application.services import CommentaryInviteService

        row, invite = args.get('row') or {}, args.get('invite') or {}
        user = await get_user_from_session(app.storage.user)
        if not user or not invite.get('id'):
            return

        async def cancel() -> None:
            try:
                await CommentaryInviteService().cancel(int(invite['id']), user)
            except ValueError as e:
                ui.notify(str(e), color='warning')
                return
            ui.notify(f"Invite to {invite.get('invitee')} cancelled.", color='positive')
            await self.update_row_by_id(row['id'])  # type: ignore[attr-defined]

        ConfirmationDialog(
            f"Cancel your invite to {invite.get('invitee')}? Their DM will say you took it back.",
            title='Cancel invite',
            confirm_text='Cancel invite',
            cancel_text='Keep it',
            tone='negative',
            on_confirm=cancel,
        ).open()

    async def _handle_acknowledge_crew(self, role, event, client):
        from application.services import CrewService
        with client:
            row = event.args['row']
            idx = event.args['idx']
            match_id = row['id']
            items = row.get(f'{role}s') or []
            if idx >= len(items) or not isinstance(items[idx], dict) or items[idx].get('id') is None:
                ui.notify('Page is out of date — please refresh and try again.', color='warning')
                return
            crew_id = items[idx]['id']
            user = await get_user_from_session(app.storage.user)
            if not user:
                ui.notify('You must be logged in to acknowledge.', color='warning')
                return
            try:
                await CrewService().acknowledge_crew_assignment(crew_id, role, user)
                ui.notify(
                    f'You acknowledged your {role} assignment for '
                    f'{match_row_label(row, with_context=False)}.',
                    color='positive',
                )
                await self.update_row_by_id(match_id)
            except ValueError as e:
                ui.notify(str(e), color='warning')

    def _handle_open_bracket(self, event):
        """Navigate to the bracket a scheduled game belongs to.

        The row carries the bracket id (``MatchDisplayService._bracket_ref``);
        the bare path lets ``ui.navigate.to`` prepend the tenant's root_path.
        Synchronous — a redirect needs no service call.
        """
        args = getattr(event, 'args', None)
        row = args if isinstance(args, dict) else {}
        bracket = row.get('bracket') if isinstance(row.get('bracket'), dict) else None
        if bracket and bracket.get('id'):
            ui.navigate.to(f"/brackets/{bracket['id']}")

    async def _handle_edit(self, event):
        match_id = self._event_match_id(event)
        if match_id is not None and self.on_edit:
            await self.on_edit(match_id)

    async def _handle_roll(self, event):
        match_id = self._event_match_id(event)
        if match_id is not None and self.on_generate_seed:
            await self.on_generate_seed(match_id)

    async def _handle_seat(self, event):
        match_id = self._event_match_id(event)
        if match_id is not None and self.on_seat:
            await self.on_seat(match_id)

    async def _handle_start(self, event):
        match_id = self._event_match_id(event)
        if match_id is not None and self.on_start:
            await self.on_start(match_id)

    async def _handle_finish(self, event):
        match_id = self._event_match_id(event)
        if match_id is not None and self.on_finish:
            await self.on_finish(match_id)

    async def _handle_report_winner(self, event):
        match_id = self._event_match_id(event)
        player_id = (event.args or {}).get('player')
        if match_id is not None and player_id is not None and self.on_report_winner:
            await self.on_report_winner(match_id, player_id)

    async def _handle_confirm(self, event):
        match_id = self._event_match_id(event)
        if match_id is not None and self.on_confirm:
            await self.on_confirm(match_id)

    async def _handle_edit_result(self, event):
        match_id = self._event_match_id(event)
        if match_id is not None and self.on_edit_result:
            await self.on_edit_result(match_id)

    async def _handle_assign_stations(self, event):
        match_id = self._event_match_id(event)
        if match_id is not None and self.on_assign_stations:
            await self.on_assign_stations(match_id)

    async def _handle_set_stage(self, event):
        """The Stage select wrote a value: a stage id, ``'candidate'``, or null."""
        match_id = self._event_match_id(event)
        args = getattr(event, 'args', None)
        stage = args.get('stage') if isinstance(args, dict) else None
        if match_id is not None and self.on_set_stage:
            await self.on_set_stage(match_id, stage)

    async def _handle_toggle_watch(self, event):
        row = event.args if isinstance(event.args, dict) else {}
        match_id = row.get('id')
        if match_id is None:
            return

        user = await get_user_from_session(app.storage.user)
        if not user:
            ui.notify('You must be logged in to watch a match.', color='warning')
            return

        currently_watching = bool(row.get('_watching'))
        try:
            if currently_watching:
                await self.watcher_service.unwatch(match_id, user)
                ui.notify(f'No longer watching {match_row_label(row, with_context=False)}.', color='positive')
            else:
                await self.watcher_service.watch(match_id, user)
                ui.notify(
                    f'Now watching {match_row_label(row, with_context=False)}. '
                    'You will receive Discord DMs on updates.',
                    color='positive',
                )
        except ValueError as e:
            ui.notify(str(e), color='warning')
            return

        idx = next((i for i, r in enumerate(self.table.rows) if r.get('id') == match_id), None)
        if idx is not None:
            self.table.rows[idx]['_watching'] = not currently_watching
            self.table.update()

    async def _handle_toggle_stream_volunteer(self, event):
        """A player offering their own match for stream, or taking it back.

        The success notice is where the advisory part lands: the button sits in
        the same row as the Stage column, and without it the toggle reads as
        booking a slot.
        """
        row = event.args if isinstance(event.args, dict) else {}
        match_id = row.get('id')
        if match_id is None:
            return

        user = await get_user_from_session(app.storage.user)
        if not user:
            ui.notify('You must be logged in to offer a match for stream.', color='warning')
            return

        volunteered = bool(row.get('_stream_volunteer'))
        label = match_row_label(row, with_context=False)
        try:
            if volunteered:
                await self.stream_volunteer_service.withdraw(match_id, user)
                ui.notify(f'{label} is no longer offered for stream.', color='positive')
            else:
                await self.stream_volunteer_service.volunteer(match_id, user)
                ui.notify(
                    f'{label} is offered for stream. Staff see this when they plan '
                    'coverage — it does not guarantee the match goes out.',
                    color='positive',
                )
        except ValueError as e:
            ui.notify(str(e), color='warning')
            return

        idx = next((i for i, r in enumerate(self.table.rows) if r.get('id') == match_id), None)
        if idx is not None:
            self.table.rows[idx]['_stream_volunteer'] = not volunteered
            names = list(self.table.rows[idx].get('stream_volunteers') or [])
            name = user.preferred_name
            if volunteered:
                names = [n for n in names if n != name]
            elif name not in names:
                names.append(name)
            self.table.rows[idx]['stream_volunteers'] = names
            self.table.update()

    async def _handle_request_reschedule(self, event):
        """A player asking staff to move or call off their own match.

        Opens the request dialog rather than doing anything: the ask needs a
        reason and possibly a time, and neither belongs in a row button.
        """
        row = event.args if isinstance(event.args, dict) else {}
        match_id = row.get('id')
        if match_id is None:
            return

        user = await get_user_from_session(app.storage.user)
        if not user:
            ui.notify('You must be logged in to ask for a change.', color='warning')
            return

        match = await self.service.get_by_id(match_id)
        if match is None:
            ui.notify('That match is no longer around.', color='warning')
            return

        async def after():
            idx = next(
                (i for i, r in enumerate(self.table.rows) if r.get('id') == match_id), None
            )
            if idx is not None:
                self.table.rows[idx]['_can_reschedule'] = False
                self.table.rows[idx]['_reschedule_pending'] = True
                self.table.update()

        from theme.dialog.reschedule_request_dialog import RescheduleRequestDialog

        await RescheduleRequestDialog(match, user, on_submit=after).open()

    def _set_my_late(self, match_id: int, tag: str) -> None:
        idx = next((i for i, r in enumerate(self.table.rows) if r.get('id') == match_id), None)  # type: ignore[attr-defined]
        if idx is None:
            return
        row = self.table.rows[idx]  # type: ignore[attr-defined]
        row['_my_late'] = tag
        row['_can_late'] = not tag
        if tag:
            row['_status'], row['_tone'] = f'Running {tag}', 'pending'
        else:
            from theme.tables.match_player_run_sheet import player_status
            row['_status'], row['_tone'] = player_status(row)
        self.table.update()  # type: ignore[attr-defined]

    async def _handle_running_late(self, event):
        """A player telling staff they're on their way: opens the minutes picker."""
        row = event.args if isinstance(event.args, dict) else {}
        match_id = row.get('id')
        if match_id is None:
            return
        user = await get_user_from_session(app.storage.user)
        if not user:
            ui.notify('You must be logged in to tell staff you are late.', color='warning')
            return
        match = await self.service.get_by_id(match_id)
        if match is None:
            ui.notify('That match is no longer around.', color='warning')
            return

        from application.utils.discord_messages_volunteer import LATE_CHOICES
        from theme.dialog.running_late_dialog import RunningLateDialog

        async def sent(minutes: int) -> None:
            tag = f'{minutes}+ min' if minutes >= LATE_CHOICES[-1] else f'~{minutes} min'
            self._set_my_late(match_id, tag)

        async def ask() -> None:
            await self._handle_request_reschedule(event)

        can_ask = bool(row.get('_can_reschedule'))
        await RunningLateDialog(
            match, user, on_sent=sent, on_ask_to_change=ask if can_ask else None,
        ).open()

    async def _handle_take_back_late(self, event):
        row = event.args if isinstance(event.args, dict) else {}
        match_id = row.get('id')
        if match_id is None:
            return
        user = await get_user_from_session(app.storage.user)
        if not user:
            return
        try:
            await self.player_late_service.take_back_late(match_id, user)
        except ValueError as e:
            ui.notify(str(e), color='warning')
            return
        ui.notify("Done. Staff won't see you as running late.", color='positive')
        self._set_my_late(match_id, '')

    async def _handle_seed_ready(self, event):
        await self._set_seed_ready(event, ready=True)

    async def _handle_seed_not_ready(self, event):
        await self._set_seed_ready(event, ready=False)

    async def _set_seed_ready(self, event, *, ready):
        """A player's Ready for seed, or taking it back. The row follows on the
        ``match_live`` refresh the service publishes."""
        row = event.args if isinstance(event.args, dict) else {}
        match_id = row.get('id')
        if match_id is None:
            return
        user = await get_user_from_session(app.storage.user)
        if not user:
            ui.notify('You must be logged in to say you are ready.', color='warning')
            return
        try:
            if ready:
                result = await self.seed_ready_service.set_ready(match_id, user)
            else:
                result = await self.seed_ready_service.set_not_ready(match_id, user)
        except ValueError as e:
            ui.notify(str(e), color='warning')
            return
        ui.notify(result.message, color='positive' if result.ok else 'warning')

    async def _handle_open_hard_preset(self, event):
        """A player opening their private harder-settings choice.

        Opens the dialog rather than toggling: the choice depends on an answer
        the player is not allowed to see, so it needs the explanation that comes
        with it. The refreshed row afterwards is the viewer's own state only —
        ``board_states`` never returns anybody else's.
        """
        row = event.args if isinstance(event.args, dict) else {}
        match_id = row.get('id')
        if match_id is None:
            return

        user = await get_user_from_session(app.storage.user)
        if not user:
            ui.notify('You must be logged in to choose your settings.', color='warning')
            return

        match = await self.service.get_by_id(match_id)
        if match is None:
            ui.notify('That match is no longer around.', color='warning')
            return

        states = await self.hard_preset_service.board_states(user, [match_id])
        state = states.get(match_id)
        if state is None:
            ui.notify(
                'This match does not offer a harder preset any more.', color='warning',
            )
            return

        async def after():
            refreshed = await self.hard_preset_service.board_states(user, [match_id])
            idx = next(
                (i for i, r in enumerate(self.table.rows) if r.get('id') == match_id), None
            )
            if idx is not None:
                apply_hard_preset_state(self.table.rows[idx], refreshed.get(match_id))
                self.table.update()

        from theme.dialog.hard_preset_dialog import HardPresetDialog

        await HardPresetDialog(match, user, state, on_change=after).open()


def _signed_up_copy(role: str, row: dict) -> str:
    label = match_row_label(row, with_context=False)
    if row.get('crew_hold'):
        return (f"Signed up as a {role} for {label}. It isn't booked yet: your signup "
                "moves onto it when it is, and you'll get a Discord message once staff approve it.")
    return f"Signed up as a {role} for {label}. You'll get a Discord message once staff approve it."
