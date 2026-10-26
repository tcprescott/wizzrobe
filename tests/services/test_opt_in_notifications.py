"""Opt-in DMs: off for everyone until a person turns them on, then sent only to them."""

import pytest

from application.services import opt_in_notifications
from application.services.match import MatchService
from application.services.notification_catalog import (
    ADMINS,
    BRACKET_STARTED,
    EVENT_LISTED,
    FOLLOWERS,
    MATCH_RESULT,
    MATCH_SCHEDULED,
    MEMBERS,
    OPPONENT_ACKNOWLEDGED,
    PLAYER_WITHDREW,
    PLAYERS,
    QUALIFIER_OPENED,
    REMOVED_FROM_TOURNAMENT,
    is_opt_in,
    targets,
)
from application.services.notification_preference_service import NotificationPreferenceService
from models import (
    AsyncQualifier,
    CommunityEvent,
    CommunityEventStatus,
    Match,
    MatchNotificationLevel,
    MatchPlayers,
    NotificationMode,
    NotificationPreset,
    Tournament,
    TournamentNotificationPreference,
)
from tests.factories import make_member, utc

OFF, PING = NotificationMode.OFF, NotificationMode.PING
pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
def sent(monkeypatch):
    calls = []

    class Recorder:
        async def send_dm(self, user_id, message, *, embed=None, link=None, notice=None, **_):
            calls.append((int(user_id), notice))
            return True, None

    monkeypatch.setattr(opt_in_notifications, 'DiscordService', Recorder)
    return calls


async def _match(*players):
    tournament = await Tournament.create(name='Cup')
    match = await Match.create(tournament=tournament, scheduled_at=utc(2026, 10, 23, 18))
    for player in players:
        await MatchPlayers.create(match=match, user=player)
    return tournament, match


async def test_opt_in_is_off_in_every_preset_until_chosen():
    player = await make_member(discord_id=9101, username='oi1')
    target = OPPONENT_ACKNOWLEDGED.to(PLAYERS)
    assert is_opt_in(target) and not is_opt_in(MATCH_SCHEDULED.to(PLAYERS))
    assert await NotificationPreferenceService.mode_for(9101, target) is OFF
    for preset in NotificationPreset:
        await NotificationPreferenceService.set_preset(player, preset)
        assert await NotificationPreferenceService.mode_for(9101, target) is OFF
    row = await NotificationPreferenceService.set_mode(player, target.key, PLAYERS, PING)
    assert row.opt_in and row.changed and row.effective is PING
    assert await NotificationPreferenceService.mode_for(9101, target) is PING
    assert await NotificationPreferenceService.opted_in_user_ids(target) == {player.id}


async def test_nobody_gets_an_opt_in_message_by_default():
    player = await make_member(discord_id=9100, username='oi0')
    mine = await NotificationPreferenceService.get_mine(player)
    opt_in_rows = [r for r in mine.rows if r.opt_in]
    assert opt_in_rows and all(r.effective is OFF for r in opt_in_rows)
    assert len([t for t in targets() if is_opt_in(t)]) >= 17


async def test_opponent_acknowledged_reaches_only_the_opponent_who_opted_in(sent):
    alice = await make_member(discord_id=9102, username='alice')
    bob = await make_member(discord_id=9103, username='bob')
    _, match = await _match(alice, bob)
    await opt_in_notifications.opponent_acknowledged(match.id, alice.id)
    assert sent == []

    await NotificationPreferenceService.set_mode(alice, OPPONENT_ACKNOWLEDGED.key, PLAYERS, PING)
    await NotificationPreferenceService.set_mode(bob, OPPONENT_ACKNOWLEDGED.key, PLAYERS, PING)
    await opt_in_notifications.opponent_acknowledged(match.id, alice.id)
    assert sent == [(9103, OPPONENT_ACKNOWLEDGED.to(PLAYERS))]


async def test_acknowledging_queues_the_opponent_dm(stub_discord_queue):
    alice = await make_member(discord_id=9104, username='alice2')
    bob = await make_member(discord_id=9105, username='bob2')
    _, match = await _match(alice, bob)
    await MatchService().acknowledge_match(match.id, alice)
    assert any(c.__name__ == 'opponent_acknowledged' for c in stub_discord_queue)


async def test_a_withdrawal_goes_to_admins_and_a_staff_removal_to_the_player(sent):
    player = await make_member(discord_id=9106, username='leaver')
    admin = await make_member(discord_id=9107, username='tadmin')
    tournament = await Tournament.create(name='Cup')
    await tournament.admins.add(admin)
    await NotificationPreferenceService.set_mode(admin, PLAYER_WITHDREW.key, ADMINS, PING)
    await NotificationPreferenceService.set_mode(player, REMOVED_FROM_TOURNAMENT.key, PLAYERS, PING)

    await opt_in_notifications.enrolment_changed(tournament.id, player.id, player.id, added=False)
    assert sent == [(9107, PLAYER_WITHDREW.to(ADMINS))]

    sent.clear()
    await opt_in_notifications.enrolment_changed(tournament.id, player.id, admin.id, added=False)
    assert sent == [(9106, REMOVED_FROM_TOURNAMENT.to(PLAYERS))]

    sent.clear()
    await opt_in_notifications.enrolment_changed(tournament.id, player.id, player.id, added=True)
    assert sent == []


async def test_a_qualifier_opening_reaches_members_who_opted_in(sent):
    keen = await make_member(discord_id=9108, username='keen')
    await make_member(discord_id=9109, username='quiet')
    await NotificationPreferenceService.set_mode(keen, QUALIFIER_OPENED.key, MEMBERS, PING)
    qualifier = await AsyncQualifier.create(name='Q1')
    await opt_in_notifications.qualifier_window(qualifier, opened=True)
    assert sent == [(9108, QUALIFIER_OPENED.to(MEMBERS))]


async def _follow(user, tournament, level=MatchNotificationLevel.ALL):
    await TournamentNotificationPreference.create(user=user, tournament=tournament, match_notifications=level)


async def test_a_confirmed_result_reaches_players_and_followers_who_opted_in(sent):
    alice = await make_member(discord_id=9110, username='res_a')
    bob = await make_member(discord_id=9111, username='res_b')
    fan = await make_member(discord_id=9112, username='res_fan')
    casual = await make_member(discord_id=9113, username='res_casual')
    tournament, match = await _match(alice, bob)
    await MatchPlayers.filter(match=match, user=alice).update(finish_rank=1, finish_time=3725)
    await MatchPlayers.filter(match=match, user=bob).update(finish_rank=2)
    await _follow(fan, tournament)
    await _follow(casual, tournament, MatchNotificationLevel.STREAMED)
    await _follow(alice, tournament)
    for user in (fan, casual, alice):
        await NotificationPreferenceService.set_mode(user, MATCH_RESULT.key, FOLLOWERS, PING)
    await NotificationPreferenceService.set_mode(bob, MATCH_RESULT.key, PLAYERS, PING)

    await opt_in_notifications.match_result(match.id)
    assert sorted(sent) == [(9111, MATCH_RESULT.to(PLAYERS)), (9112, MATCH_RESULT.to(FOLLOWERS))]


async def test_no_result_dm_without_a_recorded_result(sent):
    alice = await make_member(discord_id=9114, username='nores')
    bob = await make_member(discord_id=9115, username='nores2')
    _, match = await _match(alice, bob)
    await NotificationPreferenceService.set_mode(alice, MATCH_RESULT.key, PLAYERS, PING)
    await opt_in_notifications.match_result(match.id)
    assert sent == []


def test_result_text_names_the_winner_and_the_order():
    from application.utils.discord_messages_opt_in import match_result_dm
    assert match_result_dm('Cup', [('Alice', 1, 3725), ('Bob', 2, None)]) == \
        '**Alice** beat Bob in 1:02:05 in Cup.'
    text = match_result_dm('Cup', [('A', 1, None), ('B', 2, 60), ('C', None, None)])
    assert text.splitlines() == ['Cup result:', '1st: **A**', '2nd: **B** (0:01:00)', "Didn't finish: C"]


async def test_a_bracket_start_reaches_followers_who_opted_in(sent):
    fan = await make_member(discord_id=9116, username='bs_fan')
    lapsed = await make_member(discord_id=9117, username='bs_lapsed')
    tournament = await Tournament.create(name='Cup')
    await _follow(fan, tournament, MatchNotificationLevel.STREAMED)
    await _follow(lapsed, tournament, MatchNotificationLevel.NONE)
    for user in (fan, lapsed):
        await NotificationPreferenceService.set_mode(user, BRACKET_STARTED.key, FOLLOWERS, PING)
    await opt_in_notifications.bracket_started(tournament.id)
    assert sent == [(9116, BRACKET_STARTED.to(FOLLOWERS))]


async def test_a_new_event_reaches_members_who_opted_in_but_not_the_host(sent):
    host = await make_member(discord_id=9118, username='ev_host')
    keen = await make_member(discord_id=9119, username='ev_keen')
    for user in (host, keen):
        await NotificationPreferenceService.set_mode(user, EVENT_LISTED.key, MEMBERS, PING)
    event = await CommunityEvent.create(
        host=host, title='Board games', starts_at=utc(2026, 10, 23, 20), duration_minutes=60,
        status=CommunityEventStatus.APPROVED,
    )
    await opt_in_notifications.event_listed(event.id)
    assert sent == [(9119, EVENT_LISTED.to(MEMBERS))]
