"""Players privately agreeing to play a match on the tournament's harder preset.

A tournament may name a second, harder preset beside its standard one. Each
player in a match can opt in to it. The match rolls the harder preset only when
**every** player has opted in, and until that happens no player can learn
whether anyone else did.

That secrecy is the feature, not a detail of it. If one player could see the
other's pending opt-in, opting in would become a challenge to answer and
declining would become a refusal to meet it — and the reason to offer harder
settings at all is that two people both want them, not that one of them dared
the other. So a lone opt-in is silent: it notifies nobody, publishes no event,
and appears on no surface but the opter's own. It simply lapses when the seed
rolls.

**What the secrecy does and does not promise.** A player who has not opted in
learns nothing at all — that is the property the feature rests on, and the one
that stops opting in from becoming a dare. A player who *has* opted in does
learn the outcome: agreement is announced, and its absence is itself
informative, so they can conclude their opponent is not in. That much is
unavoidable, since a player who commits has to find out what they are playing.
It also means a player willing to opt in and immediately withdraw can probe the
other's answer, and the withdrawal is invisible. Closing that would mean adding
friction to withdrawal, which the product deliberately does not have: the window
stays open until the seed rolls. Treat "nobody learns anything" as true only for
the player who has not committed.

Three things follow, and each is load-bearing:

* :meth:`my_state` is the only read any surface may call, and it answers about
  the caller alone. ``everyone_in`` can be true only when the caller is one of
  the everyone — a caller who has not opted in cannot make the set complete, so
  the flag never leaks a state the caller is not already part of.
* The individual opt-in is audited but **not** published on the event bus.
  Webhook subscribers are arbitrary outside listeners; the audit log is staff
  reading their own community's history, which the user accepted.
* Agreement is derived (opt-in set == player set), never stored. A roster change
  re-answers the question instead of leaving a stale yes behind.

Staff can overrule the whole thing per match with ``Match.preset_override``,
in either direction. That is deliberately visible: it is staff's decision about
the match, and the players are told. It still reveals nothing, because forcing
the hard preset says nothing about who had opted in.

Both opting in and withdrawing are refused once the seed is rolled. From that
point ``GeneratedSeeds.preset`` is the record of what was actually played, and a
choice about settings that are already decided is not a choice.
"""

import logging
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from application.errors import require_found
from application.events import EventType
from application.repositories import (
    MatchHardPresetRepository,
    MatchRepository,
)
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.utils.shared_lock import shared_lock
from models import Match, Preset, PresetOverride, User

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HardPresetState:
    """What one viewer may know about one match's preset choice.

    Deliberately a projection rather than a model or a queryset: a surface that
    received rows could render somebody else's, and the leak would be one
    template edit away. Nothing here distinguishes "my opponent has not opted
    in" from "my opponent opted in and I have not" — both are ``opted_in=False,
    everyone_in=False``, which is the entire point.
    """

    offered: bool = False
    """The tournament has a hard preset, so there is something to opt into."""

    opted_in: bool = False
    """This viewer has opted in."""

    everyone_in: bool = False
    """Every player has opted in, this viewer included."""

    locked: bool = False
    """The seed is rolled; the choice is closed."""

    override: Optional[PresetOverride] = None
    """Staff's per-match decision, which beats the players' if set."""

    preset_name: str = ''
    """The hard preset's name — the label every surface shows."""

    standard_preset_name: str = ''
    """The tournament's normal preset's name, for saying what staff chose."""

    @property
    def can_choose(self) -> bool:
        """Whether the toggle should do anything at all."""
        return self.offered and not self.locked and self.override is None

    @property
    def effective_hard(self) -> bool:
        """Whether this match will roll (or did roll) the hard preset."""
        if self.override is not None:
            return self.override == PresetOverride.HARD
        return self.everyone_in


@dataclass(frozen=True)
class HardPresetSnapshot:
    """A match's opt-in situation as it stood before an edit rewrote it.

    Every field here stops being readable the moment the edit lands: the roster
    is replaced, and a reassignment swaps the tournament whose preset the
    players were agreeing to. So :meth:`MatchHardPresetService.reconcile_edit`
    cannot work any of it out afterwards — it has to be handed this.
    """

    tournament_id: int = 0
    hard_preset_id: Optional[int] = None
    hard_preset_name: str = ''
    standard_preset_name: str = ''
    unanimous: bool = False
    opted_in: Set[int] = field(default_factory=set)


def _agreement_lock(match_id: int) -> AbstractAsyncContextManager[None]:
    """Serialise one match's crossing of the unanimity line.

    Two players sending their last opt-in at the same moment would each read a
    set that is not yet complete, then each find it complete on the re-check,
    and the agreement would be audited, published and DMed twice.
    """
    return shared_lock('match.hard_preset_agreement', match_id)


class MatchHardPresetService:
    """Service for the per-match hard-preset opt-in."""

    def __init__(self) -> None:
        self.repository = MatchHardPresetRepository()
        self.match_repository = MatchRepository()
        self.audit_service = AuditService()

    async def _require_match(self, match_id: int) -> Match:
        match = require_found(
            await self.match_repository.get_by_id(match_id),
            f"Match {match_id}",
        )
        await match.fetch_related('tournament', 'tournament__hard_preset', 'tournament__preset')
        return match

    @staticmethod
    def _player_ids(match: Match) -> Set[int]:
        return {p.user_id for p in match.players}  # type: ignore[attr-defined]

    async def _require_open_match(self, match_id: int, user: User) -> Match:
        """The match, if ``user`` plays in it and its preset is still undecided."""
        match = await self._require_match(match_id)
        if user.id not in self._player_ids(match):
            raise ValueError('Only the players in a match can choose its settings.')
        if match.tournament.hard_preset_id is None:
            raise ValueError('This tournament does not offer a harder preset.')
        if match.generated_seed_id is not None:  # type: ignore[attr-defined]
            raise ValueError(
                'The seed for this match has already been rolled, so its settings are set.'
            )
        if match.preset_override is not None:
            raise ValueError('Staff have already chosen the settings for this match.')
        return match

    async def _is_unanimous(self, match: Match) -> bool:
        """Every current player holds an opt-in row.

        A match with no players is never unanimous: an empty set equalling an
        empty set would otherwise roll the hard preset for a roster nobody has
        filled in yet.
        """
        player_ids = self._player_ids(match)
        if not player_ids:
            return False
        opted_in = await self.repository.user_ids_for_match(match.id)
        return player_ids <= opted_in

    async def my_state(self, match: Match, user: Optional[User]) -> HardPresetState:
        """What ``user`` may know about ``match``'s preset choice.

        The one read a surface may call. Everything it reports is either about
        the caller or already known to them.
        """
        tournament = match.tournament
        hard = getattr(tournament, 'hard_preset', None)
        if hard is None:
            return HardPresetState()

        standard = getattr(tournament, 'preset', None)
        base = HardPresetState(
            offered=True,
            locked=match.generated_seed_id is not None,  # type: ignore[attr-defined]
            override=match.preset_override,
            preset_name=hard.name,
            standard_preset_name=standard.name if standard is not None else '',
        )
        if user is None or user.id not in self._player_ids(match):
            return base

        opted_in = await self.repository.has_opted_in(match.id, user.id)
        return HardPresetState(
            offered=True,
            opted_in=opted_in,
            # Only for the opted-in viewer, or the flag leaks the other player's opt-in.
            everyone_in=opted_in and await self._is_unanimous(match),
            locked=base.locked,
            override=base.override,
            preset_name=base.preset_name,
            standard_preset_name=base.standard_preset_name,
        )

    async def board_states(
        self, user: User, match_ids: List[int],
    ) -> Dict[int, HardPresetState]:
        """One :class:`HardPresetState` per match, for a whole board.

        Three queries for any number of rows, rather than ``my_state`` per row.
        Same guarantee as ``my_state``: every state describes this viewer, and
        ``everyone_in`` is set only where they are one of the everyone.
        """
        if not match_ids:
            return {}
        matches = await self.match_repository.get_by_ids_with_presets(match_ids)
        mine = await self.repository.opted_in_match_ids(user, match_ids)
        opted_by_match = await self.repository.user_ids_by_match(match_ids)

        states: Dict[int, HardPresetState] = {}
        for match in matches:
            hard = getattr(match.tournament, 'hard_preset', None)
            if hard is None:
                continue
            player_ids = self._player_ids(match)
            if user.id not in player_ids:
                continue
            opted_in = match.id in mine
            standard = getattr(match.tournament, 'preset', None)
            states[match.id] = HardPresetState(
                offered=True,
                opted_in=opted_in,
                everyone_in=(
                    opted_in
                    and bool(player_ids)
                    and player_ids <= opted_by_match.get(match.id, set())
                ),
                locked=match.generated_seed_id is not None,  # type: ignore[attr-defined]
                override=match.preset_override,
                preset_name=hard.name,
                standard_preset_name=standard.name if standard is not None else '',
            )
        return states

    async def resolve_preset(self, match: Match) -> Optional[Preset]:
        """The preset this match rolls with.

        Staff's override wins; otherwise the hard preset when everyone opted in;
        otherwise the tournament's standard preset. Called by ``generate_seed``
        at roll time, which is the only moment the answer matters.
        """
        tournament = match.tournament
        hard = getattr(tournament, 'hard_preset', None)
        standard = getattr(tournament, 'preset', None)

        if match.preset_override is not None:
            if match.preset_override == PresetOverride.HARD and hard is not None:
                return hard
            return standard
        if hard is not None and await self._is_unanimous(match):
            return hard
        return standard

    async def opt_in(self, match_id: int, user: User) -> HardPresetState:
        async with _agreement_lock(match_id):
            match = await self._require_open_match(match_id, user)
            was_unanimous = await self._is_unanimous(match)
            _, created = await self.repository.get_or_create(match=match, user=user)
            if created:
                # write_log, not write_and_publish: an event would broadcast a lone opt-in to every webhook.
                await self.audit_service.write_log(
                    user, AuditActions.MATCH_HARD_PRESET_OPTED_IN, {'match_id': match_id},
                )
            if not was_unanimous:
                await self._settle_agreement(match, user, became=True)
            return await self.my_state(match, user)

    async def withdraw(self, match_id: int, user: User) -> HardPresetState:
        async with _agreement_lock(match_id):
            match = await self._require_open_match(match_id, user)
            was_unanimous = await self._is_unanimous(match)
            removed = await self.repository.delete_by_match_and_user(match=match, user=user)
            if removed:
                await self.audit_service.write_log(
                    user, AuditActions.MATCH_HARD_PRESET_WITHDRAWN, {'match_id': match_id},
                )
            if was_unanimous:
                await self._settle_agreement(match, user, became=False)
            return await self.my_state(match, user)

    async def _settle_agreement(self, match: Match, actor: User, *, became: bool) -> None:
        """Audit, publish and announce a crossing of the unanimity line.

        Called only when the line might have been crossed, and re-checks which
        side the match is now on — so a second opt-in on an already-agreed match
        announces nothing.
        """
        now_unanimous = await self._is_unanimous(match)
        if now_unanimous != became:
            return

        tournament = match.tournament
        standard = getattr(tournament, 'preset', None)
        await self._record_transition(
            match, actor, became=became,
            preset_id=tournament.hard_preset_id,
            preset_name=tournament.hard_preset.name,
            standard_preset_name=standard.name if standard is not None else '',
            actor_backed_out=True,
        )

    async def _record_transition(
        self, match: Match, actor: User, *, became: bool,
        preset_id: Optional[int], preset_name: str, standard_preset_name: str,
        recipients: Optional[List[User]] = None,
        actor_backed_out: bool = False,
    ) -> None:
        """Audit, publish and announce a crossing of the unanimity line.

        The one place the line is ever recorded as crossed, so a player's own
        opt-in and a staff roster edit cannot drift into telling different
        stories about the same event.

        The preset is passed rather than read off the match because a match that
        just moved tournaments would otherwise name the preset it is heading
        *to* in a message about the one it just lost.
        """
        action = (
            AuditActions.MATCH_HARD_PRESET_AGREED if became
            else AuditActions.MATCH_HARD_PRESET_AGREEMENT_REVOKED
        )
        event = (
            EventType.MATCH_HARD_PRESET_AGREED if became
            else EventType.MATCH_HARD_PRESET_AGREEMENT_REVOKED
        )
        await self.audit_service.write_and_publish(
            actor, action,
            {'match_id': match.id, 'preset_id': preset_id},
            event,
            event_extra={'tournament_id': match.tournament_id},  # type: ignore[attr-defined]
        )
        await self._announce(
            match, actor, became=became,
            preset_name=preset_name,
            standard_preset_name=standard_preset_name,
            recipients=recipients,
            actor_backed_out=actor_backed_out,
        )

    async def _announce(
        self, match: Match, actor: User, *, became: bool,
        preset_name: str, standard_preset_name: str,
        recipients: Optional[List[User]] = None,
        actor_backed_out: bool = False,
    ) -> None:
        """DM the players that the match's settings just changed hands.

        Only ever sent to people the fact is already public to: the agreement DM
        is the moment the secret ends, and a revocation DM goes to those who had
        been told about the agreement that broke. Naming whoever backed out
        leaks nothing — everyone in that conversation already knew they were in.

        ``recipients`` exists because "the players" and "the people who were
        told" stop being the same set the moment staff edit the roster. Default
        to the current roster only where the two coincide.
        """
        from application.services.discord import discord_queue
        from application.services.match._hard_preset_notifications import (
            notify_hard_preset_agreement,
        )

        if recipients is None:
            recipients = [p.user for p in match.players]  # type: ignore[attr-defined]
        if not recipients:
            return
        discord_queue.enqueue(notify_hard_preset_agreement(
            match_id=match.id,
            tournament_name=match.tournament.name,
            preset_name=preset_name,
            standard_preset_name=standard_preset_name,
            when=match.scheduled_at,
            recipients=recipients,
            agreed=became,
            # Empty for a staff edit, or the change is credited to a player.
            actor_name=actor.preferred_name if actor_backed_out else '',
        ))

    async def send_offer(self, match: Match) -> None:
        """Offer the harder preset to a match's players, if it has one.

        Rides the scheduling fan-out, because being given a match is when a
        player first has something to decide about. Skipped once the seed
        exists: a DM offering a choice that is already closed is worse than no
        DM at all.

        The opt-in ids it reads pick each recipient's button and nothing else —
        a rescheduled match may already carry answers, and re-offering a player
        the choice they already made would read as their opt-in having been
        lost. They never reach the message text.

        Never raises; per-DM failures are logged and swallowed by the notify.
        """
        from application.services.match._hard_preset_notifications import (
            notify_hard_preset_invite,
        )

        try:
            await match.fetch_related(
                'tournament', 'tournament__hard_preset', 'players', 'players__user',
            )
            hard = getattr(match.tournament, 'hard_preset', None)
            if (
                hard is None
                or match.generated_seed_id is not None  # type: ignore[attr-defined]
                or match.preset_override is not None
            ):
                # Keep in step with ``_require_open_match``: never offer a choice its button refuses.
                return
            opted_in = await self.repository.user_ids_for_match(match.id)
            await notify_hard_preset_invite(
                match_id=match.id,
                tournament_name=match.tournament.name,
                preset_name=hard.name,
                when=match.scheduled_at,
                recipients=[p.user for p in match.players],
                opted_in_ids=list(opted_in),
            )
        except Exception:
            logger.exception("send_offer unexpected error for match %s", match.id)

    async def set_override(
        self, match_id: int, override: Optional[PresetOverride], actor: User,
    ) -> Match:
        """Staff choosing a match's preset themselves, or handing it back.

        Refused after the seed is rolled, for the same reason the players' own
        choice is: the settings are already decided and recorded on the seed.

        Existing opt-ins are deliberately left in place. Clearing the override
        restores whatever the players had chosen, rather than silently discarding
        agreements staff never asked to destroy.
        """
        match = await self._require_match(match_id)
        # Not redundant with the page's check: this is the only gate for every other caller.
        await AuthService.ensure(
            await AuthService.can_crud_match(actor, match),
            f"User cannot set the preset override on match {match_id}",
        )
        if match.generated_seed_id is not None:  # type: ignore[attr-defined]
            raise ValueError(
                'The seed for this match has already been rolled, so its settings are set.'
            )
        if override == PresetOverride.HARD and match.tournament.hard_preset_id is None:
            raise ValueError('This tournament does not have a harder preset to force.')
        if match.preset_override == override:
            return match

        await self.match_repository.update(match, preset_override=override)

        action = (
            AuditActions.MATCH_PRESET_OVERRIDE_SET if override is not None
            else AuditActions.MATCH_PRESET_OVERRIDE_CLEARED
        )
        event = (
            EventType.MATCH_PRESET_OVERRIDE_SET if override is not None
            else EventType.MATCH_PRESET_OVERRIDE_CLEARED
        )
        await self.audit_service.write_and_publish(
            actor, action,
            {'match_id': match_id, 'override': override.value if override else None},
            event,
            event_extra={'tournament_id': match.tournament_id},  # type: ignore[attr-defined]
        )
        await self._announce_override(match, override)
        return match

    async def _announce_override(
        self, match: Match, override: Optional[PresetOverride],
    ) -> None:
        from application.services.discord import discord_queue
        from application.services.match._hard_preset_notifications import (
            notify_hard_preset_override,
        )

        preset = await self.resolve_preset(match)
        players = list(match.players)  # type: ignore[attr-defined]
        discord_queue.enqueue(notify_hard_preset_override(
            match_id=match.id,
            tournament_name=match.tournament.name,
            preset_name=preset.name if preset is not None else '',
            when=match.scheduled_at,
            recipients=[p.user for p in players],
            forced=override is not None,
        ))

    async def snapshot(self, match: Match) -> HardPresetSnapshot:
        """Capture the opt-in situation before an edit rewrites it.

        Must be called *before* the write. Afterwards a swapped-in player has no
        opt-in row, so a broken agreement is indistinguishable from one that
        never existed, and a reassigned match no longer knows which tournament's
        harder preset its players had agreed to.
        """
        await match.fetch_related(
            'tournament', 'tournament__hard_preset', 'tournament__preset', 'players',
        )
        hard = getattr(match.tournament, 'hard_preset', None)
        standard = getattr(match.tournament, 'preset', None)
        return HardPresetSnapshot(
            tournament_id=match.tournament_id,  # type: ignore[attr-defined]
            hard_preset_id=match.tournament.hard_preset_id,
            hard_preset_name=hard.name if hard is not None else '',
            standard_preset_name=standard.name if standard is not None else '',
            unanimous=await self._is_unanimous(match),
            opted_in=await self.repository.user_ids_for_match(match.id),
        )

    async def reconcile_edit(
        self, match: Match, before: HardPresetSnapshot, actor: User,
    ) -> None:
        """Re-answer the agreement after a match edit, and say so if it moved.

        Every path that rewrites a match's roster or its tournament goes through
        here, because both can change the answer without anybody opting in or
        out. Two cases, and they are not the same:

        * **The match moved** to another tournament, or its tournament's harder
          preset changed underneath it. Every opt-in is discarded: the players
          agreed to a named preset, and consent to one set of settings is not
          consent to whichever settings the match lands on next.
        * **The roster changed.** A departing player's row stops counting, which
          can cross the unanimity line in either direction — a swap breaks an
          agreement the remaining players were told about, and dropping the one
          holdout completes one nobody announced.
        """
        await match.fetch_related(
            'tournament', 'tournament__hard_preset', 'tournament__preset',
            'players', 'players__user',
        )
        moved = (
            match.tournament_id != before.tournament_id  # type: ignore[attr-defined]
            or match.tournament.hard_preset_id != before.hard_preset_id
        )
        if moved:
            await self._discard_all(match, before, actor)
            return

        if before.hard_preset_id is None:
            return

        remaining = self._player_ids(match)
        for user_id in before.opted_in - remaining:
            await self.repository.delete_for_user_in_match(match.id, user_id)

        now_unanimous = await self._is_unanimous(match)
        if now_unanimous == before.unanimous:
            return
        await self._record_transition(
            match, actor, became=now_unanimous,
            preset_id=before.hard_preset_id,
            preset_name=before.hard_preset_name,
            standard_preset_name=before.standard_preset_name,
            recipients=self._roster_change_recipients(
                match, now_unanimous, before.opted_in,
            ),
        )

    async def _discard_all(
        self, match: Match, before: HardPresetSnapshot, actor: User,
    ) -> None:
        """Drop every opt-in because the match is no longer the one agreed to.

        Silent unless an agreement existed, and then told only to the players
        who were party to it and are still here — told in the *old* tournament's
        words, since that is the preset they lost.
        """
        for user_id in before.opted_in:
            await self.repository.delete_for_user_in_match(match.id, user_id)
        if not before.unanimous or before.hard_preset_id is None:
            return
        await self._record_transition(
            match, actor, became=False,
            preset_id=before.hard_preset_id,
            preset_name=before.hard_preset_name,
            standard_preset_name=before.standard_preset_name,
            recipients=self._roster_change_recipients(match, False, before.opted_in),
        )

    @staticmethod
    def _roster_change_recipients(
        match: Match, now_unanimous: bool, opted_in_before: Set[int],
    ) -> List[User]:
        """Who may be told that a roster edit moved the match's settings.

        When the edit *completed* an agreement, everyone left in the match holds
        an opt-in row, so the whole roster is party to it.

        When the edit *broke* one, the recipients are the players who were told
        it existed — the ones who already held a row. A player who was just
        added was never party to the agreement, and telling them it has lapsed
        would hand them both facts the feature exists to withhold: that their
        new opponents opted in, and that they are the only one who has not.
        """
        players = list(match.players)  # type: ignore[attr-defined]
        if now_unanimous:
            return [p.user for p in players]
        return [p.user for p in players if p.user_id in opted_in_before]

