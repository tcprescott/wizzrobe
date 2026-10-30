"""Match notification mixin: the Discord DM / embed fan-out for a match.

Split out of ``match_schedule_service.py`` as pure code motion — that module had
grown past the 800-line budget after absorbing the bracket integration. Follows
the ``_bracket/`` mixin convention: ``MatchNotificationMixin`` is composed into
:class:`MatchScheduleService`, and its methods reach ``self.discord_service`` /
``self.acknowledgment_repository`` and sibling notify methods through that one
composed class, so every caller keeps using ``MatchScheduleService`` unchanged.

Every public method here is best-effort: a DM failure is logged and swallowed so
it can never block the lifecycle operation that triggered it.
"""

import logging
from typing import Callable, Optional, Sequence

import discord

from application.repositories import MatchAcknowledgmentRepository
from application.services import notification_links
from application.services.discord import DiscordService, discord_queue
from application.services.event_service import EventService
from application.services.match._dm_context import _community_name, bracket_line_for
from application.services.match._match_recipients import (
    MatchRecipient,
    collect_match_audience,
    collect_match_recipients,
)
from application.services.match.match_hard_preset_service import MatchHardPresetService
from application.services.notification_catalog import (
    CREW,
    FOLLOWERS,
    MATCH_CANCELLED,
    MATCH_SCHEDULED,
    PLAYERS,
    REMOVED_FROM_MATCH,
    STAGE_CALL,
    STAGE_CLEARED,
    STAGE_REMINDER,
    STREAM_CANDIDATE,
    WATCHERS,
    Notice,
    NoticeTarget,
)
from application.tenant_context import require_tenant_id
from application.utils.discord_embeds import (
    COLOR_RESCHEDULED,
    COLOR_SCHEDULED,
    COLOR_STREAM,
    match_embed,
    stage_embed,
    time_field,
)
from application.utils.discord_messages import (
    AUDIENCE_CREW,
    AUDIENCE_PLAYER,
    AUDIENCE_SUBSCRIBER,
    AUDIENCE_WATCHER,
    DMLink,
    acknowledgment_request_dm,
    crew_slot_line,
    crew_slot_title,
    rescheduled_dm,
    scheduled_dm,
    stage_assigned_dm,
    stage_cleared_dm,
    stage_reminder_dm,
    stream_candidate_dm,
)
from models import Match, MatchPlayers

logger = logging.getLogger(__name__)


async def _wants_broadcaster(match: Match) -> bool:
    """Whether the crew buttons offer Broadcaster; a failed lookup drops the button, never the DM."""
    try:
        return await EventService.match_wants_broadcaster(match)
    except Exception:
        logger.exception("broadcaster lookup failed for match %s", match.id)
        return False

_AUDIENCES = (AUDIENCE_PLAYER, AUDIENCE_CREW, AUDIENCE_WATCHER)
_RECIPIENTS = {AUDIENCE_PLAYER: PLAYERS, AUDIENCE_CREW: CREW, AUDIENCE_WATCHER: WATCHERS}


def _target(notice: Notice, recipient: MatchRecipient) -> NoticeTarget:
    return notice.to(_RECIPIENTS[recipient.audience])


def _for_crew(
    crew_roles: Sequence[str], message: str, embed: Optional[discord.Embed],
) -> tuple[str, Optional[discord.Embed]]:
    """Shared match copy and card, retold to a crew member naming their slot.

    Anyone without a slot gets both back untouched; the card is copied so one
    recipient's title never leaks into the next one's DM.
    """
    if not crew_roles:
        return message, embed
    card = None
    if embed is not None:
        card = embed.copy()
        card.title = crew_slot_title(embed.title or '', crew_roles)
    return crew_slot_line(message, crew_roles), card


class MatchNotificationMixin:
    """Discord DM fan-out for match lifecycle and subscriber notifications."""

    acknowledgment_repository: MatchAcknowledgmentRepository
    discord_service: DiscordService

    async def notify_match_participants(
        self, match: Match, message: str, embed: Optional[discord.Embed] = None,
        *, notice: Notice, player_copies: Optional[dict] = None,
        skip_discord_ids: frozenset = frozenset(),
        seed_ready_buttons: bool = False,
    ) -> None:
        """
        Send a DM to all opted-in players, approved crew, and watchers for a match.

        Each recipient gets exactly one DM. Anyone who is watching the match
        (whether or not they are also a player/crew) receives the DM with an
        Unwatch button so they can opt out from Discord. ``embed`` is the
        colour-coded card rendered on Discord; the ``message`` text still feeds
        the web-push mirror. ``notice`` is the catalogued message, whose
        community setting decides per audience whether each copy pings, goes
        silent, or isn't sent. ``player_copies`` maps a player's Discord id to their own ``(text,
        embed)``, used in place of the shared one. ``seed_ready_buttons`` puts
        Ready for seed on every player's copy.

        Never raises; partial DM failures are logged and swallowed so the
        calling lifecycle operation is never blocked.
        """
        try:
            recipients = await collect_match_audience(match)
            for discord_id, recipient in recipients.items():
                if discord_id in skip_discord_ids:
                    continue
                is_player = recipient.audience == AUDIENCE_PLAYER
                if player_copies and is_player and discord_id in player_copies:
                    text, card = player_copies[discord_id]
                else:
                    text, card = _for_crew(recipient.crew_roles, message, embed)
                ready = seed_ready_buttons and is_player
                await self._send_one(
                    match, discord_id, recipient, text, embed=card,
                    link=await notification_links.player_match(match.id) if ready else None,
                    log_label='notify_match_participants', notice=_target(notice, recipient),
                    seed_ready_button=ready,
                )
        except Exception:
            logger.exception("notify_match_participants unexpected error for match %s", match.id)

    async def notify_match_crew(
        self,
        match: Match,
        message: str,
        embed: Optional[discord.Embed] = None,
        *,
        watcher_message: Optional[str] = None,
        crew_message: Optional[Callable[[Sequence[str]], str]] = None,
    ) -> None:
        """
        Send a DM to approved crew and watchers for a match.

        Players are excluded — they receive a separate acknowledgment DM via
        notify_acknowledgment_request. ``message`` is the crew copy and carries a
        button to their slot; ``watcher_message`` (defaulting to ``message``) is
        what someone who only watches reads, since "a match you're on crew for"
        is false for them. Watchers get the Unwatch button. ``embed`` is the
        colour-coded Discord card; a crew member's copy names their slot, with
        ``crew_message(crew_roles)`` (when given) writing their text. Never
        raises; per-DM failures are logged and swallowed.
        """
        try:
            recipients = await collect_match_audience(
                match, include_players=False, exclude_players=True,
            )
            if not recipients:
                return
            crew_link = await notification_links.crew_match(match.id)
            for discord_id, recipient in recipients.items():
                if recipient.audience == AUDIENCE_WATCHER:
                    await self._send_one(
                        match, discord_id, recipient, watcher_message or message,
                        embed=embed, link=None, log_label='notify_match_crew',
                        notice=MATCH_SCHEDULED.to(WATCHERS),
                    )
                    continue
                roles = recipient.crew_roles
                text, card = _for_crew(roles, message, embed)
                if crew_message is not None:
                    text = crew_message(roles)
                await self._send_one(
                    match, discord_id, recipient, text,
                    embed=card, link=crew_link, log_label='notify_match_crew',
                    notice=MATCH_SCHEDULED.to(CREW),
                )
        except Exception:
            logger.exception("notify_match_crew unexpected error for match %s", match.id)

    async def notify_stage_changed(self, match: Match) -> None:
        """Tell a match's players, approved crew and watchers that its stage changed.

        Reads the stage back off the match rather than taking a name, because
        ``discord_queue`` awaits this after the caller has returned: the row is
        already written, and the relation is the one source of truth for what it
        now says. No stage means it was cleared, and that branch is not
        optional — someone told they are on Kraid and never told otherwise walks
        to an empty stage. Watchers are in the audience too: they asked to
        follow this match, and where it is played is part of following it.

        Each audience gets its own copy and button (see :meth:`_notify_stage`).
        Never raises; per-DM failures are logged and swallowed. Publishes no
        event of its own: the DM is a consequence of the stage change, which
        ``MatchService.assign_stage`` already audits and publishes.
        """
        try:
            await match.fetch_related('stage')
        except Exception:
            logger.exception('notify_stage_changed could not load the stage for match %s', match.id)
            return
        stage_name = match.stage.name if match.stage else ''
        if stage_name:
            await self._notify_stage(
                match, STAGE_CALL,
                build=lambda names, when, audience, roles: stage_assigned_dm(
                    match.tournament.name, stage_name, when,
                    player_names=names, audience=audience, crew_roles=roles,
                ),
                stage_name=stage_name,
                titles={
                    AUDIENCE_PLAYER: '📺 You are on stage',
                    AUDIENCE_CREW: '📺 Your crew slot is on stage',
                    AUDIENCE_WATCHER: '📺 A match you watch is on stage',
                },
                descriptions={
                    AUDIENCE_PLAYER: 'Play at the stage rather than in the tournament room.',
                    AUDIENCE_CREW: "The match is on stage, so that's where you'll crew it.",
                    AUDIENCE_WATCHER: 'It will be played at the stage.',
                },
                log_label='notify_stage_changed',
            )
            return
        await self._notify_stage(
            match, STAGE_CLEARED,
            build=lambda names, when, audience, roles: stage_cleared_dm(
                match.tournament.name, when, player_names=names, audience=audience,
                crew_roles=roles,
            ),
            stage_name='',
            titles=dict.fromkeys(_AUDIENCES, '📺 Stage call cancelled'),
            descriptions=dict.fromkeys(
                _AUDIENCES, 'This match is back in the tournament room.',
            ),
            log_label='notify_stage_changed',
            include_unstaged_crew=True,
        )

    async def notify_stage_reminder(self, match: Match, *, stage_name: str) -> None:
        """The pre-match nudge, fanned out to the same audience as the assignment.

        Sent by ``application/services/match/stage_reminder.py``, which owns the
        timing and the once-only stamp; this method owns the copy and the
        fan-out. Never raises, and publishes no event — a reminder observes a
        match nobody changed.
        """
        await self._notify_stage(
            match, STAGE_REMINDER,
            build=lambda names, when, audience, roles: stage_reminder_dm(
                match.tournament.name, stage_name, when,
                player_names=names, audience=audience, crew_roles=roles,
            ),
            stage_name=stage_name,
            titles={
                AUDIENCE_PLAYER: '⏰ Your stage match is coming up',
                AUDIENCE_CREW: '⏰ Your crew slot on stage is coming up',
                AUDIENCE_WATCHER: '⏰ A match you watch is coming up on stage',
            },
            descriptions={
                AUDIENCE_PLAYER: "Head to the stage now. Running late? Tap **Tell staff I'm late**.",
                AUDIENCE_CREW: 'Head to the stage now.',
                AUDIENCE_WATCHER: 'It starts soon.',
            },
            log_label='notify_stage_reminder',
            late_button=True,
        )

    @staticmethod
    async def _audience_links(match_id: int) -> dict[str, Optional[DMLink]]:
        """Where each audience's button goes — somewhere it can act or read.

        A player lands on their own board narrowed to the match; crew on their
        slot, which is the thing they are being reminded about; a watcher on the
        community schedule, the one board that lists a match they neither play
        nor crew. Sending all three to the player filter (as this used to) gave
        crew and watchers "No matches to show yet".
        """
        return {
            AUDIENCE_PLAYER: await notification_links.player_match(match_id),
            AUDIENCE_CREW: await notification_links.crew_match(match_id),
            AUDIENCE_WATCHER: await notification_links.community_schedule(),
        }

    async def _notify_stage(
        self,
        match: Match,
        notice: Notice,
        *,
        build,
        stage_name: str,
        titles: dict[str, str],
        descriptions: dict[str, str],
        log_label: str,
        late_button: bool = False,
        include_unstaged_crew: bool = False,
    ) -> None:
        """Shared body of the three stage DMs: one audience, per-audience copy.

        ``build(player_names, scheduled_display, audience, crew_roles)`` returns
        the text; ``titles``/``descriptions`` are the card's, keyed by audience,
        and a crew member's title names their slot.
        """
        try:
            await match.fetch_related('tournament', 'players__user')
            player_names = [p.user.preferred_name for p in match.players]  # type: ignore[attr-defined]
            when = time_field(match.scheduled_at)
            community = await _community_name()
            links = await self._audience_links(match.id)
            recipients = await collect_match_audience(
                match, include_unstaged_crew=include_unstaged_crew,
            )
            for discord_id, recipient in recipients.items():
                audience = recipient.audience
                embed = stage_embed(
                    title=crew_slot_title(titles[audience], recipient.crew_roles),
                    tournament=match.tournament.name,
                    community_name=community,
                    player_names=player_names, when=match.scheduled_at,
                    stage_name=stage_name or None, description=descriptions[audience],
                )
                await self._send_one(
                    match, discord_id, recipient,
                    build(player_names, when, audience, recipient.crew_roles),
                    embed=embed, link=links.get(audience), log_label=log_label,
                    notice=_target(notice, recipient),
                    late_button=late_button and audience == AUDIENCE_PLAYER,
                )
        except Exception:
            logger.exception("%s unexpected error for match %s", log_label, match.id)

    async def notify_match_cancelled(
        self,
        recipients: dict[int, MatchRecipient],
        message: str,
        embed: Optional[discord.Embed] = None,
        *,
        notice: Notice = MATCH_CANCELLED,
    ) -> None:
        """DM a **pre-resolved** recipient set that their match was cancelled.

        Unlike every sibling notifier this takes the recipients rather than a
        ``Match``, and that difference is load-bearing. Cancellation deletes the
        row, and ``collect_match_recipients`` re-queries ``MatchPlayers`` /
        ``Commentator`` / ``Tracker`` / ``MatchWatcher`` — all of which cascade
        away with it. Because ``discord_queue`` defers this coroutine to its lone
        worker, by the time it ran the recipient set would be empty and the DMs
        would silently reach nobody. The caller resolves recipients while the
        match still exists and passes them in; this method only fans out.

        Every recipient gets a plain DM — deliberately not the watcher variant,
        whose Unwatch button would carry the id of a match that no longer exists.
        Crew read which slot they held.

        Never raises; per-DM failures are logged and swallowed.
        """
        for discord_id, recipient in recipients.items():
            try:
                text, card = _for_crew(recipient.crew_roles, message, embed)
                success, err = await self.discord_service.send_dm(
                    discord_id, text, embed=card, notice=_target(notice, recipient),
                )
                if not success:
                    logger.warning(
                        "notify_match_cancelled DM failed for %s: %s", discord_id, err
                    )
            except Exception:
                logger.exception(
                    "notify_match_cancelled unexpected error for %s", discord_id
                )

    async def notify_players_removed(
        self,
        discord_ids: list[int],
        message: str,
        embed: Optional[discord.Embed] = None,
    ) -> None:
        """DM players staff took off a match. They are no longer in its line-up,
        so ``collect_match_recipients`` can't find them; the caller passes them.

        Never raises; per-DM failures are logged and swallowed.
        """
        for discord_id in discord_ids:
            try:
                success, err = await self.discord_service.send_dm(
                    discord_id, message, embed=embed, notice=REMOVED_FROM_MATCH.to(PLAYERS),
                )
                if not success:
                    logger.warning(
                        "notify_players_removed DM failed for %s: %s", discord_id, err
                    )
            except Exception:
                logger.exception(
                    "notify_players_removed unexpected error for %s", discord_id
                )

    async def _send_one(
        self,
        match: Match,
        discord_id: int,
        recipient: MatchRecipient,
        message: str,
        *,
        embed: Optional[discord.Embed],
        link: Optional[DMLink],
        log_label: str,
        notice: NoticeTarget,
        late_button: bool = False,
        seed_ready_button: bool = False,
    ) -> None:
        """One DM of a per-audience fan-out; a failure is logged, not raised.

        ``late_button`` puts Tell staff I'm late on a player's copy (the stage
        reminder), ``seed_ready_button`` Ready for seed (the check-in DM).
        """
        try:
            if seed_ready_button:
                success, err = await self.discord_service.send_dm_with_seed_ready_button(
                    discord_id, message, match.id, embed=embed, link=link, notice=notice,
                )
            elif late_button:
                success, err = await self.discord_service.send_dm_with_player_late_button(
                    discord_id, message, match.id, embed=embed, link=link, notice=notice,
                )
            elif recipient.is_watcher:
                success, err = await self.discord_service.send_dm_with_unwatch_button(
                    discord_id, message, match.id, embed=embed, link=link, notice=notice,
                )
            else:
                success, err = await self.discord_service.send_dm(
                    discord_id, message, embed=embed, link=link, notice=notice,
                )
        except Exception:
            logger.exception("%s DM raised for %s", log_label, discord_id)
            return
        if not success:
            logger.warning("%s DM failed for %s: %s", log_label, discord_id, err)

    async def notify_acknowledgment_request(
        self,
        match: Match,
        *,
        rescheduled: bool,
        community: str = '',
        bracket_line: str = '',
    ) -> None:
        """
        Send a DM with an Acknowledge button to every current match player
        whose acknowledgment is still pending and who opts in to DMs.

        ``community`` is the embed-footer community name and ``bracket_line`` the
        series context, both resolved by the caller in request context — this
        coroutine is awaited later by the scope-less ``discord_queue`` worker,
        where the tenant is no longer in scope, so they must be passed in rather
        than looked up here.

        Never raises; per-DM failures are logged and swallowed.
        """
        try:
            await match.fetch_related('tournament', 'stage')
            scheduled_display = time_field(match.scheduled_at)
            players = await MatchPlayers.filter(match=match, tenant_id=require_tenant_id()).prefetch_related('user')
            player_names = [p.user.preferred_name for p in players] + list(getattr(match, 'bracket_placeholders', None) or [])
            message = acknowledgment_request_dm(
                match.tournament.name, scheduled_display,
                rescheduled=rescheduled,
                stage_name=match.stage.name if match.stage else '',
                player_names=player_names,
                bracket_line=bracket_line,
            )
            embed = match_embed(
                title='🔄 Match rescheduled' if rescheduled else '📣 Match scheduled',
                color=COLOR_RESCHEDULED if rescheduled else COLOR_SCHEDULED,
                description='Tap **Acknowledge** below to confirm you have seen this.',
                tournament=match.tournament.name, community_name=community,
                player_names=player_names, when=match.scheduled_at,
                stage_name=match.stage.name if match.stage else None,
            )

            # Beside the Acknowledge button, not instead of it: someone who
            # cannot make the time needs the match, and "reply to an admin" is
            # not a route. The match itself rather than the whole board, where
            # it is one row of fifteen and its Ask to change sits on that row.
            link = await notification_links.player_match(match.id)
            acks = await self.acknowledgment_repository.list_for_match(match)
            for ack in acks:
                if ack.acknowledged_at is not None:
                    continue
                if not ack.user.discord_id:
                    continue
                success, err = await self.discord_service.send_dm_with_acknowledgment_button(
                    ack.user.discord_id, message, match.id, embed=embed, link=link,
                    notice=MATCH_SCHEDULED.to(PLAYERS),
                )
                if not success:
                    logger.warning(
                        "notify_acknowledgment_request DM failed for %s: %s", ack.user.discord_id, err
                    )
        except Exception:
            logger.exception("notify_acknowledgment_request unexpected error for match %s", match.id)

    async def notify_tournament_subscribers_scheduled(
        self,
        match: Match,
        message: str,
        exclude_discord_ids: list,
        embed: Optional[discord.Embed] = None,
    ) -> None:
        """
        Send match-scheduled DM with crew signup buttons to tournament subscribers.

        ``embed`` is the colour-coded Discord card; the ``message`` text still
        feeds the web-push mirror. Never raises; per-DM failures are logged and
        swallowed.
        """
        try:
            from application.repositories import TournamentNotificationRepository
            has_stage = match.stage_id is not None
            subscribers = await TournamentNotificationRepository().get_match_notification_subscribers(
                match.tournament_id, has_stage=has_stage
            )
            link = await notification_links.community_schedule()
            broadcaster = await _wants_broadcaster(match)
            for user in subscribers:
                if user.discord_id not in exclude_discord_ids:
                    success, err = await self.discord_service.send_dm_with_crew_buttons(
                        user.discord_id, message, match.id, embed=embed, link=link,
                        notice=MATCH_SCHEDULED.to(FOLLOWERS), broadcaster=broadcaster,
                    )
                    if not success:
                        logger.warning(
                            "notify_tournament_subscribers_scheduled DM failed for %s: %s",
                            user.discord_id, err,
                        )
        except Exception:
            logger.exception(
                "notify_tournament_subscribers_scheduled unexpected error for match %s", match.id
            )

    async def notify_stream_candidate_subscribers(
        self,
        match: Match,
        exclude_discord_ids: list,
        community: str = '',
    ) -> None:
        """
        Send stream-candidate alert with crew signup buttons to opted-in subscribers.

        Skipped entirely when the match already has a stage — those subscribers
        were already notified via notify_tournament_subscribers_scheduled.

        ``community`` is the embed-footer community name, resolved by the caller in
        request context (this runs in the scope-less ``discord_queue`` worker where
        the tenant is no longer in scope). Never raises; per-DM failures are logged
        and swallowed.
        """
        if match.stage_id is not None:
            return

        try:
            from application.repositories import TournamentNotificationRepository
            subscribers = await TournamentNotificationRepository().get_stream_candidate_subscribers(
                match.tournament_id
            )
            await match.fetch_related('tournament', 'players__user')
            scheduled_display = time_field(match.scheduled_at)
            player_names = [p.user.preferred_name for p in match.players]
            msg = stream_candidate_dm(
                match.tournament.name, scheduled_display,
                player_names=player_names,
            )
            embed = match_embed(
                title='🎥 Stream candidate', color=COLOR_STREAM,
                description='This match may be streamed — sign up to crew below.',
                tournament=match.tournament.name, community_name=community,
                player_names=player_names, when=match.scheduled_at,
            )
            # The signup buttons cover the crew roles; the link covers
            # everything else the reader might want to decide first — who is
            # playing, what else is on that day.
            link = await notification_links.community_schedule()
            broadcaster = await _wants_broadcaster(match)
            for user in subscribers:
                if user.discord_id not in exclude_discord_ids:
                    success, err = await self.discord_service.send_dm_with_crew_buttons(
                        user.discord_id, msg, match.id, embed=embed, link=link,
                        notice=STREAM_CANDIDATE.to(FOLLOWERS), broadcaster=broadcaster,
                    )
                    if not success:
                        logger.warning(
                            "notify_stream_candidate_subscribers DM failed for %s: %s",
                            user.discord_id, err,
                        )
        except Exception:
            logger.exception(
                "notify_stream_candidate_subscribers unexpected error for match %s", match.id
            )

    async def notify_match_scheduled(
        self,
        match: Match,
        *,
        rescheduled: bool = False,
        is_stream_candidate: bool = False,
    ) -> None:
        """Fan out the scheduled/rescheduled notifications for a match.

        Loads the tournament/players/stage relations and computes the
        already-notified exclude list inline, then enqueues (in order): the
        per-player acknowledgment request, the crew DM, the tournament-subscriber
        signup DMs, and — only for a brand-new stream candidate — the
        stream-candidate subscriber DMs. Shared by create/update/request flows.
        """
        await match.fetch_related('tournament', 'players__user', 'stage')
        # A booking made before the bracket decided its players names the empty sides.
        player_names = [p.user.preferred_name for p in match.players] + list(getattr(match, 'bracket_placeholders', None) or [])
        build_message = rescheduled_dm if rescheduled else scheduled_dm
        bracket_line = await bracket_line_for(match.id)

        # Players are told by the acknowledgment request; everyone else hears it
        # in copy that is true for them (see discord_messages.AUDIENCE_*).
        def message_for(audience: str, crew_roles: Sequence[str] = ()) -> str:
            return build_message(
                match.tournament.name,
                time_field(match.scheduled_at),
                player_names=player_names,
                stage_name=match.stage.name if match.stage else '',
                bracket_line=bracket_line,
                audience=audience,
                crew_roles=crew_roles,
            )

        community = await _community_name()
        embed = match_embed(
            title='🔄 Match rescheduled' if rescheduled else '📣 Match scheduled',
            color=COLOR_RESCHEDULED if rescheduled else COLOR_SCHEDULED,
            tournament=match.tournament.name, community_name=community,
            player_names=player_names, when=match.scheduled_at,
            stage_name=match.stage.name if match.stage else None,
        )
        discord_queue.enqueue(self.notify_acknowledgment_request(
            match, rescheduled=rescheduled, community=community,
            bracket_line=bracket_line,
        ))
        discord_queue.enqueue(self.notify_match_crew(
            match, message_for(AUDIENCE_CREW), embed,
            watcher_message=message_for(AUDIENCE_WATCHER),
            crew_message=lambda roles: message_for(AUDIENCE_CREW, roles),
        ))
        discord_queue.enqueue(MatchHardPresetService().send_offer(match))

        notified_ids = await self._collect_notified_discord_ids(match)
        discord_queue.enqueue(self.notify_tournament_subscribers_scheduled(
            match, message_for(AUDIENCE_SUBSCRIBER), notified_ids, embed,
        ))
        if is_stream_candidate:
            discord_queue.enqueue(self.notify_stream_candidate_subscribers(match, notified_ids, community))

    async def notify_stream_candidate(self, match: Match) -> None:
        """Enqueue stream-candidate subscriber DMs for a match just flagged as a
        stream candidate (used when toggling the flag outside the scheduling flow)."""
        await match.fetch_related('tournament')
        notified_ids = await self._collect_notified_discord_ids(match)
        # Resolve the embed-footer community here (request context); the enqueued
        # coroutine runs later in the scope-less discord_queue worker.
        community = await _community_name()
        discord_queue.enqueue(self.notify_stream_candidate_subscribers(match, notified_ids, community))

    async def _collect_notified_discord_ids(self, match: Match) -> list:
        """
        Return the discord_ids of players and approved crew for a match.
        Used to deduplicate tournament-subscriber notifications.
        """
        recipients = await collect_match_recipients(
            match, include_watchers=False, include_unstaged_crew=True,
        )
        return list(recipients)
