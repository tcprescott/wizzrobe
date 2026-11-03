"""Tests for the SpeedGaming transport client's handling of the live API's quirks.

SG publishes no schema. These pin the behaviours captured from the live feed —
a JSON error object served with a 200, and canned fixtures whose shape matches
the ``SG_*_KEYS`` manifests exactly, so the mock cannot drift into a shape the
real API never serves.
"""

import pytest

from application.utils.clients.speedgaming_client import (
    _MOCK_EPISODES,
    SG_CHANNEL_KEYS,
    SG_CREW_KEYS,
    SG_CREW_ROLES,
    SG_EPISODE_KEYS,
    SG_EVENT_KEYS,
    SG_MATCH_KEYS,
    SG_PLAYER_KEYS,
    MockSpeedGamingClient,
    SpeedGamingAPIError,
    _validate_schedule_payload,
)


def _sides(episode):
    return [episode[k] for k in ('match1', 'match2') if episode[k]]


def _players(episode):
    return [p for side in _sides(episode) for p in side['players']]


def test_error_object_raises_with_sg_message():
    with pytest.raises(SpeedGamingAPIError) as exc:
        _validate_schedule_payload({'error': 'Failed to find event.'}, 'nosuchevent')
    assert 'Failed to find event.' in str(exc.value)
    assert 'nosuchevent' in str(exc.value)


def test_empty_schedule_is_not_an_error():
    assert _validate_schedule_payload([], 'ev') == []


def test_non_list_payload_raises():
    with pytest.raises(SpeedGamingAPIError):
        _validate_schedule_payload({'unexpected': True}, 'ev')


@pytest.mark.parametrize('episode', _MOCK_EPISODES, ids=lambda e: e['id'])
def test_mock_fixtures_carry_exactly_the_live_key_set(episode):
    """The fixtures serve every key the real feed does, and none it does not.

    Exact sets, not minimums: an invented key is one a consumer could come to
    rely on and then find missing in production.
    """
    assert set(episode) == SG_EPISODE_KEYS
    assert set(episode['event']) == SG_EVENT_KEYS
    for side in _sides(episode):
        assert set(side) == SG_MATCH_KEYS
    for player in _players(episode):
        assert set(player) == SG_PLAYER_KEYS
    for role in SG_CREW_ROLES:
        for member in episode[role]:
            assert set(member) == SG_CREW_KEYS
    for channel in episode['channels']:
        assert set(channel) == SG_CHANNEL_KEYS


@pytest.mark.parametrize('episode', _MOCK_EPISODES, ids=lambda e: e['id'])
def test_mock_fixtures_use_the_live_value_conventions(episode):
    """Absent means ``''``; ``match2`` is the only genuinely nullable field.

    A fixture that used ``None`` for an unset player field would let a consumer
    pass an ``is None`` check that the real feed never satisfies.
    """
    scalars = [
        v
        for obj in [episode, episode['event'], *_sides(episode), *_players(episode),
                    *(m for r in SG_CREW_ROLES for m in episode[r]),
                    *episode['channels']]
        for k, v in obj.items()
        if k not in ('match1', 'match2', 'event', 'channels', *SG_CREW_ROLES, 'players')
    ]
    assert not any(v is None for v in scalars)
    assert episode['when'].endswith('+00:00')
    assert episode['whenCountdown'] == episode['when']
    assert episode['timezone'] == ''


def test_mock_fixtures_cover_the_states_the_etl_branches_on():
    assert any(e['approved'] is False for e in _MOCK_EPISODES), 'need an unapproved episode'
    assert any(e['match2'] for e in _MOCK_EPISODES), 'need a doubleheader (match2 populated)'
    assert any(not _players(e) for e in _MOCK_EPISODES), 'need an episode with no players yet'
    assert any(len(e['match1']['players']) > 2 for e in _MOCK_EPISODES), 'need a >2-player match'

    titles = [(e['title'], e['match1']['title'], e['match1']['note']) for e in _MOCK_EPISODES]
    assert all(ep_title == '' for ep_title, _, _ in titles), 'SG always serves an empty episode title'
    assert any(not t and note for _, t, note in titles), 'need a TBD matchup labelled only by its note'

    players = _players_across_fixtures()
    assert any(p['discordId'] for p in players), 'need a discord-resolvable player'
    assert any(not p['discordId'] and p['discordTag'] for p in players), 'need a tag-only player'
    assert any(not p['discordId'] and not p['discordTag'] for p in players), 'need a placeholder'

    crew = [m for e in _MOCK_EPISODES for r in SG_CREW_ROLES for m in e[r]]
    assert {m['approved'] for m in crew} == {True, False}, 'need approved and pending crew'
    assert {m['ready'] for m in crew} == {True, False}, 'need ready and un-ready crew'
    for role in SG_CREW_ROLES:
        assert any(e[role] for e in _MOCK_EPISODES), f'no fixture exercises {role}'


def _players_across_fixtures():
    return [p for e in _MOCK_EPISODES for p in _players(e)]


async def test_mock_client_returns_deep_copies():
    client = MockSpeedGamingClient()
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    first = await client.fetch_schedule('mockevent', now, now)
    first[0]['match1']['players'][0]['displayName'] = 'mutated'
    second = await client.fetch_schedule('mockevent', now, now)
    assert second[0]['match1']['players'][0]['displayName'] != 'mutated'
