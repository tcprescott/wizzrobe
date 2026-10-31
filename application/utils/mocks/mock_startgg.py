"""The mock start.gg layer for local development (``MOCK_STARTGG``).

Linking any event link returns one canned event shaped like a real one: a
round-robin "Group Prelim" of two finished pools feeding a four-player
"Final Bracket" whose first semifinal is ready to book. The dev seed links
player_one and player_two to the start.gg accounts entrants 9001 and 9002
report, so the mirror maps them the way production maps players who linked
start.gg on their profile; Player Three and Four only match by name, so staff
see suggestions to confirm. It must never be active in production, where it
would show a bracket nobody is in.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from application.utils.environment import env_flag, is_production


def is_mock_startgg() -> bool:
    """Return True when MOCK_STARTGG is enabled (and not in production)."""
    enabled = env_flag('MOCK_STARTGG')
    if enabled and is_production():
        raise RuntimeError(
            'MOCK_STARTGG must not be enabled in production: it shows a canned '
            'start.gg event. Unset MOCK_STARTGG or change ENVIRONMENT.'
        )
    return enabled


MOCK_CONNECTED_USER_ID = '30001'

# Must match the ids the dev seed links to player_one and player_two.
MOCK_LINKED_ACCOUNTS = {'player_one': '39001', 'player_two': '39002'}

_ENTRANTS = [
    ('9001', 'Player One', MOCK_LINKED_ACCOUNTS['player_one']),
    ('9002', 'Player Two', MOCK_LINKED_ACCOUNTS['player_two']),
    ('9003', 'Player Three', None),
    ('9004', 'Player Four', None),
    ('9005', 'Saria', None),
    ('9006', 'Darunia', None),
    ('9007', 'Ruto', None),
    ('9008', 'Nabooru', None),
]
_DONE = datetime(2026, 10, 18, 18, 0, tzinfo=timezone.utc).timestamp()


def _slot(entrant_id, score=None, prereq_type='seed', prereq_id=None, placement=None):
    return {'entrant_id': entrant_id, 'score': score, 'prereq_type': prereq_type,
            'prereq_id': prereq_id, 'prereq_placement': placement}


def _rr_pool(ids: List[str], first: int) -> Dict[str, Any]:
    """Every pairing of a four-player round robin, the lower id winning."""
    pairs = [(0, 3), (1, 2), (0, 2), (1, 3), (0, 1), (2, 3)]
    sets = []
    for n, (a, b) in enumerate(pairs):
        sets.append({
            'set_id': str(first + n), 'round': n // 2 + 1, 'round_text': f'Round {n // 2 + 1}',
            'identifier': chr(ord('A') + n), 'complete': True, 'winner_id': ids[a],
            'completed_at': datetime.fromtimestamp(_DONE, tz=timezone.utc),
            'slots': [_slot(ids[a], 1), _slot(ids[b], 0)],
        })
    return {'standings': [{'entrant_id': e, 'placement': i + 1} for i, e in enumerate(ids)], 'sets': sets}


def mock_event_full(slug: str) -> Dict[str, Any]:
    final_sets = [
        {'set_id': '8101', 'round': 1, 'round_text': 'Semi-Final', 'identifier': 'A', 'complete': False,
         'winner_id': None, 'completed_at': None, 'slots': [_slot('9001'), _slot('9002')]},
        {'set_id': '8102', 'round': 1, 'round_text': 'Semi-Final', 'identifier': 'B', 'complete': False,
         'winner_id': None, 'completed_at': None, 'slots': [_slot('9005'), _slot('9006')]},
        {'set_id': '8103', 'round': 2, 'round_text': 'Grand Final', 'identifier': 'C', 'complete': False,
         'winner_id': None, 'completed_at': None,
         'slots': [_slot(None, prereq_type='set', prereq_id='8101', placement=1),
                   _slot(None, prereq_type='set', prereq_id='8102', placement=1)]},
    ]
    return {
        'event': {
            'event_id': '7001', 'name': 'Mock Singles', 'slug': slug, 'state': 'ACTIVE',
            'phases': [
                {'phase_id': '7101', 'name': 'Group Prelim', 'phase_order': 1,
                 'bracket_type': 'ROUND_ROBIN', 'state': 'COMPLETED',
                 'pools': [{'phase_group_id': '7201', 'identifier': '1', 'bracket_type': 'ROUND_ROBIN', 'complete': True},
                           {'phase_group_id': '7202', 'identifier': '2', 'bracket_type': 'ROUND_ROBIN', 'complete': True}]},
                {'phase_id': '7102', 'name': 'Final Bracket', 'phase_order': 2,
                 'bracket_type': 'SINGLE_ELIMINATION', 'state': 'ACTIVE',
                 'pools': [{'phase_group_id': '7203', 'identifier': '1', 'bracket_type': 'SINGLE_ELIMINATION', 'complete': False}]},
            ],
        },
        'entrants': [
            {'entrant_id': eid, 'name': name, 'gamer_tag': name, 'startgg_user_id': sgg_user, 'discord_id': None}
            for eid, name, sgg_user in _ENTRANTS
        ],
        'pools': {
            '7201': _rr_pool(['9001', '9005', '9003', '9007'], 8001),
            '7202': _rr_pool(['9002', '9006', '9004', '9008'], 8011),
            '7203': {'standings': [], 'sets': final_sets},
        },
    }


class MockStartggClient:
    """Stands in for :class:`StartggClient` under MOCK_STARTGG."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def get_event_full(self, slug: str) -> Dict[str, Any]:
        return mock_event_full(slug)

    async def get_set_state(self, set_id: str) -> Dict[str, Any]:
        return {'complete': False, 'entrant_ids': []}

    async def report_winner(self, set_id: str, winner_entrant_id: str) -> None:
        return None

    async def get_admin_tournament_slugs(self) -> Tuple[Optional[str], Set[str]]:
        return MOCK_CONNECTED_USER_ID, set()

    async def get_tournament_admin_ids(self, slug: str) -> Set[str]:
        return {MOCK_CONNECTED_USER_ID}
