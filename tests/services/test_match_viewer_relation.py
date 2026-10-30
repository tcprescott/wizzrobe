"""What a match is to the person a DM's link brought to it.

``?match=<id>`` on My Schedule used to filter the player's board to that match
whoever opened it, so a commentator following a stage DM got "No matches to
show yet". The page now asks :meth:`MatchService.viewer_relation` first.
"""

from application.services.match.match_reads import (
    RELATION_CREW,
    RELATION_NONE,
    RELATION_PLAYER,
    RELATION_WATCHER,
)
from application.services.match.match_service import MatchService
from models import Commentator, Match, MatchPlayers, MatchWatcher, Tournament, Tracker
from tests.factories import make_user, utc


async def test_each_tie_to_a_match_is_named(db):
    t = await Tournament.create(name='Cup')
    m = await Match.create(tournament=t, scheduled_at=utc(2026, 10, 23, 18))
    player = await make_user(1, 'player')
    commentator = await make_user(2, 'comm')
    pending_tracker = await make_user(3, 'tracker')
    watcher = await make_user(4, 'watcher')
    stranger = await make_user(5, 'stranger')
    await MatchPlayers.create(match=m, user=player)
    await Commentator.create(match=m, user=commentator, approved=True)
    await Tracker.create(match=m, user=pending_tracker, approved=False)
    await MatchWatcher.create(match=m, user=watcher)
    service = MatchService()

    assert await service.viewer_relation(m.id, player) == RELATION_PLAYER
    assert await service.viewer_relation(m.id, commentator) == RELATION_CREW
    assert await service.viewer_relation(m.id, pending_tracker) == RELATION_CREW
    assert await service.viewer_relation(m.id, watcher) == RELATION_WATCHER
    assert await service.viewer_relation(m.id, stranger) == RELATION_NONE
    assert await service.viewer_relation(m.id, None) == RELATION_NONE
    assert await service.viewer_relation(999999, player) == RELATION_NONE


async def test_a_player_who_also_watches_is_still_a_player(db):
    t = await Tournament.create(name='Cup')
    m = await Match.create(tournament=t)
    player = await make_user(1, 'player')
    await MatchPlayers.create(match=m, user=player)
    await MatchWatcher.create(match=m, user=player)

    assert await MatchService().viewer_relation(m.id, player) == RELATION_PLAYER
