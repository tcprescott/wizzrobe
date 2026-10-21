"""Build the tournament export: one JSON document, flattened into CSVs for the ZIP.

The JSON document is the source; the CSVs are a flattening of it, so the two
never disagree. Its shape is versioned (``schema_version``) and documented in
``docs/features/data-exports.md`` with a JSON Schema beside it. Adding a field
is a minor change; renaming or removing one bumps the version.

A participant's standing comes from whichever bracket the tournament runs on:

* **Wizzrobe brackets** store it: entrant status, each stage's seed and final
  rank, and every bracket match's winner.
* **start.gg** mirrors placements per pool and every set's winner.
* **Challonge** mirrors every match's winner but not placements, so placement
  is blank there.

For the two bracket sites, status is read from the synced matches: someone with
an unfinished match is ``active``, someone whose last match was a loss is
``eliminated``, and once the bracket is over everyone else is ``finished``. A
tournament with no bracket has no status; wins and losses come from its
finished Wizzrobe matches (first place counts as a win).

All times are UTC, ISO 8601, like the REST API.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from application.repositories import TournamentExportRepository
from application.utils.csv_export import files_to_zip_bytes, rows_to_csv_bytes
from application.utils.match_labels import challonge_round_label
from models import (
    BracketEntrantStatus,
    BracketEntryStatus,
    BracketMatchState,
    BracketState,
    ChallongeMatchState,
    Match,
    StartggSetState,
    Tournament,
    User,
)

FORMAT_NAME = 'wizzrobe-tournament-export'
SCHEMA_VERSION = 1

SITE_WIZZROBE = 'wizzrobe'
SITE_CHALLONGE = 'challonge'
SITE_STARTGG = 'startgg'


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def _discord_id(user: Optional[User]) -> Optional[str]:
    return str(user.discord_id) if user is not None and user.discord_id else None


def _discord_username(user: Optional[User]) -> Optional[str]:
    return user.username if user is not None and user.discord_id else None


def bracket_site(tournament: Tournament, has_native: bool) -> Optional[str]:
    if tournament.startgg_event_slug:
        return SITE_STARTGG
    if tournament.challonge_tournament_id:
        return SITE_CHALLONGE
    if has_native:
        return SITE_WIZZROBE
    return None


@dataclass
class _Participant:
    user: Optional[User] = None
    bracket_name: Optional[str] = None
    enrolled: bool = False
    status: Optional[str] = None
    seed: Optional[int] = None
    placement: Optional[int] = None
    wins: int = 0
    losses: int = 0

    def as_dict(self) -> Dict[str, Any]:
        user = self.user
        return {
            'player_id': user.id if user is not None else None,
            'name': user.preferred_name if user is not None else (self.bracket_name or ''),
            'discord_username': _discord_username(user),
            'discord_id': _discord_id(user),
            'bracket_name': self.bracket_name,
            'enrolled': self.enrolled,
            'status': self.status,
            'seed': self.seed,
            'placement': self.placement,
            'wins': self.wins,
            'losses': self.losses,
        }


@dataclass
class _Roster:
    """Participants keyed by member when linked, else by the bracket's own entrant key."""

    rows: Dict[Tuple[str, Any], _Participant] = field(default_factory=dict)

    def enrol(self, user: User) -> None:
        self.rows[('user', user.id)] = _Participant(user=user, enrolled=True)

    def entrant(self, key: Any, user: Optional[User], name: Optional[str]) -> _Participant:
        if user is not None:
            row = self.rows.setdefault(('user', user.id), _Participant(user=user))
        else:
            row = self.rows.setdefault(('entrant', key), _Participant())
        row.bracket_name = name
        return row

    def ordered(self) -> List[_Participant]:
        def sort_key(p: _Participant):
            return (
                p.placement is None, p.placement or 0,
                p.seed is None, p.seed or 0,
                (p.user.preferred_name if p.user else p.bracket_name or '').lower(),
            )
        return sorted(self.rows.values(), key=sort_key)


def _external_status(open_count: int, last_result: Optional[bool], bracket_over: bool) -> str:
    if open_count:
        return 'active'
    if last_result is False:
        return 'eliminated'
    return 'finished' if bracket_over else 'active'


def _match_state(match: Match) -> str:
    if match.confirmed_at is not None:
        return 'confirmed'
    return {
        'Finished': 'finished', 'In Progress': 'in_progress',
        'Checked In': 'checked_in', 'Scheduled': 'scheduled',
    }[match.current_state]


@dataclass
class _BracketLink:
    site: str
    bracket_match_id: str
    round: str
    scores: Dict[int, Optional[int]] = field(default_factory=dict)


async def build_tournament(tournament: Tournament) -> Dict[str, Any]:
    """One tournament's section of the document. Reads one tournament at a time."""
    repo = TournamentExportRepository
    roster = _Roster()
    enrolled = await repo.enrolled(tournament.id)
    for row in enrolled:
        roster.enrol(row.user)
    enrolled_ids = {row.user_id for row in enrolled}

    brackets = await repo.native_brackets(tournament.id)
    site = bracket_site(tournament, bool(brackets))
    links: Dict[int, _BracketLink] = {}
    unbooked: List[Dict[str, Any]] = []

    if site == SITE_WIZZROBE:
        await _native(tournament, brackets, roster, links, unbooked)
    elif site == SITE_CHALLONGE:
        await _challonge(tournament, roster, links, unbooked)
    elif site == SITE_STARTGG:
        await _startgg(tournament, roster, links, unbooked)

    for row in roster.rows.values():
        if row.user is not None and row.user.id in enrolled_ids:
            row.enrolled = True

    matches = await repo.matches(tournament.id)
    if site is None:
        _results_from_matches(roster, matches)

    match_rows = [_match_row(m, links.get(m.id), site) for m in matches]
    return {
        'id': tournament.id,
        'name': tournament.name,
        'short_name': tournament.short_name,
        'format': tournament.tournament_format,
        'active': tournament.is_active,
        'test': tournament.is_test,
        'bracket': {
            'site': site,
            'url': tournament.challonge_tournament_url if site == SITE_CHALLONGE else tournament.bracket_url,
        },
        'participants': [p.as_dict() for p in roster.ordered()],
        'matches': match_rows + unbooked,
    }


async def _native(
    tournament: Tournament, brackets, roster: _Roster,
    links: Dict[int, _BracketLink], unbooked: List[Dict[str, Any]],
) -> None:
    repo = TournamentExportRepository
    order = {b.id: (b.stage_order, b.id) for b in brackets}
    by_bracket = {b.id: b for b in brackets}
    entrants = {e.id: e for e in await repo.native_entrants(tournament.id)}
    entries = sorted(await repo.native_entries(tournament.id), key=lambda e: order.get(e.bracket_id, (0, 0)))
    rows = {
        e.id: roster.entrant(('native', e.id), e.user, e.display_name)
        for e in entrants.values()
    }
    entry_owner: Dict[int, int] = {}
    for entry in entries:
        entry_owner[entry.id] = entry.entrant_id
        row = rows.get(entry.entrant_id)
        if row is None:
            continue
        if row.seed is None and entry.seed is not None:
            row.seed = entry.seed
        if entry.final_rank is not None:
            row.placement = entry.final_rank
        bracket = by_bracket[entry.bracket_id]
        if entry.status == BracketEntryStatus.DROPPED:
            row.status = 'dropped'
        elif entry.status == BracketEntryStatus.ELIMINATED:
            row.status = 'eliminated'
        elif bracket.state in (BracketState.COMPLETE, BracketState.CANCELLED):
            row.status = 'finished'
        else:
            row.status = 'active'
    for entrant_id, row in rows.items():
        if entrants[entrant_id].status == BracketEntrantStatus.DROPPED:
            row.status = 'dropped'
        elif row.status is None:
            row.status = 'active'

    for bm in await repo.native_matches(tournament.id):
        owners = [entry_owner.get(bm.entry1_id), entry_owner.get(bm.entry2_id)]
        if bm.state == BracketMatchState.COMPLETE and bm.winner_id:
            winner = entry_owner.get(bm.winner_id)
            for owner in owners:
                if owner is None or owner not in rows:
                    continue
                if owner == winner:
                    rows[owner].wins += 1
                else:
                    rows[owner].losses += 1
        bracket = by_bracket[bm.bracket_id]
        label = f"{bracket.name} · {'Losers round' if bm.round < 0 else 'Round'} {abs(bm.round)}"
        link = _BracketLink(SITE_WIZZROBE, str(bm.id), label)
        booked = False
        for game in bm.games:
            if game.match_id is not None:
                links[game.match_id] = link
                booked = True
        if not booked and all(owners):
            winner = entry_owner.get(bm.winner_id)
            unbooked.append(_bracket_only_row(
                SITE_WIZZROBE, str(bm.id), label,
                complete=bm.state == BracketMatchState.COMPLETE,
                finished_at=None,
                players=[
                    (rows.get(owners[0]), bm.entry1_score, winner == owners[0]),
                    (rows.get(owners[1]), bm.entry2_score, winner == owners[1]),
                ],
                has_winner=bm.winner_id is not None,
            ))


async def _challonge(
    tournament: Tournament, roster: _Roster,
    links: Dict[int, _BracketLink], unbooked: List[Dict[str, Any]],
) -> None:
    repo = TournamentExportRepository
    rows = {
        p.id: roster.entrant(('challonge', p.id), p.user, p.name)
        for p in await repo.challonge_participants(tournament.id)
    }
    matches = await repo.challonge_matches(tournament.id)
    over = (tournament.challonge_state or '').lower() in ('complete', 'ended', 'awaiting_review')
    _tally_external(
        rows,
        (
            (cm.participant1_id, cm.participant2_id, cm.winner_participant_id,
             cm.state == ChallongeMatchState.COMPLETE)
            for cm in matches
        ),
        bracket_over=over or (bool(matches) and all(cm.state == ChallongeMatchState.COMPLETE for cm in matches)),
    )
    for cm in matches:
        label = 'Group stage' if cm.group_stage else challonge_round_label(cm.round)
        link = _BracketLink(SITE_CHALLONGE, cm.challonge_match_id, label)
        if cm.match_id is not None:
            links[cm.match_id] = link
        elif cm.participant1_id and cm.participant2_id:
            unbooked.append(_bracket_only_row(
                SITE_CHALLONGE, cm.challonge_match_id, label,
                complete=cm.state == ChallongeMatchState.COMPLETE,
                finished_at=None,
                players=[
                    (rows.get(cm.participant1_id), None, cm.winner_participant_id == cm.participant1_id),
                    (rows.get(cm.participant2_id), None, cm.winner_participant_id == cm.participant2_id),
                ],
                has_winner=cm.winner_participant_id is not None,
            ))


async def _startgg(
    tournament: Tournament, roster: _Roster,
    links: Dict[int, _BracketLink], unbooked: List[Dict[str, Any]],
) -> None:
    repo = TournamentExportRepository
    entrants = await repo.startgg_entrants(tournament.id)
    rows = {e.id: roster.entrant(('startgg', e.id), e.user, e.gamer_tag or e.name) for e in entrants}
    by_entrant_key = {e.entrant_id: e.id for e in entrants}
    pools = await repo.startgg_pools(tournament.id)
    # Later phases override earlier ones, so a player's placement is the deepest
    # phase they reached.
    for pool in sorted(pools, key=lambda p: (p.phase.phase_order, p.id)):
        for standing in pool.standings or []:
            entrant_pk = by_entrant_key.get(str(standing.get('entrant_id')))
            if entrant_pk in rows and standing.get('placement') is not None:
                rows[entrant_pk].placement = int(standing['placement'])
    sets = await repo.startgg_sets(tournament.id)
    _tally_external(
        rows,
        (
            (s.entrant1_id, s.entrant2_id, s.winner_id, s.state == StartggSetState.COMPLETE)
            for s in sets
        ),
        bracket_over=bool(pools) and all(p.complete for p in pools),
    )
    for s in sets:
        label = s.round_text or f'Round {s.round}'
        link = _BracketLink(SITE_STARTGG, s.set_id, label, {1: s.score1, 2: s.score2})
        if s.match_id is not None:
            links[s.match_id] = link
        elif s.entrant1_id and s.entrant2_id:
            unbooked.append(_bracket_only_row(
                SITE_STARTGG, s.set_id, label,
                complete=s.state == StartggSetState.COMPLETE,
                finished_at=s.completed_at,
                players=[
                    (rows.get(s.entrant1_id), s.score1, s.winner_id == s.entrant1_id),
                    (rows.get(s.entrant2_id), s.score2, s.winner_id == s.entrant2_id),
                ],
                has_winner=s.winner_id is not None,
            ))


def _tally_external(
    rows: Dict[int, _Participant],
    matches: Iterable[Tuple[Optional[int], Optional[int], Optional[int], bool]],
    *, bracket_over: bool,
) -> None:
    open_count: Dict[int, int] = {}
    last: Dict[int, bool] = {}
    for one, two, winner, complete in matches:
        for side in (one, two):
            if side is None or side not in rows:
                continue
            if not complete:
                open_count[side] = open_count.get(side, 0) + 1
            elif winner is not None:
                won = side == winner
                last[side] = won
                if won:
                    rows[side].wins += 1
                else:
                    rows[side].losses += 1
    for key, row in rows.items():
        row.status = _external_status(open_count.get(key, 0), last.get(key), bracket_over)


def _results_from_matches(roster: _Roster, matches: List[Match]) -> None:
    for match in matches:
        if match.finished_at is None or len(match.players) < 2:
            continue
        for mp in match.players:
            if mp.finish_rank is None:
                continue
            row = roster.rows.get(('user', mp.user_id))
            if row is None:
                continue
            if mp.finish_rank == 1:
                row.wins += 1
            else:
                row.losses += 1


def _player(
    user: Optional[User], *, name: Optional[str] = None, place: Optional[int] = None,
    finish_time: Optional[int] = None, score: Optional[int] = None,
) -> Dict[str, Any]:
    return {
        'player_id': user.id if user is not None else None,
        'name': user.preferred_name if user is not None else (name or ''),
        'discord_id': _discord_id(user),
        'place': place,
        'finish_time_seconds': finish_time,
        'score': score,
    }


def _match_row(match: Match, link: Optional[_BracketLink], site: Optional[str]) -> Dict[str, Any]:
    players = []
    for index, mp in enumerate(match.players, start=1):
        players.append(_player(
            mp.user, place=mp.finish_rank, finish_time=mp.finish_time,
            score=link.scores.get(index) if link else None,
        ))
    winner = next((p for p in players if p['place'] == 1), None)
    return {
        'match_id': match.id,
        'source': SITE_WIZZROBE,
        'bracket_site': link.site if link else None,
        'bracket_match_id': link.bracket_match_id if link else None,
        'round': link.round if link else None,
        'title': match.title,
        'state': _match_state(match),
        'scheduled_at': _iso(match.scheduled_at),
        'started_at': _iso(match.started_at),
        'finished_at': _iso(match.finished_at),
        'confirmed_at': _iso(match.confirmed_at),
        'stage': match.stage.name if match.stage else None,
        'seed_url': match.generated_seed.seed_url if match.generated_seed else None,
        'players': players,
        'winner_player_id': winner['player_id'] if winner else None,
        'winner_name': winner['name'] if winner else None,
    }


def _bracket_only_row(
    site: str, bracket_match_id: str, label: str, *, complete: bool,
    finished_at: Optional[datetime],
    players: List[Tuple[Optional[_Participant], Optional[int], bool]],
    has_winner: bool,
) -> Dict[str, Any]:
    out = []
    for participant, score, won in players:
        user = participant.user if participant else None
        name = participant.bracket_name if participant else None
        place = (1 if won else 2) if complete and has_winner else None
        out.append(_player(user, name=name, place=place, score=score))
    winner = next((p for p in out if p['place'] == 1), None)
    return {
        'match_id': None,
        'source': site,
        'bracket_site': site,
        'bracket_match_id': bracket_match_id,
        'round': label,
        'title': None,
        'state': 'reported' if complete else 'unscheduled',
        'scheduled_at': None,
        'started_at': None,
        'finished_at': _iso(finished_at),
        'confirmed_at': None,
        'stage': None,
        'seed_url': None,
        'players': out,
        'winner_player_id': winner['player_id'] if winner else None,
        'winner_name': winner['name'] if winner else None,
    }


def document(community_slug: str, community_name: str, tournaments: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        'format': FORMAT_NAME,
        'schema_version': SCHEMA_VERSION,
        'generated_at': _iso(datetime.now(timezone.utc)),
        'community': {'slug': community_slug, 'name': community_name},
        'tournaments': tournaments,
    }


def document_json(doc: Dict[str, Any]) -> bytes:
    return json.dumps(doc, ensure_ascii=False, indent=2).encode('utf-8')


PARTICIPANT_COLUMNS = [
    ('player_id', 'Player ID'), ('name', 'Name'), ('discord_username', 'Discord username'),
    ('discord_id', 'Discord ID'), ('bracket_name', 'Bracket name'), ('enrolled', 'Enrolled'),
    ('status', 'Status'), ('seed', 'Seed'), ('placement', 'Placement'),
    ('wins', 'Wins'), ('losses', 'Losses'),
]
_MATCH_COLUMNS = [
    ('match_id', 'Match ID'), ('source', 'Source'), ('bracket_match_id', 'Bracket match ID'),
    ('round', 'Round'), ('title', 'Title'), ('state', 'State'), ('scheduled_at', 'Scheduled (UTC)'),
    ('started_at', 'Started (UTC)'), ('finished_at', 'Finished (UTC)'),
    ('confirmed_at', 'Confirmed (UTC)'), ('stage', 'Stage'), ('seed_url', 'Seed'),
]


def _columns(pairs) -> List[Dict[str, str]]:
    return [{'name': name, 'label': label, 'field': name} for name, label in pairs]


def participants_csv(section: Dict[str, Any]) -> bytes:
    return rows_to_csv_bytes(_columns(PARTICIPANT_COLUMNS), section['participants'])


def matches_csv(section: Dict[str, Any]) -> bytes:
    """One row per match, with ``player_N_…`` columns for as many players as the busiest match."""
    slots = max((len(m['players']) for m in section['matches']), default=2) or 2
    pairs = list(_MATCH_COLUMNS)
    for n in range(1, slots + 1):
        pairs += [
            (f'player_{n}', f'Player {n}'), (f'player_{n}_id', f'Player {n} ID'),
            (f'player_{n}_discord_id', f'Player {n} Discord ID'),
            (f'player_{n}_place', f'Player {n} place'),
            (f'player_{n}_time', f'Player {n} time (s)'),
            (f'player_{n}_score', f'Player {n} score'),
        ]
    pairs += [('winner_name', 'Winner')]
    rows = []
    for match in section['matches']:
        row = {k: v for k, v in match.items() if k != 'players'}
        for n, player in enumerate(match['players'], start=1):
            row[f'player_{n}'] = player['name']
            row[f'player_{n}_id'] = player['player_id']
            row[f'player_{n}_discord_id'] = player['discord_id']
            row[f'player_{n}_place'] = player['place']
            row[f'player_{n}_time'] = player['finish_time_seconds']
            row[f'player_{n}_score'] = player['score']
        rows.append(row)
    return rows_to_csv_bytes(_columns(pairs), rows)


def readme(doc: Dict[str, Any], folders: Dict[int, str]) -> str:
    lines = [
        f"{doc['community']['name'] or doc['community']['slug']} tournament export from Wizzrobe",
        f"Generated {doc['generated_at']} (UTC)",
        '',
        'tournaments.json holds everything below in one file (format',
        f"{FORMAT_NAME}, schema version {SCHEMA_VERSION}).",
        '',
        'Each tournament has a folder with two files:',
        '  participants.csv  one row per player: status, seed, placement, wins, losses',
        '  matches.csv       one row per match, with each player\'s place, time and score',
        '',
        'Status is active, eliminated, dropped or finished; blank when the',
        'tournament has no bracket. Challonge does not share placements, so',
        'Placement is blank for Challonge tournaments. Times are UTC.',
        '',
        'Folders',
        '-------',
    ]
    for section in doc['tournaments']:
        lines.append(
            f"{folders[section['id']]}/  {section['name']} "
            f"({len(section['participants'])} players, {len(section['matches'])} matches)"
        )
    return '\n'.join(lines) + '\n'


def zip_bundle(doc: Dict[str, Any]) -> Tuple[bytes, bytes]:
    """``(zip bytes, json bytes)`` for a finished document."""
    json_bytes = document_json(doc)
    folders = {}
    used: set = set()
    for section in doc['tournaments']:
        name = re.sub(r'[^a-z0-9]+', '-', section['name'].lower()).strip('-')[:60] or 'tournament'
        name = f"{name}-{section['id']}"
        while name in used:
            name += '_'
        used.add(name)
        folders[section['id']] = name
    files: Dict[str, bytes] = {
        'README.txt': readme(doc, folders).encode('utf-8'),
        'tournaments.json': json_bytes,
    }
    for section in doc['tournaments']:
        folder = folders[section['id']]
        files[f'{folder}/participants.csv'] = participants_csv(section)
        files[f'{folder}/matches.csv'] = matches_csv(section)
    return files_to_zip_bytes(files), json_bytes
