"""Admin → Members' member sheet: one member's footprint here, staff only, community-scoped."""

from datetime import datetime, timedelta, timezone

import pytest

from application.errors import NotFoundError
from application.services import MemberDetailsService
from application.services.notification_catalog import MATCHUP_READY, PLAYERS, STAGE_CALL
from application.services.notification_preference_service import NotificationPreferenceService
from application.tenant_context import tenant_scope
from models import (
    Match,
    MatchPlayers,
    NotificationMode,
    NotificationPreset,
    Role,
    SentMessage,
    SentMessageStatus,
    Tournament,
    TournamentPlayers,
    UserRole,
    VolunteerAssignment,
    VolunteerPosition,
    VolunteerShift,
)
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.factories import make_member, make_user

OFF, SILENT, PING = NotificationMode.OFF, NotificationMode.SILENT, NotificationMode.PING
pytestmark = pytest.mark.usefixtures("db")


async def _staff():
    staff = await make_member(discord_id=9001, username='boss')
    await UserRole.create(user=staff, role=Role.STAFF, tenant_id=DEFAULT_TEST_TENANT_ID)
    return staff


async def test_only_staff_can_look():
    proctor = await make_member(discord_id=9002, username='proc')
    await UserRole.create(user=proctor, role=Role.PROCTOR, tenant_id=DEFAULT_TEST_TENANT_ID)
    player = await make_member(discord_id=9003, username='p')
    with pytest.raises(PermissionError):
        await MemberDetailsService().get(player.id, actor=proctor)


async def test_someone_outside_the_community_is_not_found():
    staff = await _staff()
    outsider = await make_user(discord_id=9004, username='outsider')
    with pytest.raises(NotFoundError):
        await MemberDetailsService().get(outsider.id, actor=staff)


async def test_effective_settings_say_who_made_them_quieter():
    staff = await _staff()
    player = await make_member(discord_id=9005, username='aerinon', email='a@example.com')
    await NotificationPreferenceService.set_preset(player, NotificationPreset.NEEDS_ME)
    await NotificationPreferenceService.set_mode(player, STAGE_CALL.key, PLAYERS, SILENT)

    details = await MemberDetailsService().get(player.id, actor=staff)
    rows = {(n.key, n.recipient): n for n in details.notices}
    ready = rows[(MATCHUP_READY.key, PLAYERS.value)]
    assert (ready.community, ready.effective, ready.reason) == (SILENT, SILENT, 'Community setting')
    stage = rows[(STAGE_CALL.key, PLAYERS.value)]
    assert (stage.community, stage.theirs, stage.effective, stage.reason) == (PING, SILENT, SILENT, 'Their choice')
    assert details.preset_label == 'Only what needs me'
    assert details.changed_from_preset == 1
    assert stage in details.quieter_than_community
    assert ready not in details.quieter_than_community
    assert all(n.section != 'Staff alerts' for n in details.notices)
    assert details.channels.email == 'unverified'


async def test_footprint_and_messages_stay_in_this_community(two_tenants):
    _, tenant_b = two_tenants
    staff = await _staff()
    player = await make_member(discord_id=9006, username='fynn')
    rival = await make_member(discord_id=9007, username='cass')
    soon = datetime.now(timezone.utc) + timedelta(hours=2)

    cup = await Tournament.create(name='ALTTPR Swiss')
    await TournamentPlayers.create(tournament=cup, user=player)
    match = await Match.create(tournament=cup, scheduled_at=soon)
    await MatchPlayers.create(match=match, user=player)
    await MatchPlayers.create(match=match, user=rival)
    desk = await VolunteerPosition.create(name='Check-in desk')
    shift = await VolunteerShift.create(position=desk, starts_at=soon, ends_at=soon + timedelta(hours=2))
    await VolunteerAssignment.create(shift=shift, user=player)
    await SentMessage.create(
        tenant_id=DEFAULT_TEST_TENANT_ID, recipient=player, discord_id='9006',
        title='Stage call', body='Stage 2', status=SentMessageStatus.DELIVERED,
    )
    with tenant_scope(tenant_b.id):
        other = await Tournament.create(name='B secret cup')
        await TournamentPlayers.create(tournament=other, user=player)
        await SentMessage.create(
            tenant_id=tenant_b.id, recipient=player, discord_id='9006',
            title='B only', body='private', status=SentMessageStatus.DELIVERED,
        )

    details = await MemberDetailsService().get(player.id, actor=staff)
    assert details.entered == ['ALTTPR Swiss']
    assert [m.opponents for m in details.upcoming_matches] == [['cass']]
    assert [s.position for s in details.upcoming_shifts or []] == ['Check-in desk']
    assert [m.title for m in details.recent_messages] == ['Stage call']
    assert details.channels.last_delivered_at is not None
