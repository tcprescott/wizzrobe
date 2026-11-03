"""
Match Schedule Service - Business Logic Layer

Handles match scheduling operations like seating, finishing, and seed generation.

The Discord DM fan-out that these operations trigger lives in
``_schedule_notifications.MatchNotificationMixin``, composed in below — split out
as pure code motion once this module outgrew the 800-line budget. Recipient
resolution shared by both halves lives in ``_match_recipients``.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Optional, Tuple

from application.errors import MissingCredentialError
from application.events import EventType, match_live
from application.repositories import MatchAcknowledgmentRepository, MatchRepository
from application.services import notification_links
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.discord import discord_queue, seed_emojis
from application.services.discord.discord_service import DiscordService
from application.services.match._dm_context import (  # noqa: F401  (re-exported)
    _community_name,
    bracket_dm_context,
    bracket_line_for,
)
from application.services.match._match_recipients import (  # noqa: F401  (re-exported)
    _dm_opt_ok,
    collect_match_audience,
    collect_match_recipients,
)
from application.services.match._schedule_notifications import MatchNotificationMixin
from application.services.match._seed_ready_notifications import SEED_READY_PROMPT
from application.services.match.match_hard_preset_service import MatchHardPresetService
from application.services.match.match_status import closes_without_winner, has_recorded_result
from application.services.match.seed_ready import players_may_release, seed_ready_open
from application.services.notification_catalog import (
    MATCH_CHECKED_IN,
    MATCH_CONFIRMED,
    MATCH_FINISHED,
    MATCH_STARTED,
    PLAYERS,
    SEED_READY,
    Notice,
)
from application.services.seedgen_service import SeedGenerationService
from application.services.triforce_text_service import TriforceTextService
from application.tenant_context import require_tenant_id
from application.utils.discord_embeds import (
    COLOR_CHECKED_IN,
    COLOR_SEED,
    match_embed,
    state_changed_embed,
    time_field,
)
from application.utils.discord_messages import (
    DMLink,
    checked_in_dm,
    is_seed_link,
    seed_code_line,
    seed_dm,
    seed_paste_block,
    state_changed_dm,
)
from application.utils.match_labels import match_model_label
from application.utils.seed_provider import SeedProviderError
from application.utils.shared_lock import LockBusy, shared_lock
from models import GeneratedSeeds, Match, User

logger = logging.getLogger(__name__)

_BRACKET_PUSH_SECONDS = 90

_LIFECYCLE_FIELDS = ('seated_at', 'started_at', 'finished_at', 'confirmed_at', 'needs_review')


def _match_descriptor(match: Match, bracket_line: str = '') -> dict:
    """Extract human-readable match fields from a match with ``players__user``,
    ``stage`` (and ``scheduled_at``) loaded, for passing to message builders.

    ``bracket_line`` is the caller-resolved series context ("Semifinals · Game 2
    of 3 · Series 1-0"); it is passed in rather than looked up here because this
    is a sync projection and the lookup is a query. '' for a match no bracket
    scheduled, which is almost all of them.
    """
    return {
        'player_names': [p.user.preferred_name for p in match.players],
        'scheduled_at_display': time_field(match.scheduled_at),
        'stage_name': match.stage.name if match.stage else '',
        'bracket_line': bracket_line,
    }


def _match_embed_kwargs(match: Match, community: str) -> dict:
    """Shared embed kwargs from a match with tournament/players/stage loaded."""
    return {
        'tournament': match.tournament.name,
        'community_name': community,
        'player_names': [p.user.preferred_name for p in match.players],
        'when': match.scheduled_at,
        'stage_name': match.stage.name if match.stage else None,
    }


def _seats_line(match: Match) -> str:
    """"Alice: A1 · Bob: A2" for the players who have a seat, '' for none."""
    return ' · '.join(
        f'{p.user.preferred_name}: {p.assigned_station}'
        for p in match.players if p.assigned_station  # type: ignore[attr-defined]
    )


def _checked_in_notification(
    match: Match, community: str, bracket_line: str = '', your_seat: str = '',
    ready_prompt: bool = False,
) -> tuple:
    """(text, embed) for the check-in DM; ``your_seat`` makes it one player's copy,
    and ``ready_prompt`` one that asks them for Ready for seed."""
    seats = _seats_line(match)
    embed = match_embed(
        title='✅ Checked in · ready for your seed?' if ready_prompt else '✅ Match checked in',
        color=COLOR_CHECKED_IN,
        description=SEED_READY_PROMPT if ready_prompt else 'The match is about to begin — good luck!',
        **_match_embed_kwargs(match, community),
    )
    if seats:
        embed.add_field(name='Seats', value=seats, inline=False)
    if your_seat:
        embed.insert_field_at(0, name='Your seat', value=f'**{your_seat}**', inline=False)
    return (
        checked_in_dm(
            match.tournament.name, **_match_descriptor(match, bracket_line),
            seats=seats, your_seat=your_seat,
            closing=SEED_READY_PROMPT if ready_prompt else '',
        ),
        embed,
    )


def _checked_in_player_copies(match: Match, community: str, bracket_line: str = '') -> dict:
    """Each player's own check-in DM, keyed by Discord id: a seated player's names
    their seat, and every player's asks for Ready when they release the seed."""
    ready = seed_ready_open(match)
    return {
        p.user.discord_id: _checked_in_notification(
            match, community, bracket_line, your_seat=p.assigned_station, ready_prompt=ready,
        )
        for p in match.players  # type: ignore[attr-defined]
        if p.user.discord_id and (p.assigned_station or ready)
    }


def _state_notification(
    match: Match, community: str, new_state: str, bracket_line: str = '',
) -> tuple:
    """(text, embed) for a started/finished/confirmed transition DM."""
    return (
        state_changed_dm(
            match.tournament.name, new_state, **_match_descriptor(match, bracket_line),
        ),
        state_changed_embed(
            match.tournament.name, new_state,
            community_name=community,
            player_names=[p.user.preferred_name for p in match.players],
            when=match.scheduled_at,
            stage_name=match.stage.name if match.stage else None,
        ),
    )


class MatchScheduleService(MatchNotificationMixin):
    """Service for match scheduling operations."""

    def __init__(self) -> None:
        self.match_repository = MatchRepository()
        self.acknowledgment_repository = MatchAcknowledgmentRepository()
        self.discord_service = DiscordService()
        self.seedgen_service = SeedGenerationService()
        self.audit_service = AuditService()
        self.hard_preset_service = MatchHardPresetService()

    async def _transition(
        self,
        match: Match,
        actor: Optional[User],
        *,
        action_verb: str,
        check: Callable[[], None],
        timestamp_field: str,
        audit_action: str,
        event_type: str,
        build_message: Callable[[Match, str, str], tuple],
        notice: Notice,
        authorize: Optional[Callable] = None,
        build_player_copies: Optional[Callable[[Match, str, str], dict]] = None,
        notify: bool = True,
        seed_ready_buttons: bool = False,
    ) -> None:
        """Shared match lifecycle transition: authorize, validate, stamp, audit, notify.

        ``check`` raises ValueError on a precondition failure; ``build_message``
        takes ``(match, community_name, bracket_line)`` and returns
        ``(text, embed)`` — all three are resolved after the relations are
        fetched, in this request's tenant context, because the queue worker that
        sends the DM has neither the relations nor the tenant.

        ``authorize`` defaults to ``AuthService.can_run_match`` — the gate for
        the floor transitions a proctor owns. ``confirm_match`` overrides it with
        ``AuthService.can_confirm_match``, which excludes PROCTOR.

        ``notice`` is the catalogued message; the community's setting for it
        decides who hears and whether it pings. ``build_player_copies`` takes the same arguments as ``build_message``
        and returns ``{discord_id: (text, embed)}`` for players who get their
        own version. ``seed_ready_buttons`` puts Ready for seed on the players'
        copies when the match is open for it (check-in, on a tournament where
        players release their own seed).
        """
        gate = authorize or AuthService.can_run_match
        await AuthService.ensure(
            await gate(actor, match),
            f"User cannot {action_verb} match {match.id}",
        )
        # Re-read: the caller's row may be stale. Stamp only if nobody else got there first.
        await match.refresh_from_db(fields=_LIFECYCLE_FIELDS)
        check()

        now = datetime.now(timezone.utc)
        if not await self.match_repository.stamp_if_unset(match, timestamp_field, now):
            raise ValueError(
                "Someone else just updated this match. Refresh to see where it stands."
            )
        setattr(match, timestamp_field, now)
        await match.fetch_related('tournament', 'players__user', 'stage')
        await self.audit_service.write_and_publish(
            actor, audit_action, {'match_id': match.id}, event_type,
            event_extra={
                'tournament_id': match.tournament_id,
                'tournament': match.tournament.name,
            },
        )
        if not notify:
            match_live.publish(match.id)
            return
        # Resolve here, in request context: the queue worker has no tenant bound.
        community = await _community_name()
        bracket_line = await bracket_line_for(match.id)
        message, embed = build_message(match, community, bracket_line)
        player_copies = (
            build_player_copies(match, community, bracket_line) if build_player_copies else None
        )
        discord_queue.enqueue(self.notify_match_participants(
            match, message, embed, notice=notice, player_copies=player_copies,
            seed_ready_buttons=seed_ready_buttons and seed_ready_open(match),
        ))
        match_live.publish(match.id)

    async def seat_match(self, match: Match, actor: Optional[User] = None) -> None:
        # ``check`` runs before ``_transition`` fetches relations, so load the players here.
        await match.fetch_related('tournament', 'players')
        if match.tournament and match.tournament.is_racetime_enabled:
            raise ValueError(
                "Check-in is disabled for racetime.gg tournaments — the race "
                "room manages the match lifecycle."
            )

        def check() -> None:
            if match.seated_at:
                raise ValueError("Match is already checked in")
            if not match.players:
                raise ValueError(
                    "This match has no players yet — nothing to check in."
                )
            waiting = getattr(match, 'bracket_placeholders', None)
            if waiting:
                raise ValueError(
                    f"The bracket hasn't decided {' and '.join(waiting)} yet, so this match "
                    "can't be checked in. Sync the bracket once the earlier match is reported."
                )
        await self._transition(
            match, actor,
            action_verb="seat",
            check=check,
            timestamp_field="seated_at",
            audit_action=AuditActions.MATCH_SEATED,
            event_type=EventType.MATCH_SEATED,
            notice=MATCH_CHECKED_IN,
            build_message=lambda m, c, b: _checked_in_notification(m, c, b),
            build_player_copies=_checked_in_player_copies,
            seed_ready_buttons=True,
        )

    async def start_match(self, match: Match, actor: Optional[User] = None) -> None:
        def check() -> None:
            if not match.seated_at:
                raise ValueError("Match must be checked in before starting")
            if match.started_at:
                raise ValueError("Match is already started")
        await self._transition(
            match, actor,
            action_verb="start",
            check=check,
            timestamp_field="started_at",
            audit_action=AuditActions.MATCH_STARTED,
            event_type=EventType.MATCH_STARTED,
            build_message=lambda m, c, b: _state_notification(m, c, "Started", b),
            notice=MATCH_STARTED,
        )

    async def finish_match(
        self, match: Match, actor: Optional[User] = None, *, confirm: bool = False,
    ) -> None:
        """Finish a live match. An open race (more than four players) is confirmed too.

        Nobody records a winner for an open race, so there is nothing for the
        admin's confirm step to check: whoever may finish it closes it out, and
        only the confirmed DM goes out rather than two in a row. ``confirm`` does
        the same for a result someone who may confirm just recorded, under the
        confirm gate.
        """
        await match.fetch_related('players')
        open_race = closes_without_winner(match.players)  # type: ignore[attr-defined]

        def check() -> None:
            if not match.started_at:
                raise ValueError("Match must be started before finishing")
            if match.finished_at:
                raise ValueError("Match is already finished")
        await self._transition(
            match, actor,
            action_verb="finish",
            check=check,
            timestamp_field="finished_at",
            audit_action=AuditActions.MATCH_FINISHED,
            event_type=EventType.MATCH_FINISHED,
            build_message=lambda m, c, b: _state_notification(m, c, "Finished", b),
            notice=MATCH_FINISHED,
            notify=not (open_race or confirm),
        )
        if open_race:
            await self._confirm(match, actor, authorize=AuthService.can_run_match)
        elif confirm:
            await self._confirm(match, actor, authorize=AuthService.can_confirm_match)

    async def confirm_match(self, match: Match, actor: Optional[User] = None) -> None:
        await self._confirm(match, actor, authorize=AuthService.can_confirm_match)

    async def _confirm(self, match: Match, actor: Optional[User], *, authorize: Callable) -> None:
        # ``check`` runs before ``_transition`` fetches relations, so load the players here.
        await match.fetch_related('players')

        def check() -> None:
            if not match.finished_at:
                raise ValueError("Match must be finished before confirming")
            if match.confirmed_at:
                raise ValueError("Match is already confirmed")
            if not has_recorded_result(match.players) and not closes_without_winner(match.players):
                raise ValueError(
                    "No result has been recorded for this match — record the "
                    "winner before confirming."
                )
        await self._transition(
            match, actor,
            action_verb="confirm",
            check=check,
            timestamp_field="confirmed_at",
            audit_action=AuditActions.MATCH_CONFIRMED,
            event_type=EventType.MATCH_CONFIRMED,
            build_message=lambda m, c, b: _state_notification(m, c, "Confirmed", b),
            authorize=authorize,
            notice=MATCH_CONFIRMED,
        )
        from application.services import opt_in_notifications
        discord_queue.enqueue(opt_in_notifications.match_result(match.id))

        if match.needs_review:
            match.needs_review = False
            await match.save(update_fields=['needs_review'])
            await self.audit_service.write_and_publish(
                actor,
                AuditActions.MATCH_REVIEW_CLEARED,
                {
                    'match_id': match.id,
                    'resolved_by': 'confirmation',
                },
                EventType.MATCH_REVIEW_CLEARED,
                event_extra={'tournament_id': match.tournament_id},
            )

        self.push_confirmed_result(match, actor)

    def push_confirmed_result(self, match: Match, actor: Optional[User]) -> None:
        """Hand a confirmed result on to every bracket the match mirrors, off the request path."""
        # Fire-and-forget: a Challonge outage must not block confirmation.
        async def _push_challonge_result() -> None:
            from application.services.challonge_service import ChallongeService
            try:
                await asyncio.wait_for(
                    ChallongeService().push_result_if_linked(match, actor), _BRACKET_PUSH_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.warning("challonge auto-push timed out for match %s", match.id)
            except Exception:
                logger.exception("challonge auto-push failed for match %s", match.id)

        discord_queue.enqueue(_push_challonge_result())

        async def _notify_bracket_sources() -> None:
            from application.services import bracket_sources
            try:
                await asyncio.wait_for(
                    bracket_sources.on_match_confirmed(match, actor), _BRACKET_PUSH_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.error("bracket site report timed out for match %s", match.id)

        discord_queue.enqueue(_notify_bracket_sources())

        # Fire-and-forget: a failure must not block confirmation.
        async def _advance_bracket_result() -> None:
            from application.services.bracket_service import BracketService
            try:
                await BracketService().advance_if_linked(match, actor)
            except Exception:
                logger.exception("bracket auto-advance failed for match %s", match.id)

        discord_queue.enqueue(_advance_bracket_result())

    async def close_played_elsewhere(self, match: Match, actor: User, site: str) -> None:
        """Confirm a match whose result was reported on its bracket site, skipping the floor steps.

        The players have already played, so nobody is DMed about check-in, start
        or finish. Nothing goes back to the site either: it is where the result
        came from.
        """
        await AuthService.ensure(
            await AuthService.can_confirm_match(actor, match),
            f"User cannot confirm match {match.id}",
        )
        await match.refresh_from_db(fields=_LIFECYCLE_FIELDS)
        await match.fetch_related('players')
        if match.confirmed_at:
            raise ValueError("Match is already confirmed")
        if not has_recorded_result(match.players):  # type: ignore[attr-defined]
            raise ValueError("Record the winner before confirming.")
        now = datetime.now(timezone.utc)
        for field in ('seated_at', 'started_at', 'finished_at'):
            if getattr(match, field) is None and await self.match_repository.stamp_if_unset(match, field, now):
                setattr(match, field, now)
        if not await self.match_repository.stamp_if_unset(match, 'confirmed_at', now):
            raise ValueError("Someone else just updated this match. Refresh to see where it stands.")
        match.confirmed_at = now
        await match.fetch_related('tournament')
        await self.audit_service.write_and_publish(
            actor, AuditActions.MATCH_CONFIRMED, {'match_id': match.id, 'played_on': site},
            EventType.MATCH_CONFIRMED,
            event_extra={'tournament_id': match.tournament_id, 'tournament': match.tournament.name},  # type: ignore[attr-defined]
        )
        from application.services import opt_in_notifications
        discord_queue.enqueue(opt_in_notifications.match_result(match.id))
        match_live.publish(match.id)

    async def generate_seed(self, match_id: int, actor: Optional[User] = None) -> Tuple[bool, str, Optional[str]]:
        """
        Generate a seed for a match and send DMs to players.

        This method includes locking to prevent concurrent seed generation for the same match.

        Args:
            match_id: ID of the match to generate seed for

        Returns:
            Tuple of (success: bool, message: str, seed_url: Optional[str])
            - If successful: (True, success_message, seed_url)
            - If already in progress: (False, "Generation already in progress", None)
            - If failed: (False, error_message, None)
        """
        return await self._roll_seed(match_id, actor, players_release=False)

    async def release_seed_to_ready_players(
        self, match_id: int, actor: User,
    ) -> Tuple[bool, str, Optional[str]]:
        """Roll and send the seed because every player tapped Ready for seed.

        ``actor`` is the player whose tap completed the set. It is not a proctor,
        so instead of ``can_run_match`` the roll checks, under the same lock, that
        the tournament lets players release their seed, the match is checked in,
        ``actor`` plays it and every player is ready. Same return as
        :meth:`generate_seed`.
        """
        return await self._roll_seed(match_id, actor, players_release=True)

    async def _roll_seed(
        self, match_id: int, actor: Optional[User], *, players_release: bool,
    ) -> Tuple[bool, str, Optional[str]]:
        try:
            async with shared_lock('match.seed_roll', match_id, wait=False):
                return await self._roll_seed_locked(match_id, actor, players_release=players_release)
        except LockBusy:
            return False, "Seed generation already in progress for this match", None

    async def _roll_seed_locked(
        self, match_id: int, actor: Optional[User], *, players_release: bool,
    ) -> Tuple[bool, str, Optional[str]]:
        try:
            match = await Match.get(id=match_id, tenant_id=require_tenant_id()).prefetch_related(
                'tournament', 'tournament__preset', 'tournament__hard_preset',
                'players', 'players__user', 'stage',
            )

            if players_release:
                if not players_may_release(match, actor):
                    return False, "The seed can't be released by the players right now", None
            elif not await AuthService.can_run_match(actor, match):
                return False, "You do not have permission to roll a seed for this match", None

            if match.generated_seed:
                return False, "A seed has already been generated for this match", None

            # The only place the hard-preset question is asked; resolving it closes the opt-in window.
            preset = await self.hard_preset_service.resolve_preset(match)
            randomizer = preset.randomizer if preset is not None else match.tournament.seed_generator

            if not randomizer:
                return False, "No seed generator configured for this tournament", None

            if randomizer not in self.seedgen_service.AVAILABLE_RANDOMIZERS:
                return False, f"Seed generator '{randomizer}' not found", None

            # Not only the button's check: REST and MCP reach this, and a seed rolled before players exist is lost.
            if not match.players:
                return False, (
                    "This match has no players yet — a seed can only be rolled "
                    "once, and its DM would reach nobody"
                ), None

            if randomizer in self.seedgen_service.ASYNC_RANDOMIZERS:
                return await self._queue_async_seed(match, randomizer, preset, actor)

            triforce_text = None
            if self.seedgen_service.supports_triforce_texts(randomizer):
                triforce_text = await TriforceTextService().get_balanced_text(match.tournament)

            call = await self.seedgen_service.generate_seed_call(
                randomizer, preset, surface='match', triforce_text=triforce_text,
            )
            seed_url = call.value.url

            await self.complete_seed_roll(
                match, randomizer, preset, actor,
                seed_url=seed_url,
                settings=call.value.settings,
                provider_meta=call.as_meta(),
                seed_code=call.value.code,
            )

            message = f"Seed rolled for {match_model_label(match, with_context=False)}."
            return True, message, seed_url

        except MissingCredentialError as e:
            return False, str(e), None
        except SeedProviderError as e:
            logger.error(
                'Seed generation for match %s failed after %d attempt(s): %s',
                match_id, e.attempts, e,
            )
            return False, str(e), None
        except Exception:
            # Never return the raw exception text: it reaches users and the REST 400 detail.
            logger.exception("Seed generation failed for match %s", match_id)
            return False, "Seed generation failed. Please check the server logs.", None

    async def _queue_async_seed(
        self, match: Match, randomizer: str, preset, actor: Optional[User],
    ) -> Tuple[bool, str, Optional[str]]:
        """Hand a task-queue roll to :class:`ProviderTask` and return immediately.

        Returns no seed url — there is not one yet. The caller learns the roll
        finished the same way everyone else does: the worker completes it and
        publishes on ``match_live``.
        """
        from application.services.provider_task_service import ProviderTaskService
        from application.services.seed_roll_service import SeedRollService

        task_service = ProviderTaskService()
        existing = await task_service.repository.active_for_match(match.id)
        if existing is not None:
            return False, "Seed generation already in progress for this match", None

        task = await task_service.queue(
            provider=randomizer, operation='generate_seed', actor=actor,
            match_id=match.id, preset_id=preset.id if preset is not None else None,
        )
        await SeedRollService().submit(task, preset)

        await self.audit_service.write_and_publish(
            actor,
            AuditActions.MATCH_SEED_ROLL_QUEUED,
            {
                'match_id': match.id,
                'randomizer': randomizer,
                'preset': preset.name if preset is not None else None,
                'provider_task_id': task.provider_task_id,
            },
            EventType.MATCH_SEED_ROLL_QUEUED,
            event_details={'match_id': match.id, 'tournament_id': match.tournament_id},
        )

        match_live.publish(match.id)

        return True, (
            "Rolling the seed — this takes a few minutes. "
            "The players are DMed as soon as it lands."
        ), None

    async def complete_seed_roll(
        self, match: Match, randomizer: str, preset, actor: Optional[User], *,
        seed_url: str, settings: Optional[dict], provider_meta: Optional[dict],
        seed_code: Optional[list[str]] = None,
    ) -> str:
        """Record a finished roll, tell the players, and audit it.

        The single definition of "a seed roll completed", called both inline by
        the synchronous path and minutes later by the worker for a task-queue
        backend. Keeping one copy is what makes a restart mid-roll change only
        *who* finishes the job.
        """
        # Snapshot the settings: a later preset edit must not change this seed's record.
        match.generated_seed = await GeneratedSeeds.create(
            tenant_id=require_tenant_id(),
            seed_url=seed_url,
            seed_info=f"Generated seed for match {match.id}",
            randomizer=randomizer,
            preset_id=preset.id if preset is not None else None,
            settings_snapshot=settings,
            rolled_by_id=actor.id if actor is not None else None,
            provider_meta=provider_meta,
            seed_code=seed_code or None,
        )
        # Write only the seed: the proctor may have checked players in while the roll ran.
        await match.save(update_fields=['generated_seed_id'])

        descriptor = _match_descriptor(match, await bracket_line_for(match.id))
        community = await _community_name()
        if is_seed_link(seed_url):
            seed_description = f'[Open your seed]({seed_url})'
            embed_url: Optional[str] = seed_url
            paste_instructions = None
            # Deliberately off-site: the button opens the seed on the randomizer.
            seed_link: Optional[DMLink] = DMLink('Open your seed', seed_url)
        else:
            # Discord rejects a non-URL button or embed URL (the whole DM fails), so it rides in a code block.
            paste_instructions = (
                self.seedgen_service.paste_instructions(randomizer, settings)
                or 'Paste this into your randomizer.'
            )
            seed_description = seed_paste_block(seed_url, paste_instructions)
            embed_url = None
            seed_link = await notification_links.player_match(match.id, label='Copy it from Wizzrobe')
        code_line = seed_code_line(seed_code)
        emoji_row = await seed_emojis.code_row(randomizer, seed_code, self.discord_service.get_bot())
        code_field = f"{emoji_row}\n-# {' · '.join(seed_code or [])}" if emoji_row else None
        if code_line and not code_field:
            seed_description = f'{seed_description}\n{code_line}'
        seed_embed = match_embed(
            title='🎲 Seed ready', color=COLOR_SEED,
            description=seed_description,
            tournament=match.tournament.name, community_name=community,
            player_names=descriptor['player_names'], when=match.scheduled_at,
            stage_name=match.stage.name if match.stage else None,
            url=embed_url, code=code_field,
        )

        async def _send_seed_dms() -> None:
            for player in match.players:
                if player.user.discord_id:
                    dm_message = seed_dm(
                        player.user.preferred_name,
                        match.tournament.name,
                        seed_url,
                        paste_instructions=paste_instructions,
                        code_line=code_line,
                        **descriptor,
                    )
                    success, err = await self.discord_service.send_dm(
                        player.user.discord_id, dm_message,
                        embed=seed_embed, link=seed_link, notice=SEED_READY.to(PLAYERS),
                    )
                    if not success:
                        logger.warning(
                            "seed DM failed for %s: %s", player.user.discord_id, err
                        )

        discord_queue.enqueue(_send_seed_dms())

        await self.audit_service.write_and_publish(
            actor,  # type: ignore[arg-type]
            AuditActions.MATCH_SEED_ROLLED,
            {
                'match_id': match.id,
                'randomizer': randomizer,
                'preset': preset.name if preset is not None else None,
                'seed_url': seed_url,
                **{k: v for k, v in (provider_meta or {}).items()
                   if k in ('attempts', 'latency_ms')},
            },
            EventType.MATCH_SEED_ROLLED,
            event_details={
                'match_id': match.id,
                'tournament_id': match.tournament_id,  # type: ignore[attr-defined]
                'seed_url': seed_url,
            },
        )

        match_live.publish(match.id)
        return f"Seed rolled for {match_model_label(match, with_context=False)}."

