"""Tests for the SpeedGaming ETL (PR 7).

Exercises the transform (placeholder resolution + upgrade-in-place), the load
(materialize/refresh Match + players), the lifecycle guards (skip finished /
manual / room-linked; auto-finish >4h past), and the soft-detach of an upstream
cancellation — all against the in-memory schema with the mock SG client.
"""

from datetime import datetime, timedelta, timezone

import pytest

from application.repositories import MatchRepository, UserRepository
from application.services.speedgaming_etl_service import SpeedGamingETLService
from application.utils.clients.speedgaming_client import MockSpeedGamingClient
from models import (
    Match,
    MatchAcknowledgment,
    MatchPlayers,
    RacetimeRoom,
    SpeedGamingEpisode,
    SpeedGamingEventLink,
    SyncStatus,
    Tournament,
    User,
)


def _episode(sg_id, when, players, title='R1', note='', match2=None, **extra):
    """A live-shaped episode.

    SG serves an episode-level ``title`` but leaves it empty on every episode —
    the matchup name lives on ``match1.title``, with ``note`` as the side's
    free-text slot.
    """
    return {
        'id': sg_id,
        'when': when,
        'title': '',
        'match1': {'id': sg_id * 10, 'title': title, 'note': note, 'players': players},
        'match2': match2,
        **extra,
    }


async def _setup(db):
    system = await UserRepository.get_or_create_system_user()
    tourn = await Tournament.create(name='T')
    link = await SpeedGamingEventLink.create(tournament=tourn, event_slug='ev')
    etl = SpeedGamingETLService(client=MockSpeedGamingClient([]))
    return system, tourn, link, etl


async def test_import_creates_match_and_resolves_players(db):
    system, tourn, link, etl = await _setup(db)
    real = await User.create(discord_id=111, username='playerone')
    raw = _episode(42, '2026-07-20T18:00:00+00:00', [
        {'id': 1, 'displayName': 'PlayerOne', 'discordId': '111', 'discordTag': 'playerone'},
        {'id': 2, 'displayName': 'SG Only', 'discordId': None, 'discordTag': 'sgonly'},
    ])

    outcome = await etl.import_episode(link, raw, actor=system)
    assert outcome == 'imported'

    match = await Match.filter(speedgaming_episode__sg_episode_id='42').first()
    assert match is not None
    assert match.tournament_id == tourn.id
    assert match.scheduled_at == datetime(2026, 7, 20, 18, 0, tzinfo=timezone.utc)

    players = await MatchPlayers.filter(match=match).prefetch_related('user')
    assert len(players) == 2
    by_ph = {p.user.is_placeholder: p.user for p in players}
    assert by_ph[False].id == real.id
    placeholder = by_ph[True]
    assert placeholder.discord_id is None
    assert placeholder.speedgaming_id == '2'
    assert placeholder.username == 'sg_2'


async def test_import_is_idempotent_unchanged(db):
    system, tourn, link, etl = await _setup(db)
    raw = _episode(50, '2026-07-20T18:00:00+00:00', [
        {'id': 9, 'displayName': 'A', 'discordId': None, 'discordTag': 'a'},
    ])
    assert await etl.import_episode(link, raw, actor=system) == 'imported'
    assert await etl.import_episode(link, raw, actor=system) == 'unchanged'
    assert await Match.filter(speedgaming_episode__sg_episode_id='50').count() == 1


async def test_placeholder_upgraded_in_place_when_discord_id_appears(db):
    system, tourn, link, etl = await _setup(db)
    # First sync: no discord id → placeholder created keyed on sg id 7.
    raw1 = _episode(60, '2026-07-20T18:00:00+00:00', [
        {'id': 7, 'displayName': 'Later', 'discordId': None, 'discordTag': 'later'},
    ])
    await etl.import_episode(link, raw1, actor=system)
    placeholder = await UserRepository.get_placeholder_by_speedgaming_id('7')
    assert placeholder is not None and placeholder.is_placeholder

    # Second sync: same sg id now carries a discord id → upgraded in place.
    raw2 = _episode(60, '2026-07-20T19:00:00+00:00', [
        {'id': 7, 'displayName': 'Later', 'discordId': '777', 'discordTag': 'later'},
    ])
    await etl.import_episode(link, raw2, actor=system)
    upgraded = await User.get(id=placeholder.id)
    assert upgraded.is_placeholder is False
    assert upgraded.discord_id == 777
    # No second user row was forked for the same person.
    assert await User.filter(speedgaming_id='7').count() == 1


async def test_resolves_by_discord_username(db):
    system, tourn, link, etl = await _setup(db)
    existing = await User.create(discord_id=222, username='byname')
    raw = _episode(70, '2026-07-20T18:00:00+00:00', [
        {'id': 8, 'displayName': 'By Name', 'discordId': None, 'discordTag': 'byname'},
    ])
    await etl.import_episode(link, raw, actor=system)
    match = await Match.filter(speedgaming_episode__sg_episode_id='70').first()
    players = await MatchPlayers.filter(match=match).prefetch_related('user')
    assert [p.user_id for p in players] == [existing.id]


@pytest.mark.parametrize('field', ['finished_at', 'seated_at', 'started_at', 'confirmed_at'])
async def test_resync_skips_manually_progressed_match(db, field):
    system, tourn, link, etl = await _setup(db)
    raw = _episode(80, '2026-07-20T18:00:00+00:00', [
        {'id': 3, 'discordId': None, 'discordTag': 'p'},
    ])
    await etl.import_episode(link, raw, actor=system)
    match = await Match.filter(speedgaming_episode__sg_episode_id='80').first()
    await MatchRepository.update(match, **{field: datetime.now(timezone.utc)})

    changed = _episode(80, '2026-07-22T18:00:00+00:00', [
        {'id': 3, 'discordId': None, 'discordTag': 'p'},
    ])
    assert await etl.import_episode(link, changed, actor=system) == 'skipped'

    refreshed = await Match.get(id=match.id)
    assert refreshed.scheduled_at == datetime(2026, 7, 20, 18, 0, tzinfo=timezone.utc)
    episode = await SpeedGamingEpisode.get(sg_episode_id='80')
    assert episode.sync_status == SyncStatus.SKIPPED


async def test_resync_skips_room_linked_match(db):
    system, tourn, link, etl = await _setup(db)
    raw = _episode(90, '2026-07-20T18:00:00+00:00', [
        {'id': 4, 'discordId': None, 'discordTag': 'q'},
    ])
    await etl.import_episode(link, raw, actor=system)
    match = await Match.filter(speedgaming_episode__sg_episode_id='90').first()
    await RacetimeRoom.create(slug='alttpr/room-x', category='alttpr', match=match)

    changed = _episode(90, '2026-07-25T18:00:00+00:00', [
        {'id': 4, 'discordId': None, 'discordTag': 'q'},
    ])
    assert await etl.import_episode(link, changed, actor=system) == 'skipped'


async def test_sync_event_link_cancels_vanished_episode(db):
    system, tourn, link, etl = await _setup(db)
    now = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)
    two = [
        _episode(101, '2026-07-20T18:00:00+00:00', [{'id': 11, 'discordTag': 'a'}]),
        _episode(102, '2026-07-21T18:00:00+00:00', [{'id': 12, 'discordTag': 'b'}]),
    ]
    etl.client = MockSpeedGamingClient(two)
    result = await etl.sync_event_link(link, actor=system, now=now)
    assert result.imported == 2

    # Next poll: 102 vanished upstream → soft-detached (Match survives).
    etl.client = MockSpeedGamingClient([two[0]])
    result2 = await etl.sync_event_link(link, actor=system, now=now + timedelta(minutes=30))
    assert result2.cancelled == 1
    ep = await SpeedGamingEpisode.get(sg_episode_id='102')
    assert ep.sync_status == SyncStatus.CANCELLED
    assert await Match.filter(speedgaming_episode__sg_episode_id='102').count() == 1


async def test_auto_finishes_stale_match(db):
    system, tourn, link, etl = await _setup(db)
    now = datetime(2026, 7, 20, 23, 0, tzinfo=timezone.utc)
    # Episode scheduled >4h before ``now`` and never progressed.
    stale = [_episode(110, '2026-07-20T18:00:00+00:00', [{'id': 13, 'discordTag': 'c'}])]
    etl.client = MockSpeedGamingClient(stale)
    result = await etl.sync_event_link(link, actor=system, now=now)
    assert result.auto_finished == 1
    match = await Match.filter(speedgaming_episode__sg_episode_id='110').first()
    assert match.finished_at is not None


async def test_import_gives_every_synced_player_an_acknowledgment_row(db):
    system, _tourn, link, etl = await _setup(db)
    await User.create(discord_id=111, username='playerone')
    raw = _episode(60, '2026-07-20T18:00:00+00:00', [
        {'id': 1, 'displayName': 'PlayerOne', 'discordId': '111', 'discordTag': 'playerone'},
        {'id': 2, 'displayName': 'SG Only', 'discordId': None, 'discordTag': 'sgonly'},
    ])

    await etl.import_episode(link, raw, actor=system)

    match = await Match.filter(speedgaming_episode__sg_episode_id='60').first()
    acks = await MatchAcknowledgment.filter(match=match)
    assert len(acks) == 2
    # Created un-answered: the sync assigns people, it does not speak for them.
    assert all(a.acknowledged_at is None for a in acks)
    assert all(a.auto_acknowledged is False for a in acks)


async def test_a_resync_does_not_discard_an_answer_a_player_already_gave(db):
    """The reason this reconciles rather than re-seeds.

    ``seed_acknowledgments`` deletes every row first, so calling it from a poll
    that runs every few minutes would erase each confirmation moments after it
    arrived.
    """
    system, _tourn, link, etl = await _setup(db)
    await User.create(discord_id=111, username='playerone')
    raw = _episode(61, '2026-07-20T18:00:00+00:00', [
        {'id': 1, 'displayName': 'PlayerOne', 'discordId': '111', 'discordTag': 'playerone'},
    ])
    await etl.import_episode(link, raw, actor=system)
    match = await Match.filter(speedgaming_episode__sg_episode_id='61').first()

    ack = await MatchAcknowledgment.filter(match=match).first()
    answered = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)
    ack.acknowledged_at = answered
    await ack.save()

    await etl.import_episode(link, raw, actor=system)

    rows = await MatchAcknowledgment.filter(match=match)
    assert len(rows) == 1
    assert rows[0].acknowledged_at == answered


async def test_a_player_dropped_upstream_loses_their_acknowledgment_row(db):
    """The roster is a full replace, so a stale row would outlive its player."""
    system, _tourn, link, etl = await _setup(db)
    await User.create(discord_id=111, username='playerone')
    two = _episode(62, '2026-07-20T18:00:00+00:00', [
        {'id': 1, 'displayName': 'PlayerOne', 'discordId': '111', 'discordTag': 'playerone'},
        {'id': 2, 'displayName': 'SG Only', 'discordId': None, 'discordTag': 'sgonly'},
    ])
    await etl.import_episode(link, two, actor=system)
    match = await Match.filter(speedgaming_episode__sg_episode_id='62').first()
    assert await MatchAcknowledgment.filter(match=match).count() == 2

    one = _episode(62, '2026-07-20T18:00:00+00:00', [
        {'id': 1, 'displayName': 'PlayerOne', 'discordId': '111', 'discordTag': 'playerone'},
    ])
    await etl.import_episode(link, one, actor=system)

    assert await MatchPlayers.filter(match=match).count() == 1
    assert await MatchAcknowledgment.filter(match=match).count() == 1


async def test_a_player_dropped_upstream_loses_their_hard_preset_opt_in(db):
    """These are the rosters that change with nobody in the app touching them.

    A row left behind would keep counting toward the harder-preset agreement
    for a player who is no longer in the match, and would come back already
    opted in if SG re-added them to a roster they never saw.
    """
    from models import MatchHardPresetOptIn, Preset

    system, tourn, link, etl = await _setup(db)
    standard = await Preset.create(name='Std', randomizer='alttpr', settings={})
    hard = await Preset.create(name='Hard', randomizer='alttpr', settings={})
    tourn.preset = standard
    tourn.hard_preset = hard
    await tourn.save()

    await User.create(discord_id=111, username='playerone')
    two = _episode(63, '2026-07-20T18:00:00+00:00', [
        {'id': 1, 'displayName': 'PlayerOne', 'discordId': '111', 'discordTag': 'playerone'},
        {'id': 2, 'displayName': 'SG Only', 'discordId': None, 'discordTag': 'sgonly'},
    ])
    await etl.import_episode(link, two, actor=system)
    match = await Match.filter(speedgaming_episode__sg_episode_id='63').first()
    await match.fetch_related('players')
    for player in match.players:
        await MatchHardPresetOptIn.create(match=match, user_id=player.user_id)
    assert await MatchHardPresetOptIn.filter(match=match).count() == 2

    one = _episode(63, '2026-07-20T18:00:00+00:00', [
        {'id': 1, 'displayName': 'PlayerOne', 'discordId': '111', 'discordTag': 'playerone'},
    ])
    await etl.import_episode(link, one, actor=system)

    remaining = await MatchHardPresetOptIn.filter(match=match).values_list(
        'user_id', flat=True,
    )
    survivors = await MatchPlayers.filter(match=match).values_list('user_id', flat=True)
    assert set(remaining) == set(survivors)
    assert len(survivors) == 1


# Captured from GET https://speedgaming.org/api/schedule/ — the conventions
# below are what the real feed does, not what a simplified reading assumes.

async def test_a_tbd_matchup_is_labelled_by_its_note(db):
    """The episode and matchup titles are empty far more often than not.

    SG leaves the episode title empty on every episode, and a matchup that has
    not been drawn yet has an empty ``match1.title`` too. The side's ``note`` —
    "Swiss Round 5", "Game 2" — is then the only descriptor, and without it the
    match reaches the board as an untitled row.
    """
    system, _tourn, link, etl = await _setup(db)
    raw = _episode(200, '2026-07-20T18:00:00+00:00',
                   [{'id': 20, 'discordTag': 'p'}], title='', note='Swiss Round 5')

    await etl.import_episode(link, raw, actor=system)

    match = await Match.filter(speedgaming_episode__sg_episode_id='200').first()
    assert match.title == 'Swiss Round 5'
    episode = await SpeedGamingEpisode.get(sg_episode_id='200')
    assert episode.title == 'Swiss Round 5'


async def test_a_real_title_wins_over_a_note(db):
    system, _tourn, link, etl = await _setup(db)
    raw = _episode(201, '2026-07-20T18:00:00+00:00', [{'id': 21, 'discordTag': 'p'}],
                   title='Lower Quarterfinals', note='inertia:f7kyc6i6hth7')

    await etl.import_episode(link, raw, actor=system)

    match = await Match.filter(speedgaming_episode__sg_episode_id='201').first()
    assert match.title == 'Lower Quarterfinals'


async def test_an_over_long_title_is_truncated_rather_than_failing_the_episode(db):
    """SG's titles are unbounded free text; ``Match.title`` is a CharField(255)."""
    system, _tourn, link, etl = await _setup(db)
    raw = _episode(202, '2026-07-20T18:00:00+00:00', [{'id': 22, 'discordTag': 'p'}],
                   title='x' * 400)

    await etl.import_episode(link, raw, actor=system)

    match = await Match.filter(speedgaming_episode__sg_episode_id='202').first()
    assert len(match.title) == 255


async def test_crew_churn_does_not_force_a_re_import(db):
    """Crew ``ready``/``approved`` flags flip constantly and the ETL reads neither.

    Hashing the whole payload made ``unchanged`` almost unreachable: every crew
    signup or approval upstream re-materialized the match, writing an audit row
    and publishing an event for a change nothing here consumes.
    """
    system, _tourn, link, etl = await _setup(db)
    crew = [{'id': 30, 'displayName': 'CasterOne', 'discordId': '', 'discordTag': 'casterone',
             'publicStream': 'casterone', 'approved': False, 'ready': False,
             'partner': '', 'language': 'en'}]
    raw = _episode(210, '2026-07-20T18:00:00+00:00', [{'id': 23, 'discordTag': 'p'}],
                   commentators=crew, trackers=[], broadcasters=[], helpers=[])
    assert await etl.import_episode(link, raw, actor=system) == 'imported'

    churned = _episode(210, '2026-07-20T18:00:00+00:00', [{'id': 23, 'discordTag': 'p'}],
                       commentators=[{**crew[0], 'approved': True, 'ready': True}],
                       trackers=[], broadcasters=[], helpers=[])

    assert await etl.import_episode(link, churned, actor=system) == 'unchanged'
    # The raw snapshot still tracks upstream — only re-materialization is skipped.
    episode = await SpeedGamingEpisode.get(sg_episode_id='210')
    assert episode.payload['commentators'][0]['ready'] is True


async def test_a_change_the_etl_reads_still_re_imports(db):
    """The narrowed fingerprint must not swallow a real schedule change."""
    system, _tourn, link, etl = await _setup(db)
    raw = _episode(211, '2026-07-20T18:00:00+00:00', [{'id': 24, 'discordTag': 'p'}])
    await etl.import_episode(link, raw, actor=system)

    moved = _episode(211, '2026-07-20T21:00:00+00:00', [{'id': 24, 'discordTag': 'p'}])
    assert await etl.import_episode(link, moved, actor=system) == 'imported'

    match = await Match.filter(speedgaming_episode__sg_episode_id='211').first()
    assert match.scheduled_at == datetime(2026, 7, 20, 21, 0, tzinfo=timezone.utc)


async def test_a_doubleheader_unions_both_sides_players(db):
    """``match2`` is the one genuinely nullable field, and it is sometimes set."""
    system, _tourn, link, etl = await _setup(db)
    raw = _episode(
        220, '2026-07-20T18:00:00+00:00',
        [{'id': 25, 'displayName': 'Player 1', 'discordId': '', 'discordTag': 'p1'}],
        title='', note='Showcase Race 5 & 6',
        match2={'id': 2201, 'title': '', 'note': 'Showcase Race 5 & 6', 'players': [
            {'id': 26, 'displayName': 'Player 2', 'discordId': '', 'discordTag': 'p2'},
        ]},
    )

    await etl.import_episode(link, raw, actor=system)

    match = await Match.filter(speedgaming_episode__sg_episode_id='220').first()
    assert await MatchPlayers.filter(match=match).count() == 2
    assert match.title == 'Showcase Race 5 & 6'


async def test_an_episode_with_no_players_yet_still_materializes(db):
    """SG publishes the airtime before the matchup is drawn."""
    system, _tourn, link, etl = await _setup(db)
    raw = _episode(230, '2026-07-20T18:00:00+00:00', [], title='Blitz')

    assert await etl.import_episode(link, raw, actor=system) == 'imported'

    match = await Match.filter(speedgaming_episode__sg_episode_id='230').first()
    assert match is not None and match.title == 'Blitz'
    assert await MatchPlayers.filter(match=match).count() == 0


async def test_absent_identity_is_an_empty_string_not_null(db):
    """Every SG scalar is a string that is empty when unset, ``null`` never."""
    system, _tourn, link, etl = await _setup(db)
    existing = await User.create(discord_id=333, username='bytag')
    raw = _episode(240, '2026-07-20T18:00:00+00:00', [
        {'id': 27, 'displayName': 'By Tag', 'discordId': '', 'discordTag': 'bytag',
         'publicStream': '', 'streamingFrom': ''},
        {'id': 28, 'displayName': 'Nobody', 'discordId': '', 'discordTag': '',
         'publicStream': '', 'streamingFrom': ''},
    ])

    await etl.import_episode(link, raw, actor=system)

    match = await Match.filter(speedgaming_episode__sg_episode_id='240').first()
    players = await MatchPlayers.filter(match=match).prefetch_related('user')
    by_id = {p.user.id: p.user for p in players}
    assert existing.id in by_id
    placeholder = await UserRepository.get_placeholder_by_speedgaming_id('28')
    assert placeholder is not None and placeholder.id in by_id


async def test_a_malformed_discord_id_degrades_instead_of_failing_the_episode(db):
    """``discordId`` is a string field upstream, so it can hold a non-snowflake."""
    system, _tourn, link, etl = await _setup(db)
    existing = await User.create(discord_id=444, username='fallback')
    raw = _episode(250, '2026-07-20T18:00:00+00:00', [
        {'id': 29, 'displayName': 'Odd', 'discordId': 'not-a-snowflake',
         'discordTag': 'fallback'},
    ])

    assert await etl.import_episode(link, raw, actor=system) == 'imported'

    match = await Match.filter(speedgaming_episode__sg_episode_id='250').first()
    players = await MatchPlayers.filter(match=match)
    assert [p.user_id for p in players] == [existing.id]
