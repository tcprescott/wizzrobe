"""Staff forcing one match's preset over every seed rule, or handing it back.

``Match.forced_preset`` names any preset of the community that fits the
tournament's randomizer. It beats every seed rule, hard mode included (hard
mode is a hidden match form question plus a rule since migration 170). It is
deliberately visible: it is staff's decision about the match, and the players
are told.

Refused once the seed is rolled. From that point ``GeneratedSeeds.preset`` is
the record of what was actually played.
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from application.errors import require_found
from application.events import EventType
from application.repositories import MatchRepository, PresetRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from models import Match, Preset, User

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PresetOverrideState:
    """What the override control needs to know about one match."""

    locked: bool = False
    """The seed is rolled; the choice is closed."""

    forced_preset_id: Optional[int] = None
    """The preset staff forced, if any."""

    choices: List[Tuple[int, str]] = field(default_factory=list)
    """``(id, name)`` of every preset staff may force: the tournament's randomizer's."""


class MatchPresetOverrideService:
    """Staff's per-match preset override."""

    def __init__(self) -> None:
        self.match_repository = MatchRepository()
        self.preset_repository = PresetRepository()
        self.audit_service = AuditService()

    async def _require_match(self, match_id: int) -> Match:
        match = require_found(
            await self.match_repository.get_by_id(match_id),
            f"Match {match_id}",
        )
        await match.fetch_related('tournament', 'tournament__preset', 'forced_preset')
        return match

    async def _choices(self, match: Match) -> List[Preset]:
        randomizer = match.tournament.seed_generator
        return [
            p for p in await self.preset_repository.list_all()
            if not randomizer or p.randomizer == randomizer
        ]

    async def state(self, match: Match) -> PresetOverrideState:
        """The override control's facts for ``match`` (``tournament`` loaded)."""
        presets = await self._choices(match)
        return PresetOverrideState(
            locked=match.generated_seed_id is not None,  # type: ignore[attr-defined]
            forced_preset_id=match.forced_preset_id,  # type: ignore[attr-defined]
            choices=[(p.id, p.name) for p in sorted(presets, key=lambda p: p.name.lower())],
        )

    async def decided_preset(self, match: Match) -> Optional[Preset]:
        """The preset staff forced on ``match``, else ``None`` (seed rules and then
        the tournament's preset decide). Loads ``forced_preset`` if needed."""
        if match.forced_preset_id is None:  # type: ignore[attr-defined]
            return None
        await match.fetch_related('forced_preset')
        return match.forced_preset

    async def set_override(
        self, match_id: int, preset_id: Optional[int], actor: User,
    ) -> Match:
        """Staff forcing a match's preset, or handing it back (``None``).

        The players' match form answers are left in place, so clearing the
        override hands the match back to the seed rules and what they answered.
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
        preset: Optional[Preset] = None
        if preset_id is not None:
            preset = require_found(await self.preset_repository.get_by_id(preset_id), 'Preset')
            randomizer = match.tournament.seed_generator
            if randomizer and preset.randomizer != randomizer:
                raise ValueError(
                    f'{preset.name} is a {preset.randomizer} preset, and this tournament rolls {randomizer}.'
                )
        if match.forced_preset_id == preset_id:  # type: ignore[attr-defined]
            return match

        await self.match_repository.update(match, forced_preset_id=preset_id)
        match.forced_preset = preset  # type: ignore[assignment]

        action = (
            AuditActions.MATCH_PRESET_OVERRIDE_SET if preset is not None
            else AuditActions.MATCH_PRESET_OVERRIDE_CLEARED
        )
        event = (
            EventType.MATCH_PRESET_OVERRIDE_SET if preset is not None
            else EventType.MATCH_PRESET_OVERRIDE_CLEARED
        )
        await self.audit_service.write_and_publish(
            actor, action,
            {'match_id': match_id, 'preset_id': preset_id, 'preset_name': preset.name if preset else None},
            event,
            event_extra={'tournament_id': match.tournament_id},  # type: ignore[attr-defined]
        )
        await self._announce_override(match, preset)
        return match

    async def _announce_override(self, match: Match, preset: Optional[Preset]) -> None:
        from application.services.discord import discord_queue
        from application.services.match._preset_override_notifications import (
            notify_preset_override,
        )

        await match.fetch_related('players__user')
        players = list(match.players)  # type: ignore[attr-defined]
        discord_queue.enqueue(notify_preset_override(
            match_id=match.id,
            tournament_name=match.tournament.name,
            preset_name=preset.name if preset is not None else '',
            when=match.scheduled_at,
            recipients=[p.user for p in players],
            forced=preset is not None,
        ))
