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
from typing import Callable, ClassVar, Dict, Optional, Tuple

from application.errors import MissingCredentialError
from application.events import EventType, match_live
from application.repositories import MatchAcknowledgmentRepository, MatchRepository
from application.services import notification_links
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.discord import discord_queue
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
from application.services.match.match_hard_preset_service import MatchHardPresetService
from application.services.match.match_status import has_recorded_result
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
from application.utils.seed_provider import SeedProviderError
from models import GeneratedSeeds, Match, User

logger = logging.getLogger(__name__)

# Bracket pushes ride the serial DM queue; a provider that hangs must not hold
# every match call behind it.
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
) -> tuple:
    """(text, embed) for the check-in DM; ``your_seat`` makes it one player's copy."""
    seats = _seats_line(match)
    embed = match_embed(
        title='✅ Match checked in', color=COLOR_CHECKED_IN,
        description='The match is about to begin — good luck!',
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
        ),
        embed,
    )


def _checked_in_player_copies(match: Match, community: str, bracket_line: str = '') -> dict:
    """Each seated player's own check-in DM, keyed by Discord id."""
    return {
        p.user.discord_id: _checked_in_notification(
            match, community, bracket_line, your_seat=p.assigned_station,
        )
        for p in match.players if p.assigned_station and p.user.discord_id  # type: ignore[attr-defined]
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

    _seed_locks: ClassVar[Dict[int, asyncio.Lock]] = {}

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
        own version.
        """
        gate = authorize or AuthService.can_run_match
        await AuthService.ensure(
            await gate(actor, match),
            f"User cannot {action_verb} match {match.id}",
        )
        # The caller's row was loaded when its dialog opened; decide from the row
        # as it stands now, and stamp only if nobody else got there first.
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
        # Resolve the community name here (request context) rather than in the
        # queue worker, and pass the pre-built embed down with the text.
        community = await _community_name()
        bracket_line = await bracket_line_for(match.id)
        message, embed = build_message(match, community, bracket_line)
        player_copies = (
            build_player_copies(match, community, bracket_line) if build_player_copies else None
        )
        discord_queue.enqueue(self.notify_match_participants(
            match, message, embed, notice=notice, player_copies=player_copies,
        ))
        match_live.publish(match.id)

    async def seat_match(self, match: Match, actor: Optional[User] = None) -> None:
        # ``check`` below is synchronous and runs before ``_transition`` fetches
        # relations, so the players have to be loaded here for it to see them.
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

    async def finish_match(self, match: Match, actor: Optional[User] = None) -> None:
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
        )

    async def confirm_match(self, match: Match, actor: Optional[User] = None) -> None:
        # ``check`` below is synchronous and runs before ``_transition`` fetches
        # relations, so the players have to be loaded here for it to see them.
        await match.fetch_related('players')

        def check() -> None:
            if not match.finished_at:
                raise ValueError("Match must be finished before confirming")
            if match.confirmed_at:
                raise ValueError("Match is already confirmed")
            if not has_recorded_result(match.players):
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
            authorize=AuthService.can_confirm_match,
            notice=MATCH_CONFIRMED,
        )

        # Confirming a flagged result *is* the resolution — the admin has now
        # looked at it, which is all the flag ever asked for. A separate
        # MATCH_REVIEW_CLEARED row (rather than extra keys on the confirm audit)
        # keeps ``_transition``'s shared detail shape untouched and makes the
        # trail read record → flag → clear → confirm.
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

        # Push the confirmed result to Challonge when this match mirrors a
        # bracket match. Fire-and-forget so a Challonge outage never blocks
        # confirmation; failures are visible via audit and the manual re-push.
        async def _push_challonge_result() -> None:
            from application.services.challonge_service import ChallongeService
            try:
                await asyncio.wait_for(
                    ChallongeService().push_result_if_linked(match, actor), _BRACKET_PUSH_SECONDS,
                )
            except Exception:
                logger.exception("challonge auto-push failed for match %s", match.id)

        discord_queue.enqueue(_push_challonge_result())

        # Every other linked bracket site (start.gg) hears about it through the
        # shared bracket-source hook; it isolates each site's failure itself.
        async def _notify_bracket_sources() -> None:
            from application.services import bracket_sources
            try:
                await asyncio.wait_for(
                    bracket_sources.on_match_confirmed(match, actor), _BRACKET_PUSH_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.error("bracket site report timed out for match %s", match.id)

        discord_queue.enqueue(_notify_bracket_sources())

        # Advance the native bracket when this match mirrors a bracket match.
        # Peer of the Challonge push above: fire-and-forget so a failure never
        # blocks confirmation; failures are visible via audit and manual replay.
        async def _advance_bracket_result() -> None:
            from application.services.bracket_service import BracketService
            try:
                await BracketService().advance_if_linked(match, actor)
            except Exception:
                logger.exception("bracket auto-advance failed for match %s", match.id)

        discord_queue.enqueue(_advance_bracket_result())

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
        lock = self._seed_locks.get(match_id)
        if lock is None:
            lock = asyncio.Lock()
            self._seed_locks[match_id] = lock

        if lock.locked():
            return False, "Seed generation already in progress for this match", None

        async with lock:
            try:
                match = await Match.get(id=match_id, tenant_id=require_tenant_id()).prefetch_related(
                    'tournament', 'tournament__preset', 'tournament__hard_preset',
                    'players', 'players__user', 'stage',
                )

                if not await AuthService.can_run_match(actor, match):
                    return False, "You do not have permission to roll a seed for this match", None

                if match.generated_seed:
                    return False, "A seed has already been generated for this match", None

                # Resolve which randomizer + settings to roll. A Preset FK wins
                # when set (its randomizer + settings); otherwise fall back to the
                # legacy ``seed_generator`` string (hard-coded settings).
                #
                # Which preset that is, is the players' answer as much as the
                # tournament's: a match whose players all opted in rolls the
                # harder preset, and staff can override either way. This is the
                # only place that question is asked, and asking it here is what
                # closes the opt-in window — from this line on, the settings are
                # what ``GeneratedSeeds`` records rather than what anyone picks.
                preset = await self.hard_preset_service.resolve_preset(match)
                randomizer = preset.randomizer if preset is not None else match.tournament.seed_generator

                if not randomizer:
                    return False, "No seed generator configured for this tournament", None

                if randomizer not in self.seedgen_service.AVAILABLE_RANDOMIZERS:
                    return False, f"Seed generator '{randomizer}' not found", None

                # Last precondition, because it is about this match rather than
                # the tournament's setup: a match can only be seeded once and the
                # seed reaches the players by DM, so rolling before they exist
                # spends the single roll on nobody and leaves the real players
                # unable to get another. Enforced here and not only in the
                # table's Generate button, because REST and MCP reach this too.
                if not match.players:
                    return False, (
                        "This match has no players yet — a seed can only be rolled "
                        "once, and its DM would reach nobody"
                    ), None

                # A task-queue backend cannot answer "here is your seed" from one
                # call, so this surface hands it to ``ProviderTask`` and returns.
                # The worker finishes the job minutes later, through
                # ``complete_seed_roll`` below — the same completion this path
                # runs inline, so a restart in the middle changes who finishes
                # the roll but not what finishing means.
                if randomizer in self.seedgen_service.ASYNC_RANDOMIZERS:
                    return await self._queue_async_seed(match, randomizer, preset, actor)

                # Generate the seed. A keyed randomizer resolves this community's
                # own credential inside the generator and raises when it is not
                # configured; that surfaces below as "Error generating seed: …".
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

                message = f"Seed generated successfully for match ID {match.id}"
                return True, message, seed_url

            except MissingCredentialError as e:
                # Actionable and safe to show: it names a credential this actor
                # can go and configure, never upstream response text.
                return False, str(e), None
            except SeedProviderError as e:
                # Also actionable, and also curated rather than raw: the envelope
                # already classified this into "the upstream is down / rate-
                # limiting / rejected the settings" and bounded the provider's own
                # words. Telling someone minutes before a race that alttpr.com is
                # down — rather than "check the server logs" — is the difference
                # between waiting a minute and calling off the match.
                logger.error(
                    'Seed generation for match %s failed after %d attempt(s): %s',
                    match_id, e.attempts, e,
                )
                return False, str(e), None
            except Exception:
                # Log the full traceback (reaches logs + Sentry) and return a
                # generic message rather than leaking raw randomizer/HTTP error
                # text to the user and the REST 400 detail.
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

        # Nudges every open view into showing the rolling indicator, not just the
        # browser that clicked Generate.
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
        # The settings are snapshotted as sent — a preset edited after this roll
        # must not change what this seed is recorded as having used.
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
        # The roll waited on the provider; the proctor may have checked the
        # players in meanwhile, so write the seed and nothing else.
        await match.save(update_fields=['generated_seed_id'])

        # Send DMs to players in the background
        descriptor = _match_descriptor(match, await bracket_line_for(match.id))
        community = await _community_name()
        if is_seed_link(seed_url):
            seed_description = f'[Open your seed]({seed_url})'
            embed_url: Optional[str] = seed_url
            paste_instructions = None
            # The seed URL is the whole point of this DM, so it gets a button
            # rather than only a hyperlink buried in the embed description. It
            # points at the randomizer, not at Wizzrobe — the one link here that
            # is deliberately off-site.
            seed_link: Optional[DMLink] = DMLink('Open your seed', seed_url)
        else:
            # A permalink or seed-and-flags string is pasted into the player's
            # own randomizer. Discord refuses a non-URL as a button or embed URL
            # (the whole DM fails), so the seed rides in a copyable code block
            # and the button opens the match on the Player tab, where the same
            # seed has a copy button.
            paste_instructions = (
                self.seedgen_service.paste_instructions(randomizer, settings)
                or 'Paste this into your randomizer.'
            )
            seed_description = seed_paste_block(seed_url, paste_instructions)
            embed_url = None
            seed_link = await notification_links.player_match(match.id, label='Copy it from Wizzrobe')
        code_line = seed_code_line(seed_code)
        if code_line:
            seed_description = f'{seed_description}\n{code_line}'
        seed_embed = match_embed(
            title='🎲 Seed ready', color=COLOR_SEED,
            description=seed_description,
            tournament=match.tournament.name, community_name=community,
            player_names=descriptor['player_names'], when=match.scheduled_at,
            stage_name=match.stage.name if match.stage else None,
            url=embed_url,
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
        return f"Seed generated successfully for match ID {match.id}"

