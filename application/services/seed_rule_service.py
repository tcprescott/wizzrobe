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
from application.repositories.bracket_repository import BracketRepository
from application.repositories.match_repository import MatchRepository
from application.repositories.preset_repository import PresetRepository
from application.repositories.tournament_repository import TournamentRepository
from application.services import seed_rules
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.bracket_set_service import BracketSetService
from application.services.match.match_hard_preset_service import MatchHardPresetService
from application.services.round_schedule_service import RoundScheduleService
from application.utils.match_labels import match_model_label
from models import GeneratedSeeds, Match, Preset, Tournament, User


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
        self.match_repository = MatchRepository()
        self.bracket_repository = BracketRepository()

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
        cleaned = self._clean(tournament, rules, await self._presets())
        await self.tournament_repository.update(tournament, seed_rules=cleaned or None)
        await self.audit_service.write_and_publish(
            actor, AuditActions.TOURNAMENT_SEED_RULES_UPDATED,
            {'tournament_id': tournament.id, 'rules': len(cleaned)},
            EventType.TOURNAMENT_SEED_RULES_UPDATED,
        )
        return cleaned

    @staticmethod
    def _clean(tournament: Tournament, rules: Any, presets: Dict[int, Preset]) -> List[dict]:
        """:func:`seed_rules.clean_rules`, plus: every preset rolls on this tournament's randomizer."""
        cleaned = seed_rules.clean_rules(rules, presets)
        randomizer = tournament.seed_generator
        if randomizer:
            for n, rule in enumerate(cleaned, start=1):
                for pid in sorted(seed_rules.preset_ids([rule])):
                    if presets[pid].randomizer != randomizer:
                        raise ValueError(
                            f"Rule {n}: {presets[pid].name} is a {presets[pid].randomizer} preset, "
                            f"but this tournament rolls {randomizer}."
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
            if not tournament.seed_generator or p.randomizer == tournament.seed_generator:
                by_name.setdefault(p.name, pid)
        for pid, p in sorted(presets.items()):
            by_name.setdefault(p.name, pid)
        return self._clean(tournament, seed_rules.from_yaml(text, by_name), presets)

    async def editor(self, tournament_id: int, actor: Optional[User]) -> Dict[str, Any]:
        """Everything the Seed rules tab needs: the rules, the presets a rule may
        name, the round keys the bracket offers and matches to preview."""
        tournament = await self._tournament(tournament_id)
        await self._ensure_can_edit(actor, tournament)
        await tournament.fetch_related('preset')
        presets = [
            p for p in await self.preset_repository.list_all()
            if not tournament.seed_generator or p.randomizer == tournament.seed_generator
        ]
        matches = await self.match_repository.list_unseeded_for_tournament(tournament.id)
        return {
            'tournament_id': tournament.id,
            'randomizer': tournament.seed_generator,
            'rules': list(tournament.seed_rules or []),
            'default_preset': _preset_ref(tournament.preset),
            'presets': [_preset_ref(p) for p in presets],
            'rounds': await self._rounds(tournament),
            'matches': [
                {'id': m.id, 'label': match_model_label(m, with_context=False), 'scheduled_at': m.scheduled_at}
                for m in matches
            ],
            'max_rules': seed_rules.MAX_RULES,
        }

    async def _rounds(self, tournament: Tournament) -> List[Dict[str, str]]:
        try:
            stages = await self.round_schedule_service.stages_for(tournament)
        except ValueError:
            stages = []
        if stages:
            return [{'key': s.key, 'label': s.label} for s in stages]
        top = 0
        for bracket in await self.bracket_repository.list_for_tournament(tournament.id):
            top = max(top, await self.bracket_repository.max_round(bracket.id) or 0)
        return [{'key': f'round:{n}', 'label': f'Round {n}'} for n in range(1, top + 1)]

    async def preview(
        self, tournament_id: int, actor: Optional[User], *,
        rules: Any = None, match_id: Optional[int] = None,
        facts: Optional[seed_rules.MatchFacts] = None,
    ) -> Dict[str, Any]:
        """Which rule a match (real or made up) would fire, without rolling.

        ``rules`` previews an unsaved draft; left out, the saved rules. A real
        match brings its own facts. Staff's override and hard mode aren't
        consulted: asking them would close the opt-in window.
        """
        tournament = await self._tournament(tournament_id)
        await self._ensure_can_edit(actor, tournament)
        await tournament.fetch_related('preset')
        presets = await self._presets()
        draft = self._clean(tournament, rules, presets) if rules is not None else list(tournament.seed_rules or [])
        label = None
        if match_id is not None:
            match = require_found(await self.match_repository.get_by_id(match_id), 'Match')
            if match.tournament_id != tournament.id:  # type: ignore[attr-defined]
                raise ValueError('That match is in another tournament.')
            facts = await self.facts_for(match)
            label = match_model_label(match, with_context=False)
        facts = facts or seed_rules.MatchFacts()
        found = seed_rules.first_fitting(draft, facts, usable=presets.__contains__)
        out: Dict[str, Any] = {
            'facts': {
                'round': facts.round, 'game': facts.game, 'best_of': facts.best_of, 'title': facts.title,
                'on_stage': facts.on_stage, 'scheduled_at': facts.scheduled_at,
            },
            'match_label': label,
        }
        if found is None:
            preset = tournament.preset
            out.update({
                'source': 'default', 'rule_id': None, 'rule_number': None, 'rule_name': None,
                'outcomes': [dict(_preset_ref(preset) or {}, weight=1, percent=100.0)] if preset else [],
                'tweaks': {},
                'settings': preset.settings if preset else None,
            })
            return out
        index, rule, entries = found
        total = sum(e['weight'] for e in entries)
        tweaks = dict(rule.get('tweaks') or {})
        out.update({
            'source': 'rule', 'rule_id': rule['id'], 'rule_number': index + 1, 'rule_name': rule['name'],
            'outcomes': [
                dict(_preset_ref(presets[e['preset']]) or {}, weight=e['weight'],
                     percent=round(100 * e['weight'] / total, 1))
                for e in entries
            ],
            'tweaks': tweaks,
            'settings': (
                seed_rules.apply_tweaks(presets[entries[0]['preset']].settings, tweaks)
                if len(entries) == 1 else None
            ),
        })
        return out

    async def why(self, seed: Optional[GeneratedSeeds]) -> Optional[str]:
        """Why a rolled seed rolled what it did (``Rule 3: Top 8 → Open Hard``), for staff and proctors."""
        record = getattr(seed, 'resolution', None) if seed is not None else None
        if not record:
            return None
        return seed_rules.describe(record, {pid: p.name for pid, p in (await self._presets()).items()})

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


def _preset_ref(preset: Optional[Preset]) -> Optional[Dict[str, Any]]:
    if preset is None:
        return None
    return {'id': preset.id, 'name': preset.name, 'randomizer': preset.randomizer}


def _with_tweaks(preset: Preset, tweaks: Dict[str, Any]) -> Preset:
    """An unsaved copy of ``preset`` carrying the tweaked settings; the row is untouched."""
    if not tweaks:
        return preset
    tweaked = copy.copy(preset)
    tweaked.settings = seed_rules.apply_tweaks(preset.settings, tweaks)
    return tweaked
