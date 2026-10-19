"""Staff request DMs: every staff copy is remembered, then edited once someone decides.

Rick, tsigma6 and Hurfy all get the "Community event to review" DM. Once one of
them approves it, the others' copies have to say so, or they all open it to
check. These drive each request type through the real services and read what
the queue would have sent and edited.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.services import MatchRescheduleService
from application.services.accommodation_service import AccommodationService
from application.services.community_event_service import CommunityEventService, EventDraft
from application.services.tenant_membership_service import TenantMembershipService
from models import (
    AccommodationStatus,
    Match,
    MatchPlayers,
    Role,
    StaffRequestDM,
    StaffRequestKind,
    TenantMembership,
    Tournament,
    User,
    UserRole,
)
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.factories import make_user

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
def discord(monkeypatch):
    """What the queue sends and edits, run in order the way its worker would."""
    from application.services import discord as discord_pkg
    from application.services.discord import DiscordService

    log = {'sent': [], 'edited': []}
    message_ids = iter(range(5001, 6000))

    async def fake_send(self, user_id, message, view_factory=None, embed=None, link=None,
                        on_sent=None, notice=None):
        log['sent'].append(user_id)
        if on_sent is not None:
            await on_sent(user_id, next(message_ids))
        return True, ''

    async def fake_edit(self, channel_id, message_id, embed, link=None):
        log['edited'].append({
            'to': channel_id, 'title': embed.title, 'text': embed.description,
            'link': link.label if link else None,
        })
        return True, ''

    monkeypatch.setattr(DiscordService, 'send_dm', fake_send)
    monkeypatch.setattr(DiscordService, 'edit_dm', fake_edit)
    pending: list = []
    monkeypatch.setattr(discord_pkg.discord_queue, 'enqueue', pending.append)

    async def flush() -> dict:
        """What was sent and edited since the last flush."""
        while pending:
            await pending.pop(0)
        batch = {key: list(value) for key, value in log.items()}
        for value in log.values():
            value.clear()
        return batch

    return flush


async def _staff(discord_id: int, name: str) -> User:
    user = await make_user(discord_id=discord_id, username=name)
    await TenantMembership.create(user=user, tenant_id=DEFAULT_TEST_TENANT_ID)
    await UserRole.create(user=user, role=Role.STAFF, tenant_id=DEFAULT_TEST_TENANT_ID)
    return user


async def _member(discord_id: int, name: str) -> User:
    user = await make_user(discord_id=discord_id, username=name)
    await TenantMembership.create(user=user, tenant_id=DEFAULT_TEST_TENANT_ID)
    return user


def _by_recipient(edited: list) -> dict:
    return {e['to']: e for e in edited}


class TestCommunityEvents:
    @staticmethod
    def _draft() -> EventDraft:
        return EventDraft(
            title='Melee Friendlies', description='', location='Hotel bar',
            starts_at=datetime.now(timezone.utc) + timedelta(days=2), duration_minutes=120,
        )

    async def test_approving_edits_every_staff_copy_and_says_you_on_your_own(self, discord):
        await _staff(701, 'rick')
        hurfy = await _staff(702, 'hurfy')
        host = await _member(703, 'host')
        event = await CommunityEventService().submit(host, self._draft())
        log = await discord()
        assert sorted(log['sent']) == [701, 702]
        assert await StaffRequestDM.filter(object_id=event.id).count() == 2

        await CommunityEventService().approve(hurfy, event.id)
        edited = _by_recipient((await discord())['edited'])

        assert set(edited) == {701, 702}
        assert edited[701]['title'] == '✅ Approved by hurfy'
        assert edited[702]['title'] == '✅ Approved by You'
        assert 'Melee Friendlies' in edited[701]['text']
        assert edited[701]['link'] == 'View the event'

    async def test_rejecting_says_why_and_a_later_approval_edits_again(self, discord):
        await _staff(711, 'rick')
        tsigma = await _staff(712, 'tsigma')
        host = await _member(713, 'host')
        event = await CommunityEventService().submit(host, self._draft())
        await discord()

        await CommunityEventService().reject(tsigma, event.id, 'Start after the last bracket')
        edited = _by_recipient((await discord())['edited'])
        assert edited[711]['title'] == '🚫 Rejected by tsigma'
        assert 'Start after the last bracket' in edited[711]['text']

        await CommunityEventService().approve(tsigma, event.id)
        assert _by_recipient((await discord())['edited'])[711]['title'] == '✅ Approved by tsigma'

    async def test_a_resubmission_sends_fresh_dms_and_leaves_the_old_ones_alone(self, discord):
        rick = await _staff(721, 'rick')
        host = await _member(722, 'host')
        service = CommunityEventService()
        event = await service.submit(host, self._draft())
        await service.reject(rick, event.id, 'No')
        await discord()

        await service.update(host, event.id, self._draft())
        await discord()
        rows = await StaffRequestDM.filter(object_id=event.id)
        assert [r.message_id for r in rows] == [5002]

    async def test_the_host_cancelling_a_pending_event_tells_staff(self, discord):
        await _staff(731, 'rick')
        host = await _member(732, 'host')
        event = await CommunityEventService().submit(host, self._draft())
        await discord()

        await CommunityEventService().cancel(host, event.id)
        edited = (await discord())['edited']
        assert [e['title'] for e in edited] == ['↩️ Cancelled by host']


class TestAdaRequests:
    async def test_acknowledging_is_handled_and_a_change_reopens_the_card(self, discord):
        rick = await _staff(741, 'rick')
        await _staff(742, 'hurfy')
        jordan = await _member(743, 'jordan')
        service = AccommodationService()
        request = await service.set_my_request(jordan, True, 'Seat near the door')
        await discord()

        await service.update_request(rick, request.id, AccommodationStatus.ACKNOWLEDGED, None)
        edited = _by_recipient((await discord())['edited'])
        assert edited[742]['title'] == '✅ Acknowledged by rick'
        assert edited[741]['title'] == '✅ Acknowledged by You'
        assert "jordan" in edited[742]['text']

        # Changing the details on an acknowledged request puts it back to New
        # without a fresh DM, so the copies go back to asking for a look.
        await service.set_my_request(jordan, True, 'Seat near the door, and a chair')
        edited = (await discord())['edited']
        assert {e['title'] for e in edited} == {'♿ ADA request'}

    async def test_withdrawing_tells_staff_there_is_nothing_left(self, discord):
        await _staff(751, 'rick')
        jordan = await _member(752, 'jordan')
        service = AccommodationService()
        await service.set_my_request(jordan, True, 'x')
        await discord()

        await service.set_my_request(jordan, False)
        assert [e['title'] for e in (await discord())['edited']] == ['↩️ Request withdrawn']


class TestJoinRequests:
    async def test_declining_edits_the_staff_copies(self, discord):
        await _staff(761, 'rick')
        synack = await _staff(762, 'synack')
        outsider = await make_user(discord_id=763, username='pinkpanther')
        service = TenantMembershipService()
        request = await service.request_to_join(outsider, DEFAULT_TEST_TENANT_ID)
        await discord()

        await service.deny_request(synack, request.id)
        edited = _by_recipient((await discord())['edited'])
        assert edited[761]['title'] == '🚫 Declined by synack'
        assert edited[762]['title'] == '🚫 Declined by You'

    async def test_being_let_in_another_way_closes_it_out(self, discord):
        await _staff(771, 'rick')
        synack = await _staff(772, 'synack')
        outsider = await make_user(discord_id=773, username='pinkpanther')
        service = TenantMembershipService()
        await service.request_to_join(outsider, DEFAULT_TEST_TENANT_ID)
        await discord()

        await service.add_member(synack, outsider)
        edited = _by_recipient((await discord())['edited'])
        assert edited[771]['title'] == '✅ Approved by synack'


class TestRescheduleRequests:
    @staticmethod
    async def _match():
        tournament = await Tournament.create(name='ALTTPR', allow_reschedule_requests=True)
        match = await Match.create(
            tournament=tournament, scheduled_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
        p1 = await make_user(discord_id=9301, username='link28')
        p2 = await make_user(discord_id=9302, username='zelda')
        await MatchPlayers.create(match=match, user=p1)
        await MatchPlayers.create(match=match, user=p2)
        return match, p1

    async def _submit(self, match, player):
        return await MatchRescheduleService().submit(
            match.id, player, reason='work',
            proposed_at=datetime.now(timezone.utc) + timedelta(days=2),
        )

    async def test_declining_edits_the_deciders_copies(self, discord):
        rick = await _staff(781, 'rick')
        await _staff(782, 'hurfy')
        match, p1 = await self._match()
        request = await self._submit(match, p1)
        log = await discord()
        assert {781, 782} <= set(log['sent'])

        await MatchRescheduleService().decline(request.id, rick, 'No free stage')
        edited = _by_recipient((await discord())['edited'])
        assert edited[782]['title'] == '🚫 Declined by rick'
        assert 'ALTTPR' in edited[782]['text']
        assert edited[782]['link'] == 'Open the match'

    async def test_withdrawing_tells_the_deciders(self, discord):
        await _staff(791, 'rick')
        match, p1 = await self._match()
        request = await self._submit(match, p1)
        await discord()

        await MatchRescheduleService().withdraw(request.id, p1)
        assert [e['title'] for e in (await discord())['edited']] == ['↩️ Request withdrawn']


class TestDiscordService:
    @pytest.fixture
    def real_service(self, monkeypatch):
        from application.services.discord import discord_service as ds

        sent_message = MagicMock()
        sent_message.id = 42
        sent_message.channel.id = 7
        discord_user = MagicMock()
        discord_user.send = AsyncMock(return_value=sent_message)
        bot = MagicMock()
        bot.is_ready.return_value = True
        bot.get_user.return_value = None
        bot.fetch_user = AsyncMock(return_value=discord_user)
        monkeypatch.setattr(ds, '_mirror_dm_to_web_push', lambda *a, **kw: None)
        service = object.__new__(ds._RealDiscordService)
        service._bot = bot
        return service, bot

    async def test_send_reports_where_the_message_landed(self, real_service):
        service, _bot = real_service
        landed: list = []

        async def on_sent(channel_id, message_id):
            landed.append((channel_id, message_id))

        ok, _ = await service.send_dm(1234, 'hi', on_sent=on_sent)
        assert ok
        assert landed == [(7, 42)]

    async def test_a_failing_hook_is_not_a_failed_send(self, real_service):
        service, _bot = real_service

        async def boom(channel_id, message_id):
            raise RuntimeError('db down')

        ok, _ = await service.send_dm(1234, 'hi', on_sent=boom)
        assert ok

    async def test_edit_replaces_the_card_and_buttons(self, real_service):
        import discord as discord_lib

        from application.utils.discord_messages import DMLink

        service, bot = real_service
        partial = MagicMock()
        partial.edit = AsyncMock()
        bot.get_partial_messageable.return_value.get_partial_message.return_value = partial

        embed = discord_lib.Embed(title='✅ Approved by hurfy')
        ok, _ = await service.edit_dm(7, 42, embed, DMLink(label='View the event', url='https://x.test/e'))

        assert ok
        bot.get_partial_messageable.assert_called_once_with(7, type=discord_lib.ChannelType.private)
        bot.get_partial_messageable.return_value.get_partial_message.assert_called_once_with(42)
        kwargs = partial.edit.await_args.kwargs
        assert kwargs['embed'] is embed
        assert [item.label for item in kwargs['view'].children] == ['View the event']


def test_kinds_are_stable_strings():
    assert {k.value for k in StaffRequestKind} == {
        'community_event', 'ada_request', 'join_request', 'reschedule_request',
    }
