"""The DMs a stage assignment sends, and who gets them.

``MatchService.assign_stage`` used to change a row and refresh an open page:
the two people it most concerns learned about it when somebody walked over and
told them. These drive the real fan-out against the in-memory ORM — the audience
(players and *approved* crew), the retraction when a stage is cleared, the
reminder stamp that re-arms on every change, and the link each DM carries.
"""

from unittest.mock import AsyncMock

import pytest

from application.services.match.match_service import MatchService
from application.services.notification_catalog import CREW, PLAYERS, STAGE_CALL, WATCHERS
from models import (
    Commentator,
    Match,
    MatchPlayers,
    MatchWatcher,
    NotificationMode,
    Role,
    Stage,
    Tournament,
    Tracker,
    User,
    UserRole,
)
from tests.factories import make_user, utc


@pytest.fixture
def sent(monkeypatch):
    """Every ``send_dm``/``send_dm_with_unwatch_button`` call, as kwargs dicts."""
    calls: list[dict] = []

    async def _send(discord_id, message, *args, **kwargs):
        calls.append({'discord_id': discord_id, 'message': message, **kwargs})
        return True, 'ok'

    monkeypatch.setattr(
        'application.services.discord.discord_service.DiscordService.send_dm',
        AsyncMock(side_effect=_send),
    )
    monkeypatch.setattr(
        'application.services.discord.discord_service.DiscordService'
        '.send_dm_with_unwatch_button',
        AsyncMock(side_effect=_send),
    )
    return calls


@pytest.fixture
async def fixture(db):
    """Staff actor, a two-player match, one approved and one pending commentator."""
    staff = await make_user(9000, 'staff')
    await UserRole.create(user=staff, role=Role.STAFF)
    p1 = await make_user(1, 'p1')
    p2 = await make_user(2, 'p2')
    approved = await make_user(3, 'approved_comm')
    pending = await make_user(4, 'pending_comm')

    tournament = await Tournament.create(name='Cup')
    match = await Match.create(
        title='Game 1', tournament=tournament, scheduled_at=utc(2026, 10, 23, 18),
    )
    for user in (p1, p2):
        await MatchPlayers.create(match=match, user=user)
    await Commentator.create(match=match, user=approved, approved=True)
    await Commentator.create(match=match, user=pending, approved=False)
    stage = await Stage.create(name='Kraid')
    other_stage = await Stage.create(name='Ridley')
    return {
        'staff': staff, 'match': match, 'stage': stage, 'other_stage': other_stage,
        'players': [p1, p2], 'approved': approved, 'pending': pending,
    }


async def _drain(queue) -> None:
    """Await the coroutines the service handed to ``discord_queue``."""
    pending = list(queue)
    queue.clear()
    for coro in pending:
        await coro


async def test_assigning_a_stage_dms_players_and_approved_crew(
    fixture, sent, stub_discord_queue,
):
    await MatchService().assign_stage(
        fixture['match'].id, fixture['stage'].id, fixture['staff'],
    )
    await _drain(stub_discord_queue)

    told = {call['discord_id'] for call in sent}
    assert told == {1, 2, 3}
    assert all('Kraid' in call['message'] for call in sent)


async def test_pending_crew_is_told_nothing(fixture, sent, stub_discord_queue):
    """A commentator nobody approved has not been given the job."""
    await MatchService().assign_stage(
        fixture['match'].id, fixture['stage'].id, fixture['staff'],
    )
    await _drain(stub_discord_queue)

    assert fixture['pending'].discord_id not in {call['discord_id'] for call in sent}


async def test_clearing_the_stage_retracts_and_rearms(
    fixture, sent, stub_discord_queue,
):
    service = MatchService()
    await service.assign_stage(fixture['match'].id, fixture['stage'].id, fixture['staff'])
    await _drain(stub_discord_queue)
    sent.clear()

    await service.assign_stage(fixture['match'].id, None, fixture['staff'])
    await _drain(stub_discord_queue)

    assert sent, 'clearing a stage must send its own DM'
    assert fixture['approved'].discord_id in {call['discord_id'] for call in sent}, (
        'crew were told they are on stage, so they must hear it was called off'
    )
    assert all('tournament room' in call['message'] for call in sent)
    assert all('Kraid' not in call['message'] for call in sent)
    match = await Match.get(id=fixture['match'].id)
    assert match.stage_reminder_sent_at is None


async def test_reassigning_arms_the_reminder_again(fixture, stub_discord_queue):
    """Assign, clear, reassign: the stamp has to be gone each time.

    A stamp that survived a stage change would suppress the reminder for the
    stage the match actually ended up on.
    """
    service = MatchService()
    match_id = fixture['match'].id

    for stage_id in (fixture['stage'].id, None, fixture['other_stage'].id):
        await Match.filter(id=match_id).update(stage_reminder_sent_at=utc(2026, 10, 23, 17))
        await service.assign_stage(match_id, stage_id, fixture['staff'])
        assert (await Match.get(id=match_id)).stage_reminder_sent_at is None
        await _drain(stub_discord_queue)


async def test_every_stage_dm_carries_an_absolute_tenant_link(
    fixture, sent, stub_discord_queue,
):
    await MatchService().assign_stage(
        fixture['match'].id, fixture['stage'].id, fixture['staff'],
    )
    await _drain(stub_discord_queue)

    assert sent
    for call in sent:
        link = call['link']
        assert link is not None
        assert link.url.startswith('http')


async def test_each_audience_gets_its_own_copy_and_button(
    fixture, sent, stub_discord_queue,
):
    """Players land on their match, crew on their slot, a watcher on the schedule.

    All three used to get "Your match…" and the player filter, which showed crew
    and watchers "No matches to show yet".
    """
    watcher = await make_user(6, 'watcher')
    await MatchWatcher.create(match=fixture['match'], user=watcher)
    match_id = fixture['match'].id

    await MatchService().assign_stage(match_id, fixture['stage'].id, fixture['staff'])
    await _drain(stub_discord_queue)

    by_id = {call['discord_id']: call for call in sent}
    for player_id in (1, 2):
        assert by_id[player_id]['message'].startswith('Your match in **Cup**')
        assert by_id[player_id]['link'].url.endswith(f'/home/player?match={match_id}')
    crew = by_id[fixture['approved'].discord_id]
    assert crew['message'].startswith("A match you're commentating in **Cup**")
    assert crew['link'].url.endswith(f'/home/my-crew?match={match_id}')
    assert crew['embed'].title == '📺 Your commentator slot is on stage'
    watching = by_id[6]
    assert watching['message'].startswith("A match you're watching in **Cup**")
    assert watching['link'].url.endswith('/home/schedule')
    # Players and crew have somewhere to be; a watcher only follows along,
    # so the catalogue quiets only the watcher's copy.
    assert watching['notice'] == STAGE_CALL.to(WATCHERS)
    assert watching['notice'].default is NotificationMode.SILENT
    for pid in (1, 2):
        assert by_id[pid]['notice'] == STAGE_CALL.to(PLAYERS)
    assert crew['notice'] == STAGE_CALL.to(CREW)
    assert STAGE_CALL.to(PLAYERS).default is NotificationMode.PING
    assert STAGE_CALL.to(CREW).default is NotificationMode.PING


async def test_reminder_dm_names_the_stage_and_the_time(
    fixture, sent, stub_discord_queue,
):
    """The worker's copy path, driven directly — the worker owns only the timing."""
    from application.services.match.match_schedule_service import MatchScheduleService

    match = await Match.get(id=fixture['match'].id)
    await MatchScheduleService().notify_stage_reminder(match, stage_name='Kraid')

    assert sent
    for call in sent:
        assert 'coming up on **Kraid**' in call['message']
        # The commentator is reminded about their slot, not told to play.
        if call['discord_id'] == fixture['approved'].discord_id:
            assert call['message'].startswith("A match you're commentating")
            assert call['embed'].title == '⏰ Your commentator slot on stage is coming up'
        assert '<t:' in call['message']


async def test_a_dm_failure_never_reaches_the_caller(fixture, monkeypatch, stub_discord_queue):
    monkeypatch.setattr(
        'application.services.discord.discord_service.DiscordService.send_dm',
        AsyncMock(side_effect=RuntimeError('discord is down')),
    )
    await MatchService().assign_stage(
        fixture['match'].id, fixture['stage'].id, fixture['staff'],
    )
    await _drain(stub_discord_queue)  # must not raise

    assert (await Match.get(id=fixture['match'].id)).stage_id == fixture['stage'].id


async def test_a_tracker_who_is_approved_is_told_too(fixture, sent, stub_discord_queue):
    tracker_user = await User.create(discord_id=5, username='tracker')
    await Tracker.create(match=fixture['match'], user=tracker_user, approved=True)

    await MatchService().assign_stage(
        fixture['match'].id, fixture['stage'].id, fixture['staff'],
    )
    await _drain(stub_discord_queue)

    assert 5 in {call['discord_id'] for call in sent}


def test_stage_embed_is_not_coloured_like_a_schedule_change():
    from application.utils.discord_embeds import (
        COLOR_RESCHEDULED,
        COLOR_SCHEDULED,
        COLOR_STAGE,
        stage_embed,
    )

    embed = stage_embed(title='📺 You are on stage', tournament='Cup', stage_name='Kraid')
    assert COLOR_STAGE not in (COLOR_SCHEDULED, COLOR_RESCHEDULED)
    assert embed.colour.value == COLOR_STAGE


async def test_moving_the_match_arms_the_reminder_again(fixture, stub_discord_queue):
    """A reminder sent for the old time must not suppress the one for the new time."""
    match_id = fixture['match'].id
    await Match.filter(id=match_id).update(stage_reminder_sent_at=utc(2026, 10, 23, 17))

    await MatchService().update_match(
        match_id, scheduled_at=utc(2026, 10, 23, 20), actor=fixture['staff'],
    )
    await _drain(stub_discord_queue)

    assert (await Match.get(id=match_id)).stage_reminder_sent_at is None


async def test_an_edit_that_keeps_the_time_leaves_the_reminder_sent(fixture, stub_discord_queue):
    match_id = fixture['match'].id
    await Match.filter(id=match_id).update(stage_reminder_sent_at=utc(2026, 10, 23, 17))

    await MatchService().update_match(match_id, comment='Bring a controller', actor=fixture['staff'])
    await _drain(stub_discord_queue)

    assert (await Match.get(id=match_id)).stage_reminder_sent_at is not None


async def test_the_reminder_gives_players_a_running_late_button(
    fixture, sent, stub_discord_queue, monkeypatch,
):
    """Players' copies carry Running late; crew's don't, since it's the player's match."""
    from application.services.match.match_schedule_service import MatchScheduleService

    with_button: list[int] = []

    async def _late(discord_id, message, match_id, **kwargs):
        with_button.append(discord_id)
        return True, 'ok'

    monkeypatch.setattr(
        'application.services.discord.discord_service.DiscordService'
        '.send_dm_with_player_late_button',
        AsyncMock(side_effect=_late),
    )
    await Match.filter(id=fixture['match'].id).update(stage_id=fixture['stage'].id)
    match = await Match.get(id=fixture['match'].id)
    await MatchScheduleService().notify_stage_reminder(match, stage_name='Kraid')

    assert sorted(with_button) == [1, 2]
    assert fixture['approved'].discord_id in {call['discord_id'] for call in sent}
