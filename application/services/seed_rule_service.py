"""A tournament's seed rules: who may edit them, and the preset a match rolls.

The rule shape, its checks and the matching itself are pure and live in
:mod:`application.services.seed_rules`; this service loads the rules, the
community's presets and a match's facts around them.
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from application.errors import require_found
from application.events import EventType
from application.repositories.preset_repository import PresetRepository
from application.repositories.tournament_repository import TournamentRepository
from application.services import seed_rules
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.bracket_set_service import BracketSetService
from application.services.match.match_hard_preset_service import MatchHardPresetService
from application.services.round_schedule_service import RoundScheduleService
from models import Match, Preset, Tournament, User


@dataclass
class SeedResolution:
    """The preset a roll uses (tweaks already applied) and why.

    ``record`` is what :class:`GeneratedSeeds` keeps as ``resolution``.
    """
    preset: Optional[Preset]
    record: Dict[str, Any] = field(default_factory=dict)


class SeedRuleService:
    def __init__(self) -> None:
        self.tournament_repository = TournamentRepository()
        self.preset_repository = PresetRepository()
        self.audit_service = AuditService()
        self.hard_preset_service = MatchHardPresetService()
        self.round_schedule_service = RoundScheduleService()

    async def _tournament(self, tournament_id: int) -> Tournament:
        return require_found(await self.tournament_repository.get_by_id(tournament_id), 'Tournament')

    async def _ensure_can_edit(self, actor: Optional[User], tournament: Tournament) -> None:
        allowed = (
            await AuthService.can_edit_tournament(actor, tournament)
            or await AuthService.can_manage_presets(actor)
        )
        await AuthService.ensure(allowed, "You can't change this tournament's seed rules.")

    async def _presets(self) -> Dict[int, Preset]:
        return {p.id: p for p in await self.preset_repository.list_all()}

    async def get_rules(self, tournament_id: int, actor: Optional[User]) -> List[dict]:
        tournament = await self._tournament(tournament_id)
        await self._ensure_can_edit(actor, tournament)
        return list(tournament.seed_rules or [])

    async def save_rules(self, tournament_id: int, rules: Any, actor: User) -> List[dict]:
        """Replace the whole rule list. Raises ``ValueError`` naming the bad rule."""
        tournament = await self._tournament(tournament_id)
        await self._ensure_can_edit(actor, tournament)
        cleaned = seed_rules.clean_rules(rules, await self._presets())
        await self.tournament_repository.update(tournament, seed_rules=cleaned or None)
        await self.audit_service.write_and_publish(
            actor, AuditActions.TOURNAMENT_SEED_RULES_UPDATED,
            {'tournament_id': tournament.id, 'rules': len(cleaned)},
            EventType.TOURNAMENT_SEED_RULES_UPDATED,
        )
        return cleaned

    async def export_text(self, tournament_id: int, actor: Optional[User]) -> str:
        rules = await self.get_rules(tournament_id, actor)
        return seed_rules.to_yaml(rules, {pid: p.name for pid, p in (await self._presets()).items()})

    async def parse_text(self, tournament_id: int, text: str, actor: Optional[User]) -> List[dict]:
        """The text view's rules, checked but not saved, for the editor to show."""
        tournament = await self._tournament(tournament_id)
        await self._ensure_can_edit(actor, tournament)
        presets = await self._presets()
        by_name: Dict[str, int] = {}
        for pid, p in sorted(presets.items()):
            by_name.setdefault(p.name, pid)
        return seed_rules.clean_rules(seed_rules.from_yaml(text, by_name), presets)

    async def facts_for(self, match: Match) -> seed_rules.MatchFacts:
        return seed_rules.MatchFacts(
            round=await self.round_schedule_service.round_key_for_match(match),
            game=(await BracketSetService().game_numbers_for([match])).get(match.id),
            best_of=match.set_best_of,
            title=match.title,
            on_stage=match.stage_id is not None,  # type: ignore[attr-defined]
            scheduled_at=match.scheduled_at,
        )

    async def resolve(self, match: Match, *, rng: Optional[random.Random] = None) -> SeedResolution:
        """The preset ``match`` rolls with, in order: staff's override, a unanimous
        hard-mode opt-in, the first seed rule that fits, the tournament's preset.

        ``match.tournament`` (with ``preset`` and ``hard_preset``) must be loaded.
        Asking closes the hard-mode opt-in window, as before.
        """
        decided = await self.hard_preset_service.decided_preset(match)
        if decided is not None:
            preset, source = decided
            return SeedResolution(preset, {'source': source, 'preset_id': preset.id if preset else None})

        tournament = match.tournament
        standard = getattr(tournament, 'preset', None)
        rules = tournament.seed_rules or []
        if rules:
            presets = await self._presets()
            hit = seed_rules.resolve(
                rules, await self.facts_for(match), rng=rng, usable=presets.__contains__,
            )
            if hit is not None:
                return SeedResolution(_with_tweaks(presets[hit.preset_id], hit.tweaks), hit.record())
        return SeedResolution(standard, {'source': 'default', 'preset_id': standard.id if standard else None})


def _with_tweaks(preset: Preset, tweaks: Dict[str, Any]) -> Preset:
    """An unsaved copy of ``preset`` carrying the tweaked settings; the row is untouched."""
    if not tweaks:
        return preset
    tweaked = copy.copy(preset)
    tweaked.settings = seed_rules.apply_tweaks(preset.settings, tweaks)
    return tweaked
