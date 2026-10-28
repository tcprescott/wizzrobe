"""FriendService — the friends list behind Event > Schedule's Friends switch.

The ids arrive from a browser, so the save drops anything that isn't an active
account (and the viewer themselves) rather than storing it. The picker offers
friends first, then this event's players, and nobody from another event.
"""

from datetime import datetime, timezone

import pytest

from application.event_context import event_scope
from application.services.friend_service import FriendService
from application.tenant_context import tenant_scope
from models import Event, EventStatus, Match, MatchPlayers, Tournament, UserFriend
from tests.conftest import DEFAULT_TEST_EVENT_ID
from tests.factories import make_user

NOW = datetime(2026, 10, 23, 18, tzinfo=timezone.utc)


async def _play(user, event_id=DEFAULT_TEST_EVENT_ID):
    with event_scope(event_id):
        cup = await Tournament.create(name=f'Cup {user.id}')
        match = await Match.create(tournament=cup, scheduled_at=NOW)
        await MatchPlayers.create(match=match, user=user)


async def test_set_friends_replaces_the_list(db):
    me = await make_user(1, 'me')
    a, b, c = await make_user(2, 'ash'), await make_user(3, 'bo'), await make_user(4, 'cy')
    service = FriendService()

    assert await service.set_friends(me, [a.id, b.id]) == {a.id, b.id}
    assert await service.set_friends(me, [b.id, c.id]) == {b.id, c.id}
    assert await service.friend_ids(me) == {b.id, c.id}


async def test_set_friends_drops_self_unknown_and_inactive(db):
    me = await make_user(1, 'me')
    gone = await make_user(2, 'gone', is_active=False)
    friend = await make_user(3, 'pal')

    saved = await FriendService().set_friends(me, [me.id, gone.id, friend.id, 99999, str(friend.id)])

    assert saved == {friend.id}


async def test_set_friends_refuses_junk_and_too_many(db):
    me = await make_user(1, 'me')
    service = FriendService()

    with pytest.raises(ValueError):
        await service.set_friends(me, ['not a number'])
    with pytest.raises(ValueError, match='up to'):
        await service.set_friends(me, range(1000, 1000 + service.MAX_FRIENDS + 1))


async def test_the_list_is_one_per_person_not_per_community(db):
    me = await make_user(1, 'me')
    friend = await make_user(2, 'pal')
    with tenant_scope(1):
        await FriendService().set_friends(me, [friend.id])

    assert await UserFriend.filter(user=me).count() == 1
    assert await FriendService().friend_ids(me) == {friend.id}


async def test_friend_ids_is_empty_signed_out(db):
    assert await FriendService().friend_ids(None) == set()


async def test_picker_lists_friends_first_then_this_events_players(db):
    me = await make_user(1, 'me')
    zed = await make_user(2, 'zed', display_name='Zed')
    amy = await make_user(3, 'amy', display_name='Amy')
    away = await make_user(4, 'away', display_name='Away Friend')
    later = await make_user(5, 'later', display_name='Next Year')
    for player in (me, zed, amy):
        await _play(player)
    next_year = await Event.create(tenant_id=1, name='SGL 2027', slug='sgl27', status=EventStatus.LIVE, is_default=False)
    await _play(later, next_year.id)
    await FriendService().set_friends(me, [away.id])

    with event_scope(DEFAULT_TEST_EVENT_ID):
        options = await FriendService().picker_options(me)

    assert list(options) == [away.id, amy.id, zed.id]
    assert options[amy.id] == 'Amy'
