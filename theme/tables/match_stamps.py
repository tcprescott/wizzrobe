"""The per-viewer fields a match board stamps on its rows, and the ADA popup.

What the shared display rows can't carry because it depends on who is looking:
watched and stream-offered matches, reschedule and harder-preset state, comment
counts and late notes on staff boards, and arranged accommodations. Each is one
bulk lookup per refresh, skipped on a board that doesn't draw it.
``_restamp_shared_state`` redoes the ones other people's actions change for a
single row arriving from ``match_live``.

``MatchTableView`` mixes this in.
"""

from typing import Any

from nicegui import app, ui

from application.services import MatchCommentService, get_user_from_session
from theme.dialog._helpers import form_dialog
from theme.tables.hard_preset_rows import apply_hard_preset_state
from theme.tables.match_player_run_sheet import keep_own_late_only
from theme.tables.match_slots import MY_ACTIONS_COLUMN


class MatchRowStampsMixin:
    """Per-viewer row fields for a ``MatchTableView``."""

    columns: Any
    access: Any
    show_accommodations: bool
    watcher_service: Any
    stream_volunteer_service: Any
    accommodation_service: Any
    hard_preset_service: Any
    reschedule_service: Any

    async def _fetch_watched_ids(self) -> set:
        user = await get_user_from_session(app.storage.user)
        if not user:
            return set()
        return set(await self.watcher_service.list_watched_match_ids(user))

    async def _fetch_stream_volunteered_ids(self) -> set:
        """Matches this viewer has offered for stream — the toggle's own state.

        Separate from the row's ``stream_volunteers`` names, which are everyone's
        and are what staff read.
        """
        user = await get_user_from_session(app.storage.user)
        if not user:
            return set()
        return set(await self.stream_volunteer_service.list_volunteered_match_ids(user))

    async def _apply_comment_counts(self, rows) -> None:
        """Stamp ``comment_counts`` on each row, in one query, on a staff board."""
        if not (getattr(self, 'show_comment_counts', False) and self.access.run):
            return
        counts = await MatchCommentService().counts_by_match([row.get('id') for row in rows])
        for row in rows:
            row['comment_counts'] = counts.get(row.get('id'))

    def _apply_late_visibility(self, rows) -> None:
        """Keep who's running late on staff boards; elsewhere only the viewer's own.

        The heads-up is between a player and staff, so the opponent's row (and
        everyone's on Event > Schedule) must not carry it to the browser.
        """
        if getattr(self, 'show_comment_counts', False) and self.access.run:
            return
        keep_own_late_only(rows, getattr(self, '_discord_id', None))

    async def _apply_accommodations(self, rows) -> None:
        """Mark each player with an arranged ADA accommodation, in one query.

        Sets ``ada`` and ``ada_note`` on the player dicts the players cell
        renders. Nothing is set on a board that has not opted in, or for a
        viewer the service does not admit (it returns ``{}``). For a proctor
        the service withholds the note (``None``), and so does this: the
        popup is told so server-side rather than trusting what the row says.
        """
        if not self.show_accommodations:
            return
        user_ids = {p.get('user_id') for row in rows for p in row.get('players') or []}
        actor = await get_user_from_session(app.storage.user)
        notes = await self.accommodation_service.arranged_notes_for(actor, user_ids)
        if not notes:
            return
        self._ada_notes_readable = all(n is not None for n in notes.values())
        for row in rows:
            for player in row.get('players') or []:
                if player.get('user_id') in notes:
                    player['ada'] = True
                    player['ada_note'] = notes[player['user_id']]

    def _show_accommodation(self, args) -> None:
        """The popup behind a player's accessibility icon.

        Staff get the staff notes. A proctor gets that it's arranged and who to
        ask: the notes are written under a "only staff can read these" promise.
        """
        name = (args or {}).get('name') or 'This player'
        note = (args or {}).get('note')
        readable = getattr(self, '_ada_notes_readable', False) and note is not None
        with form_dialog(f'ADA accommodation: {name}', card_classes='dialog-card dialog-card--sm') as dialog:
            if readable:
                ui.label('Staff notes').classes('subsection-title q-mt-sm')
                # Plain text, never markup: staff wrote it.
                ui.label(note or 'Staff marked this arranged but left no notes.') \
                    .classes('text-body2').style('white-space: pre-wrap')
            else:
                ui.label(
                    f'Staff have arranged an accommodation for {name}. Check with '
                    'staff for what it involves before the match starts.'
                ).classes('text-body2 q-mt-sm')
        dialog.open()

    def _shows_column(self, name: str) -> bool:
        """Whether the board renders ``name``'s control, on its own or inside
        the player's combined "Your actions" cell (``MY_ACTIONS_COLUMN``)."""
        names = {c.get('name') for c in self.columns}
        return name in names or MY_ACTIONS_COLUMN in names

    async def _fetch_hard_preset_states(self, rows) -> dict:
        """This viewer's own hard-preset state for every visible row, in bulk.

        Skipped entirely on a board with no ``hard_preset`` column — the admin
        schedule and the proctor station render matches nobody is choosing
        settings on, so the queries would buy nothing.
        """
        if not self._shows_column('hard_preset'):
            return {}
        user = await get_user_from_session(app.storage.user)
        if not user:
            return {}
        match_ids = [row.get('id') for row in rows if row.get('id') is not None]
        return await self.hard_preset_service.board_states(user, match_ids)

    async def _fetch_reschedule_state(self) -> tuple:
        """``(requestable ids, already-asked ids)`` for this viewer, in bulk.

        Two sets rather than one flag because they drive different marks: the
        first shows the Ask button, the second shows the "Asked" chip on a row
        where the button is gone precisely *because* they already asked.

        Skipped entirely on a board with no ``reschedule`` column — the admin
        schedule, the proctor station and the home schedule all render matches
        and none of them offers the ask, so the two queries would buy nothing.
        """
        if not self._shows_column('reschedule'):
            return set(), set()
        user = await get_user_from_session(app.storage.user)
        if not user:
            return set(), set()
        requestable = await self.reschedule_service.list_requestable_match_ids(user)
        asked = set(await self.reschedule_service.pending_match_ids_for_user(user))
        return requestable, asked

    async def _restamp_shared_state(self, row) -> None:
        """Recompute the per-viewer fields other people's actions change.

        The reschedule pair and the harder-preset fields (``RESTAMPED_ROW_FIELDS``)
        for this one row, through the same bulk lookups ``refresh`` uses: staff
        approving a move is exactly the ``match_live`` push that lands here.
        """
        if self._shows_column('reschedule'):
            requestable_ids, asked_ids = await self._fetch_reschedule_state()
            row['_can_reschedule'] = row.get('id') in requestable_ids
            row['_reschedule_pending'] = row.get('id') in asked_ids
        if self._shows_column('hard_preset'):
            states = await self._fetch_hard_preset_states([row])
            apply_hard_preset_state(row, states.get(row.get('id')))
