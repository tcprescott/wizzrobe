"""Pair commentary: invite a partner, they accept or decline, staff approve each one."""

from unittest.mock import AsyncMock

import pytest

from application.services import CommentaryInviteService, CrewService
from application.services.commentary_invite_service import INVITES_PER_HOUR
from models import (
    CommentaryInvite,
    CommentaryInviteStatus,
    Commentator,
    Match,
    MatchPlayers,
    Tournament,
)
from tests.factories import make_member, make_user

pytestmark = pytest.mark.usefixtures("bypass_auth")


@pytest.fixture(autouse=True)
def queued(monkeypatch):
    """Run nothing on Discord; keep what was queued so a test can play it."""
    jobs: list = []
    monkeypatch.setattr('application.services.commentary_invite_service.discord_queue.enqueue', jobs.append)
    monkeypatch.setattr('application.services.crew_service.CrewService._notify_crew_owners_of_signup', AsyncMock())
    monkeypatch.setattr('application.services.crew_service.CrewService._notify_crew_withdrawal', AsyncMock())
    yield jobs
    for job in jobs:
        job.close()


async def _setup(need: int = 2):
    tournament = await Tournament.create(name='Cup', is_active=True, required_commentators=need)
    match = await Match.create(tournament=tournament, title='Final')
    andy = await make_member(11, 'andy')
    benny = await make_member(12, 'benny')
    await CrewService().signup_crew(match.id, andy, 'commentator')
    return match, andy, benny


async def test_invite_then_accept_signs_the_partner_up_unapproved(db, queued):
    match, andy, benny = await _setup()
    service = CommentaryInviteService()
    invite = await service.invite(match.id, andy, benny.id)
    assert invite.status is CommentaryInviteStatus.PENDING
    assert not await Commentator.filter(match=match, user=benny).exists()
    assert [i.id for i in await service.pending_for(benny)] == [invite.id]

    await service.respond(invite.id, benny, accept=True)

    row = await Commentator.get(match=match, user=benny)
    assert row.approved is False and row.invited_by_id == andy.id
    assert (await CommentaryInvite.get(id=invite.id)).status is CommentaryInviteStatus.ACCEPTED
    assert await service.pending_for(benny) == []


async def test_decline_signs_nobody_up(db):
    match, andy, benny = await _setup()
    service = CommentaryInviteService()
    invite = await service.invite(match.id, andy, benny.id)
    await service.respond(invite.id, benny, accept=False)
    assert not await Commentator.filter(match=match, user=benny).exists()
    with pytest.raises(ValueError, match='already been declined'):
        await service.respond(invite.id, benny, accept=True)


async def test_only_the_invitee_can_answer(db):
    match, andy, benny = await _setup()
    invite = await CommentaryInviteService().invite(match.id, andy, benny.id)
    with pytest.raises(ValueError, match='someone else'):
        await CommentaryInviteService().respond(invite.id, andy, accept=True)


async def test_staff_approve_each_commentator_on_their_own(db):
    match, andy, benny = await _setup()
    service = CommentaryInviteService()
    invite = await service.invite(match.id, andy, benny.id)
    await service.respond(invite.id, benny, accept=True)
    staff = await make_user(99, 'staff')
    andy_row = await Commentator.get(match=match, user=andy)
    await CrewService().update_crew_approval(andy_row, 'commentator', True, actor=staff)
    assert (await Commentator.get(match=match, user=andy)).approved
    assert not (await Commentator.get(match=match, user=benny)).approved


async def test_withdrawing_cancels_a_waiting_invite_and_edits_its_dm(db, queued, monkeypatch):
    match, andy, benny = await _setup()
    invite = await CommentaryInviteService().invite(match.id, andy, benny.id)
    await CommentaryInvite.filter(id=invite.id).update(dm_channel_id=5, dm_message_id=6)
    queued.clear()

    await CrewService().undo_crew_signup(match.id, andy, 'commentator')

    assert (await CommentaryInvite.get(id=invite.id)).status is CommentaryInviteStatus.CANCELLED
    edit = AsyncMock(return_value=(True, 'ok'))
    monkeypatch.setattr('application.services.commentary_invite_service.DiscordService.edit_dm', edit)
    await next(job for job in queued if 'edit' in job.__qualname__)
    assert edit.await_args.args[:2] == (5, 6)
    assert edit.await_args.args[2].title == '↩️ Invite cancelled'


async def test_withdrawing_leaves_an_accepted_partner_signed_up(db):
    match, andy, benny = await _setup()
    service = CommentaryInviteService()
    invite = await service.invite(match.id, andy, benny.id)
    await service.respond(invite.id, benny, accept=True)
    await CrewService().undo_crew_signup(match.id, andy, 'commentator')
    assert await Commentator.filter(match=match, user=benny).exists()
    assert (await CommentaryInvite.get(id=invite.id)).status is CommentaryInviteStatus.ACCEPTED


async def test_one_partner_per_match(db):
    match, andy, benny = await _setup()
    cleo = await make_member(13, 'cleo')
    service = CommentaryInviteService()
    await service.invite(match.id, andy, benny.id)
    with pytest.raises(ValueError, match='already invited'):
        await service.invite(match.id, andy, cleo.id)


async def test_cancelled_invite_frees_the_slot(db):
    match, andy, benny = await _setup()
    cleo = await make_member(13, 'cleo')
    service = CommentaryInviteService()
    invite = await service.invite(match.id, andy, benny.id)
    await service.cancel(invite.id, andy)
    again = await service.invite(match.id, andy, cleo.id)
    assert again.invitee_id == cleo.id


@pytest.mark.parametrize('case, message', [
    ('not_signed_up', 'Sign up as commentator first'),
    ('one_commentator', 'only needs one commentator'),
    ('self', "can't invite yourself"),
    ('outsider', "isn't a member"),
    ('player', 'is playing in this match'),
    ('already_on', 'already signed up to commentate'),
    ('started', 'already started'),
    ('full', 'already full'),
])
async def test_refusals(db, case, message):
    from datetime import datetime, timezone

    match, andy, benny = await _setup(need={'one_commentator': 1, 'already_on': 3}.get(case, 2))
    inviter, invitee = andy, benny
    if case == 'not_signed_up':
        inviter, invitee = benny, andy
    elif case == 'self':
        invitee = andy
    elif case == 'outsider':
        invitee = await make_user(77, 'stranger')
    elif case == 'player':
        await MatchPlayers.create(match=match, user=benny)
    elif case == 'already_on':
        await CrewService().signup_crew(match.id, benny, 'commentator')
    elif case == 'full':
        invitee = await make_member(14, 'dana')
        await CrewService().signup_crew(match.id, benny, 'commentator')
    elif case == 'started':
        await Match.filter(id=match.id).update(started_at=datetime.now(timezone.utc))
    with pytest.raises(ValueError, match=message):
        await CommentaryInviteService().invite(match.id, inviter, invitee.id)


async def test_invites_are_rate_limited(db, monkeypatch):
    monkeypatch.setattr('application.services.commentary_invite_service.INVITES_PER_HOUR', 1)
    match, andy, benny = await _setup()
    other = await Match.create(tournament_id=match.tournament_id, title='Other')
    await CrewService().signup_crew(other.id, andy, 'commentator')
    service = CommentaryInviteService()
    await service.invite(match.id, andy, benny.id)
    with pytest.raises(ValueError, match='lot of invites'):
        await service.invite(other.id, andy, benny.id)
    assert INVITES_PER_HOUR == 10


async def test_partner_choices_leave_out_who_cannot_be_asked(db):
    match, andy, benny = await _setup()
    cleo = await make_member(13, 'cleo')
    await MatchPlayers.create(match=match, user=cleo)
    choices = await CommentaryInviteService().partner_choices(match.id, andy)
    assert benny.id in choices
    assert andy.id not in choices and cleo.id not in choices


async def test_invite_sends_the_partner_a_dm_with_buttons(db, queued, monkeypatch):
    match, andy, benny = await _setup()
    send = AsyncMock(return_value=(True, 'sent'))
    monkeypatch.setattr(
        'application.services.commentary_invite_service.DiscordService.send_dm_with_partner_invite_buttons', send,
    )
    invite = await CommentaryInviteService().invite(match.id, andy, benny.id)
    await queued.pop()
    args, kwargs = send.await_args
    assert args[0] == 12 and args[2] == invite.id
    assert kwargs['notice'].key == 'crew_partner_invite'
    assert kwargs['link'].url.endswith('/home/my-crew')
    await kwargs['on_sent'](7, 8)
    stored = await CommentaryInvite.get(id=invite.id)
    assert (stored.dm_channel_id, stored.dm_message_id) == (7, 8)


async def test_pairs_on_a_match_nobody_has_booked_follow_it_to_the_booking(db, monkeypatch):
    from application.services import CrewHoldService, bracket_sources
    from tests.services.test_crew_holds import _final

    actor, *_, final = await _final(monkeypatch)
    await Tournament.filter(id=final.tournament_id).update(required_commentators=4)
    ana = await make_member(50, 'ana')
    bo = await make_member(51, 'bo')
    cy = await make_member(52, 'cy')
    dee = await make_member(53, 'dee')
    hold = await CrewHoldService().signup('challonge', final.id, ana, 'commentator')
    service = CommentaryInviteService()
    accepted = await service.invite(hold.id, ana, bo.id)
    await service.respond(accepted.id, bo, accept=True)
    assert (await Commentator.get(user=bo)).match_id == hold.id

    await CrewService().signup_crew(hold.id, cy, 'commentator')
    waiting = await service.invite(hold.id, cy, dee.id)

    match = await bracket_sources.book_ahead('challonge', final.id, '2026-10-24', '18:00', actor)

    assert (await Commentator.get(user=bo)).match_id == match.id
    assert (await CommentaryInvite.get(id=waiting.id)).match_id == match.id


async def test_a_partner_who_left_frees_the_slot(db):
    match, andy, benny = await _setup()
    cleo = await make_member(13, 'cleo')
    service = CommentaryInviteService()
    invite = await service.invite(match.id, andy, benny.id)
    await service.respond(invite.id, benny, accept=True)
    with pytest.raises(ValueError, match='already invited'):
        await service.invite(match.id, andy, cleo.id)
    await CrewService().undo_crew_signup(match.id, benny, 'commentator')
    assert (await service.invite(match.id, andy, cleo.id)).invitee_id == cleo.id


def _row(viewer_user, people, **extra):
    return {'id': 1, 'state': 'Scheduled', 'crew_signup_open': True, 'commentary_seats': 2,
            'crew_wanted': {'commentators': True, 'trackers': False},
            'crew_required': {'commentators': 2}, 'commentators': people, **extra}


@pytest.mark.parametrize('people, invites, can_invite', [
    ([{'discord_id': '11', 'user_id': 1}], [], True),
    ([{'discord_id': '11', 'user_id': 1}], [{'id': 9, 'inviter_discord_id': '11', 'invitee': 'B'}], False),
    ([{'discord_id': '11', 'user_id': 1}, {'discord_id': '12', 'user_id': 2, 'invited_by_id': 1}], [], False),
    ([{'discord_id': '11', 'user_id': 1, 'invited_by_id': 2}, {'discord_id': '12', 'user_id': 2}], [], False),
    ([{'discord_id': '12', 'user_id': 2}], [], False),
    ([{'discord_id': '11', 'user_id': 1}, {'discord_id': '13', 'user_id': 3}], [], False),
])
def test_invite_a_partner_shows_only_for_an_unpaired_commentator(people, invites, can_invite):
    from theme.tables.match_player_run_sheet import player_crew

    roles = player_crew(_row(None, people, partner_invites=invites), '11')
    commentary = next(r for r in roles if r['key'] == 'commentators')
    assert commentary['can_invite'] is can_invite
    assert (commentary['my_invite'] is not None) is bool(invites)


def test_other_viewers_never_see_who_was_invited():
    from theme.tables.match_player_run_sheet import decorate_player_row

    row = _row(None, [{'discord_id': '11', 'user_id': 1, 'name': 'A'}],
               partner_invites=[{'id': 9, 'inviter_discord_id': '11', 'invitee': 'B'}],
               players=[], trackers=[])
    decorate_player_row(row, '99')
    assert row['partner_invites'] == []
