"""DataExportService: who may export what, the limits, the build, and the worker."""

import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from application.services import DataExportService
from application.services.exports import data_export_service as export_module
from application.services.exports import data_export_worker
from models import (
    AuditLog,
    Bracket,
    BracketEntrant,
    BracketEntrantStatus,
    BracketEntry,
    BracketEntryStatus,
    BracketFormat,
    BracketMatch,
    BracketMatchState,
    BracketState,
    DataExport,
    DataExportKind,
    DataExportStatus,
    Match,
    MatchPlayers,
    Role,
    Tournament,
    TournamentPlayers,
    UserRole,
)
from tests.factories import make_user

_SCHEMA_PATH = Path(__file__).resolve().parents[2] / 'docs' / 'schemas' / 'tournament-export-v1.schema.json'


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    monkeypatch.setattr(export_module, 'PAUSE_BETWEEN_TOURNAMENTS', 0)


async def _staff(discord_id=9100, username='boss'):
    user = await make_user(discord_id, username=username)
    await UserRole.create(user=user, role=Role.STAFF)
    return user


async def _built(export: DataExport) -> DataExport:
    await data_export_worker._tick()
    return await DataExport.get(id=export.id)


def _zip(export: DataExport) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(bytes(export.content)))


async def _single_elim(tournament: Tournament, players):
    """A finished 2-player final plus an entrant who dropped before it."""
    bracket = await Bracket.create(
        tournament=tournament, name='Main', format=BracketFormat.SINGLE_ELIM, state=BracketState.COMPLETE,
    )
    entries = []
    for seed, user in enumerate(players, start=1):
        entrant = await BracketEntrant.create(tournament=tournament, display_name=user.username, user=user)
        entries.append(await BracketEntry.create(
            bracket=bracket, entrant=entrant, seed=seed, final_rank=seed,
            status=BracketEntryStatus.ACTIVE if seed == 1 else BracketEntryStatus.ELIMINATED,
        ))
    dropped = await BracketEntrant.create(
        tournament=tournament, display_name='Gone Player', status=BracketEntrantStatus.DROPPED,
    )
    await BracketMatch.create(
        bracket=bracket, round=1, position=0, entry1=entries[0], entry2=entries[1],
        winner=entries[0], entry1_score=2, entry2_score=1, state=BracketMatchState.COMPLETE,
    )
    return dropped


class TestWhoMayExport:
    async def test_staff_export_every_tournament(self, db):
        staff = await _staff()
        await Tournament.create(name='A')
        await Tournament.create(name='B')
        export = await DataExportService().request_tournaments(staff)
        assert export.title == 'All 2 tournaments'
        assert export.status == DataExportStatus.QUEUED

    async def test_a_tournament_admin_exports_only_their_own(self, db):
        ta = await make_user(9101, username='ta')
        mine = await Tournament.create(name='Mine')
        other = await Tournament.create(name='Other')
        await mine.admins.add(ta)
        export = await DataExportService().request_tournaments(ta)
        assert export.params['tournament_ids'] == [mine.id]
        with pytest.raises(PermissionError):
            await DataExportService().request_tournaments(ta, [other.id])

    async def test_a_plain_member_is_refused(self, db):
        player = await make_user(9102, username='player')
        await Tournament.create(name='A')
        with pytest.raises(PermissionError):
            await DataExportService().request_tournaments(player)

    async def test_someone_elses_export_reads_as_missing(self, db):
        staff = await _staff()
        other = await _staff(9103, 'other')
        await Tournament.create(name='A')
        export = await DataExportService().request_tournaments(staff)
        with pytest.raises(ValueError):
            await DataExportService().get_mine(other, export.id)
        assert await DataExportService().list_mine(other) == []


class TestLimits:
    async def test_one_export_building_per_person(self, db):
        staff = await _staff()
        await Tournament.create(name='A')
        await DataExportService().request_tournaments(staff)
        with pytest.raises(ValueError, match='already have an export'):
            await DataExportService().request_tournaments(staff)

    async def test_hourly_cap(self, db, monkeypatch):
        monkeypatch.setattr(export_module, 'MAX_PENDING_PER_PERSON', 100)
        monkeypatch.setattr(export_module, 'MAX_PENDING_PER_COMMUNITY', 100)
        monkeypatch.setattr(export_module, 'MAX_PER_HOUR', 2)
        staff = await _staff()
        await Tournament.create(name='A')
        await DataExportService().request_tournaments(staff)
        await DataExportService().request_tournaments(staff)
        with pytest.raises(ValueError, match='an hour'):
            await DataExportService().request_tournaments(staff)

    async def test_community_cap(self, db, monkeypatch):
        monkeypatch.setattr(export_module, 'MAX_PENDING_PER_COMMUNITY', 1)
        await Tournament.create(name='A')
        await DataExportService().request_tournaments(await _staff())
        with pytest.raises(ValueError, match='Several exports'):
            await DataExportService().request_tournaments(await _staff(9104, 'second'))

    async def test_logged_table_csvs_do_not_count(self, db, monkeypatch):
        monkeypatch.setattr(export_module, 'MAX_PER_HOUR', 1)
        staff = await _staff()
        await Tournament.create(name='A')
        await DataExportService().record_table_csv(staff, 'users.csv', 'users.csv', b'a,b\n')
        export = await DataExportService().request_tournaments(staff)
        assert export.kind == DataExportKind.TOURNAMENTS


class TestBuild:
    async def test_participants_and_matches_from_a_native_bracket(self, db):
        staff = await _staff()
        tournament = await Tournament.create(name='Zelda Cup')
        one = await make_user(9110, username='one')
        two = await make_user(9111, username='two')
        await TournamentPlayers.create(tournament=tournament, user=one)
        await _single_elim(tournament, [one, two])
        match = await Match.create(
            tournament=tournament, scheduled_at=datetime(2026, 10, 23, 18, tzinfo=timezone.utc),
            finished_at=datetime(2026, 10, 23, 19, tzinfo=timezone.utc),
        )
        await MatchPlayers.create(match=match, user=one, finish_rank=1, finish_time=3600)
        await MatchPlayers.create(match=match, user=two, finish_rank=2)

        export = await _built(await DataExportService().request_tournaments(staff))

        assert export.status == DataExportStatus.READY, export.error
        assert export.filename.endswith('.zip')
        archive = _zip(export)
        folder = f'zelda-cup-{tournament.id}'
        assert archive.namelist() == [
            'README.txt', 'tournaments.json', f'{folder}/participants.csv', f'{folder}/matches.csv',
        ]
        doc = json.loads(archive.read('tournaments.json'))
        assert doc['format'] == 'wizzrobe-tournament-export'
        assert doc['schema_version'] == 1
        section = doc['tournaments'][0]
        assert section['bracket']['site'] == 'wizzrobe'
        by_name = {p['name']: p for p in section['participants']}
        assert by_name['one'] == {
            'player_id': one.id, 'name': 'one', 'discord_username': 'one', 'discord_id': '9110',
            'bracket_name': 'one', 'enrolled': True, 'status': 'finished', 'seed': 1,
            'placement': 1, 'wins': 1, 'losses': 0,
        }
        assert by_name['two']['status'] == 'eliminated'
        assert by_name['two']['enrolled'] is False
        assert by_name['Gone Player']['status'] == 'dropped'
        assert by_name['Gone Player']['player_id'] is None

        played = next(m for m in section['matches'] if m['match_id'] == match.id)
        assert played['state'] == 'finished'
        assert played['scheduled_at'] == '2026-10-23T18:00:00Z'
        assert played['winner_name'] == 'one'
        assert played['players'][0]['finish_time_seconds'] == 3600
        reported = next(m for m in section['matches'] if m['match_id'] is None)
        assert reported['state'] == 'reported'
        assert [p['score'] for p in reported['players']] == [2, 1]

        participants = archive.read(f'{folder}/participants.csv').decode('utf-8-sig')
        assert participants.splitlines()[0].startswith('Player ID,Name,Discord username,Discord ID')
        assert json.loads(bytes(export.json_content)) == doc

        audit = await AuditLog.filter(action='tournament.data_exported').first()
        assert audit is not None

    async def test_a_tournament_without_a_bracket_counts_finishes(self, db):
        staff = await _staff()
        tournament = await Tournament.create(name='Races')
        one = await make_user(9120, username='one')
        two = await make_user(9121, username='two')
        for u in (one, two):
            await TournamentPlayers.create(tournament=tournament, user=u)
        match = await Match.create(tournament=tournament, finished_at=datetime.now(timezone.utc))
        await MatchPlayers.create(match=match, user=one, finish_rank=2)
        await MatchPlayers.create(match=match, user=two, finish_rank=1)

        export = await _built(await DataExportService().request_tournaments(staff))
        section = json.loads(bytes(export.json_content))['tournaments'][0]
        by_name = {p['name']: p for p in section['participants']}
        assert section['bracket']['site'] is None
        assert (by_name['two']['wins'], by_name['one']['losses']) == (1, 1)
        assert by_name['one']['status'] is None

    async def test_download_waits_for_ready_and_returns_json(self, db):
        staff = await _staff()
        await Tournament.create(name='A')
        export = await DataExportService().request_tournaments(staff)
        with pytest.raises(ValueError, match='not ready'):
            await DataExportService().download(staff, export.id)
        await _built(export)
        f = await DataExportService().download(staff, export.id, 'json')
        assert f.media_type == 'application/json'
        assert f.filename.endswith('.json')

    async def test_a_revoked_admin_gets_a_failed_export(self, db):
        ta = await make_user(9130, username='ta')
        tournament = await Tournament.create(name='Mine')
        await tournament.admins.add(ta)
        export = await DataExportService().request_tournaments(ta)
        await tournament.admins.remove(ta)
        export = await _built(export)
        assert export.status == DataExportStatus.FAILED
        assert 'no longer export' in export.error


class TestWorker:
    async def test_one_export_per_tick_oldest_first(self, db, monkeypatch):
        monkeypatch.setattr(export_module, 'MAX_PENDING_PER_COMMUNITY', 10)
        await Tournament.create(name='A')
        first = await DataExportService().request_tournaments(await _staff())
        second = await DataExportService().request_tournaments(await _staff(9140, 'second'))
        await data_export_worker._tick()
        assert (await DataExport.get(id=first.id)).status == DataExportStatus.READY
        assert (await DataExport.get(id=second.id)).status == DataExportStatus.QUEUED

    async def test_interrupted_builds_fail_and_old_exports_are_deleted(self, db, monkeypatch):
        staff = await _staff()
        await Tournament.create(name='A')
        stuck = await DataExportService().request_tournaments(staff)
        await DataExport.filter(id=stuck.id).update(
            status=DataExportStatus.RUNNING,
            started_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        old = await DataExportService().record_table_csv(staff, 'x.csv', 'x.csv', b'x')
        await DataExport.filter(id=old.id).update(created_at=datetime.now(timezone.utc) - timedelta(days=4))
        monkeypatch.setattr(data_export_worker, '_last_prune', 0.0)

        await data_export_worker._tick()

        stuck = await DataExport.get(id=stuck.id)
        assert stuck.status == DataExportStatus.FAILED
        assert 'restarted' in stuck.error
        assert not await DataExport.filter(id=old.id).exists()


class TestSchema:
    async def test_the_json_matches_the_published_schema(self, db):
        jsonschema = pytest.importorskip('jsonschema')
        schema = json.loads(_SCHEMA_PATH.read_text())
        staff = await _staff()
        tournament = await Tournament.create(name='Zelda Cup')
        one = await make_user(9150, username='one')
        two = await make_user(9151, username='two')
        await TournamentPlayers.create(tournament=tournament, user=one)
        await _single_elim(tournament, [one, two])
        await Tournament.create(name='Races')
        match = await Match.create(tournament=tournament, finished_at=datetime.now(timezone.utc))
        await MatchPlayers.create(match=match, user=one, finish_rank=1, finish_time=3600)

        export = await _built(await DataExportService().request_tournaments(staff))

        jsonschema.validate(json.loads(bytes(export.json_content)), schema)
