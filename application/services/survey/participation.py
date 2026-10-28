"""What everyone did at one event, and the facts each survey recipient gets.

:class:`Participation` reads the event once (a handful of bulk queries) and
turns it into a ``facts`` dict per person: the tournaments they played, the
tournaments they ran (as a tournament admin) and the positions they worked
(which decide the repeating sections), whether they crewed or checked in, and every registered condition's answer. Those facts are
frozen onto the recipient row when the survey is sent.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from application.repositories.survey_participation_repository import SurveyParticipationRepository
from application.services.survey.conditions import CONDITIONS


@dataclass
class Participation:
    event_id: int
    tournaments: Dict[int, Dict[int, dict]] = field(default_factory=dict)
    positions: Dict[int, Dict[int, dict]] = field(default_factory=dict)
    organized: Dict[int, Dict[int, dict]] = field(default_factory=dict)
    on_stage: Dict[int, Set[int]] = field(default_factory=dict)
    room_tournaments: Dict[int, Set[int]] = field(default_factory=dict)
    crew: Set[int] = field(default_factory=set)
    crew_roles: Dict[str, Set[int]] = field(default_factory=dict)
    checked_in: Set[int] = field(default_factory=set)
    _reschedules: Optional[Set[Tuple[int, int]]] = None

    @classmethod
    async def load(cls, event_id: int) -> 'Participation':
        repo = SurveyParticipationRepository
        p = cls(event_id)
        for row in await repo.match_rows(event_id):
            user_id, tournament_id = int(row['user_id']), int(row['tournament_id'])
            played = p.tournaments.setdefault(user_id, {})
            entry = played.setdefault(tournament_id, {
                'id': tournament_id,
                'name': row['tournament_short'] or row['tournament_name'],
                'matches': 0,
            })
            entry['matches'] += 1
            if row['stage_id'] is not None:
                p.on_stage.setdefault(user_id, set()).add(tournament_id)
            if row['uses_room']:
                p.room_tournaments.setdefault(user_id, set()).add(tournament_id)
        for row in await repo.shift_rows(event_id):
            user_id, position_id = int(row['user_id']), int(row['position_id'])
            worked = p.positions.setdefault(user_id, {})
            entry = worked.setdefault(position_id, {'id': position_id, 'name': row['position_name'], 'shifts': 0})
            entry['shifts'] += 1
        for row in await repo.organizer_rows(event_id):
            tournament_id = int(row['id'])
            p.organized.setdefault(int(row['user_id']), {})[tournament_id] = {
                'id': tournament_id, 'name': row['short_name'] or row['name'],
            }
        p.crew_roles = await repo.crew_user_ids(event_id)
        p.crew = set().union(*p.crew_roles.values()) if p.crew_roles else set()
        p.checked_in = await repo.checked_in_user_ids(event_id)
        return p

    async def reschedules(self) -> Set[Tuple[int, int]]:
        if self._reschedules is None:
            self._reschedules = await SurveyParticipationRepository.reschedule_pairs(self.event_id)
        return self._reschedules

    def everyone(self) -> Set[int]:
        """Anyone who took part: played, ran a tournament, worked a shift, crewed or checked in."""
        return set(self.tournaments) | set(self.organized) | set(self.positions) | self.crew | self.checked_in

    async def facts_by_user(self, user_ids: Optional[Set[int]] = None) -> Dict[int, dict]:
        """The frozen facts for each of ``user_ids`` (everyone who took part by default)."""
        users = self.everyone() if user_ids is None else user_ids
        collected = {
            key: await condition.collect(self)
            for key, condition in CONDITIONS.items() if condition.collect is not None
        }
        facts: Dict[int, dict] = {}
        for user_id in users:
            facts[user_id] = {
                'tournaments': sorted(self.tournaments.get(user_id, {}).values(), key=lambda t: t['name'].lower()),
                'organized': sorted(self.organized.get(user_id, {}).values(), key=lambda t: t['name'].lower()),
                'positions': sorted(self.positions.get(user_id, {}).values(), key=lambda p: p['name'].lower()),
                'crew': user_id in self.crew,
                'crew_roles': [role for role, users in self.crew_roles.items() if user_id in users],
                'checked_in': user_id in self.checked_in,
                'conditions': {key: values[user_id] for key, values in collected.items() if user_id in values},
            }
        return facts

    def most_tournaments(self) -> int:
        return max((len(t) for t in self.tournaments.values()), default=0)

    def most_positions(self) -> int:
        return max((len(p) for p in self.positions.values()), default=0)

    def tournament_names(self) -> List[dict]:
        seen: Dict[int, dict] = {}
        for played in self.tournaments.values():
            for t in played.values():
                seen.setdefault(t['id'], {'id': t['id'], 'name': t['name']})
        return sorted(seen.values(), key=lambda t: t['name'].lower())

    def position_names(self) -> List[dict]:
        seen: Dict[int, dict] = {}
        for worked in self.positions.values():
            for p in worked.values():
                seen.setdefault(p['id'], {'id': p['id'], 'name': p['name']})
        return sorted(seen.values(), key=lambda p: p['name'].lower())

    def counts(self) -> Dict[str, int]:
        """How many people each audience reaches, for the builder's "Who gets it"."""
        everyone = self.everyone()
        only_checked_in = self.checked_in - set(self.tournaments) - set(self.organized) - set(self.positions) - self.crew
        return {
            'players': len(self.tournaments),
            'organizers': len(self.organized),
            'volunteers': len(self.positions),
            'crew': len(self.crew),
            **{f'crew_{role}': len(users) for role, users in self.crew_roles.items()},
            'checked_in_only': len(only_checked_in),
            'everyone': len(everyone),
        }
