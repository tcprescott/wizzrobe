"""Events inside one community stay apart: SGL 2026's lists never show SGL 2027's rows.

The tenant leak tests keep communities apart. These keep a community's events
apart, the same way: rows in two events, a read in each, and each sees its own.
Every event-owned model must be named here (see the last test).
"""

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from tortoise.exceptions import IntegrityError

from application.event_context import event_scope
from application.repositories import (
    AccommodationRepository,
    AsyncQualifierRepository,
    CheckInEventRepository,
    CommunityEventRepository,
    DiscordRoleSyncRepository,
    EmergencyContactRepository,
    MatchRepository,
    RoomTokenRepository,
    StageRepository,
    StationRepository,
    StreamLayoutRepository,
    TournamentRepository,
    VolunteerPositionRepository,
    VolunteerShiftRepository,
)
from models import (
    AccommodationRequest,
    AsyncQualifier,
    CheckInEvent,
    CommunityEvent,
    CommunityEventStatus,
    DiscordRoleRule,
    DiscordRoleRuleKind,
    EmergencyContact,
    Event,
    EventStatus,
    Match,
    MatchPlayers,
    RoomToken,
    Stage,
    StageOverlay,
    Station,
    StreamLayout,
    Tournament,
    VolunteerPosition,
    VolunteerShift,
)
from tests.conftest import DEFAULT_TEST_EVENT_ID, _event_owned_models
from tests.factories import make_member

NOW = datetime(2026, 10, 23, 18, tzinfo=timezone.utc)


@pytest.fixture
async def two_events(db):
    """The default event (SGL 2026) and a second live one (SGL 2027), both in tenant 1."""
    other = await Event.create(tenant_id=1, name='SGL 2027', slug='sgl27', status=EventStatus.LIVE, is_default=False)
    return DEFAULT_TEST_EVENT_ID, other.id


async def _in_each(two_events, make):
    """Run ``make(label)`` once in each event; return the two rows."""
    a, b = two_events
    with event_scope(a):
        row_a = await make('A')
    with event_scope(b):
        row_b = await make('B')
    return row_a, row_b


def _ids(rows) -> list[int]:
    return sorted(r.id for r in rows)


@pytest.mark.parametrize('model, make, read', [
    (Tournament, lambda n: Tournament.create(name=f'Cup {n}'), lambda: TournamentRepository.get_all()),
    (Stage, lambda n: Stage.create(name=f'Stage {n}'), lambda: StageRepository.get_all()),
    (Station, lambda n: Station.create(name=f'Station {n}'), lambda: StationRepository.get_all()),
    (VolunteerPosition, lambda n: VolunteerPosition.create(name=f'Desk {n}'), lambda: VolunteerPositionRepository.list_all()),
    (AsyncQualifier, lambda n: AsyncQualifier.create(name=f'Q {n}'), lambda: AsyncQualifierRepository().list_all()),
    (CheckInEvent, lambda n: CheckInEvent.create(name=f'Check-in {n}'), lambda: CheckInEventRepository.list_all()),
    (StreamLayout, lambda n: StreamLayout.create(name=f'Layout {n}', boxes=[]), lambda: StreamLayoutRepository.list_for_event()),
])
async def test_a_list_shows_only_its_own_events_rows(two_events, model, make, read):
    row_a, row_b = await _in_each(two_events, make)
    a, b = two_events

    with event_scope(a):
        assert _ids(await read()) == [row_a.id]
    with event_scope(b):
        assert _ids(await read()) == [row_b.id]


async def test_room_tokens_list_per_event(two_events):
    staff = await make_member(discord_id=7001)
    token_a, token_b = await _in_each(
        two_events, lambda n: RoomToken.create(label=f'Desk {n}', token_hash=f'hash-{n}', created_by=staff),
    )
    a, b = two_events
    with event_scope(a):
        assert _ids(await RoomTokenRepository.list_all()) == [token_a.id]
    with event_scope(b):
        assert _ids(await RoomTokenRepository.list_all()) == [token_b.id]


async def test_meetups_list_per_event(two_events):
    host = await make_member(discord_id=7002)

    def make(n):
        return CommunityEvent.create(
            title=f'Meetup {n}', starts_at=NOW, duration_minutes=60, host=host, status=CommunityEventStatus.APPROVED,
        )

    meetup_a, meetup_b = await _in_each(two_events, make)
    a, b = two_events
    with event_scope(a):
        assert _ids(await CommunityEventRepository.list_by_status([CommunityEventStatus.APPROVED])) == [meetup_a.id]
    with event_scope(b):
        assert _ids(await CommunityEventRepository.list_by_status([CommunityEventStatus.APPROVED])) == [meetup_b.id]


async def test_surveys_list_per_event(two_events):
    from application.repositories.survey_repository import SurveyRepository
    from models import Survey

    survey_a, survey_b = await _in_each(two_events, lambda n: Survey.create(title=f'Survey {n}'))
    a, b = two_events
    with event_scope(a):
        assert _ids(await SurveyRepository().list_for_event()) == [survey_a.id]
    with event_scope(b):
        assert _ids(await SurveyRepository().list_for_event()) == [survey_b.id]


async def test_role_rules_list_per_event_and_a_sync_reads_every_live_one(two_events):
    def make(n):
        return DiscordRoleRule.create(
            key='any_tournament', kind=DiscordRoleRuleKind.ANY_TOURNAMENT,
            discord_role_id=100 + ord(n), discord_role_name=f'Player {n}',
        )

    rule_a, rule_b = await _in_each(two_events, make)
    a, b = two_events
    with event_scope(a):
        assert _ids(await DiscordRoleSyncRepository.list_rules()) == [rule_a.id]
        assert _ids(await DiscordRoleSyncRepository.list_live_rules()) == sorted([rule_a.id, rule_b.id])

    await Event.filter(id=b).update(status=EventStatus.ARCHIVED)
    with event_scope(a):
        assert _ids(await DiscordRoleSyncRepository.list_live_rules()) == [rule_a.id]


async def test_matches_and_shifts_take_their_parents_event_and_list_per_event(two_events):
    a, b = two_events
    with event_scope(a):
        cup = await Tournament.create(name='Cup A')
        desk = await VolunteerPosition.create(name='Desk A')
    with event_scope(b):
        match = await Match.create(tournament=cup, scheduled_at=NOW)
        shift = await VolunteerShift.create(position=desk, starts_at=NOW, ends_at=NOW + timedelta(hours=2))
        assert await MatchRepository.get_all() == []
        assert await VolunteerShiftRepository.list_for_window(NOW - timedelta(hours=1), NOW + timedelta(hours=3)) == []

    assert (match.event_id, shift.event_id) == (a, a)
    with event_scope(a):
        assert _ids(await MatchRepository.get_all()) == [match.id]
        assert _ids(await VolunteerShiftRepository.list_for_window(
            NOW - timedelta(hours=1), NOW + timedelta(hours=3),
        )) == [shift.id]


async def test_a_players_own_matches_follow_them_across_live_events_only(two_events):
    a, b = two_events
    player = await make_member(discord_id=7003)
    archived = await Event.create(tenant_id=1, name='SGL 2025', slug='sgl25', status=EventStatus.LIVE, is_default=False)
    matches = {}
    for event_id in (a, b, archived.id):
        with event_scope(event_id):
            cup = await Tournament.create(name=f'Cup {event_id}')
            matches[event_id] = await Match.create(tournament=cup, scheduled_at=NOW + timedelta(days=1))
            await MatchPlayers.create(match=matches[event_id], user=player)
    archived.status = EventStatus.ARCHIVED
    await archived.save()

    with event_scope(a):
        assert _ids(await MatchRepository.get_upcoming_for_user(player.id)) == sorted([matches[a].id, matches[b].id])


async def test_names_are_unique_per_event_not_per_community(two_events):
    a, b = two_events
    with event_scope(a):
        await Stage.create(name='Main Stage')
        await VolunteerPosition.create(name='Runner')
    with event_scope(b):
        await Stage.create(name='Main Stage')
        await VolunteerPosition.create(name='Runner')
        assert await StageRepository.name_taken('Main Stage')
    with event_scope(a), pytest.raises(IntegrityError):
        await Stage.create(name='Main Stage')


async def test_a_persons_private_rows_are_per_event(two_events):
    """An ADA request or emergency contact given for one event isn't another's."""
    a, b = two_events
    person = await make_member(discord_id=7004)
    with event_scope(a):
        request = await AccommodationRequest.create(user=person, details='Seat near the stage')
        contact = await EmergencyContact.create(user=person, name='Sam', phone='555-0100')
        assert (await AccommodationRepository.get_for_user(person)).id == request.id
        assert (await EmergencyContactRepository.get_for_user(person.id)).id == contact.id
    with event_scope(b):
        assert await AccommodationRepository.get_for_user(person) is None
        assert await EmergencyContactRepository.get_for_user(person.id) is None


async def test_stage_overlays_take_their_stages_event_and_list_per_event(two_events):
    a, b = two_events
    with event_scope(a):
        stage = await Stage.create(name='Main Stage')
    with event_scope(b):
        overlay = await StreamLayoutRepository.create_overlay(stage, 'ovl_a')
        assert await StreamLayoutRepository.overlays() == []
        assert await StreamLayoutRepository.stages() == []

    assert isinstance(overlay, StageOverlay) and overlay.event_id == a
    with event_scope(a):
        assert _ids(await StreamLayoutRepository.overlays()) == [overlay.id]
        assert _ids(await StreamLayoutRepository.stages()) == [stage.id]


async def test_role_grants_and_discord_mappings_count_in_their_own_event(two_events):
    """UserRole and DiscordRoleMapping: a per-event grant counts only in its event
    (tests/services/test_per_event_roles.py covers the rest)."""
    from application.services import AuthService
    from models import DiscordRoleMapping, Role, UserRole

    a, b = two_events
    person = await make_member(discord_id=7095)
    await UserRole.create(user=person, role=Role.PROCTOR, tenant_id=1, event_id=b)
    await DiscordRoleMapping.create(
        tenant_id=1, guild_id=42, discord_role_id=111, discord_role_name='Crew', app_role=Role.VOLUNTEER, event_id=b,
    )
    with event_scope(a):
        assert not await AuthService.has_role(person, Role.PROCTOR)
    with event_scope(b):
        assert await AuthService.has_role(person, Role.PROCTOR)
    assert (await DiscordRoleMapping.get(discord_role_id=111)).event_id == b


# Shrink this, never grow it: a new event-owned model gets a test above.
_BACKLOG = {
    'VolunteerRoleMapping': 'read through its position, which is tested',
    'VolunteerAvailability': 'per-person reads; needs a two-event test',
    'PlayerAvailability': 'per-person reads; needs a two-event test',
    'CommunityEventSeen': 'per-person lookup; needs a two-event test',
    'DiscordScheduledEvent': 'looked up by source row only',
    'WatchLayoutVote': 'no reader left in the app',
}


def test_every_event_owned_model_has_an_event_isolation_test():
    """A new event-owned model is named in a test above, or in the backlog with a reason."""
    text = Path(__file__).read_text().split('_BACKLOG = {')[0]
    missing = [
        m.__name__ for m in _event_owned_models()
        if m.__name__ not in _BACKLOG and not re.search(rf'(?<!\w){m.__name__}(?!\w)', text)
    ]
    assert missing == [], f'event-owned models with no event isolation test: {missing}'


def test_the_backlog_only_shrinks():
    text = Path(__file__).read_text().split('_BACKLOG = {')[0]
    owned = {m.__name__ for m in _event_owned_models()}
    stale = [name for name in _BACKLOG if name not in owned or re.search(rf'(?<!\w){name}(?!\w)', text)]
    assert stale == [], f'remove from _BACKLOG (now tested, or no longer event-owned): {stale}'


async def test_the_reschedule_queue_on_a_board_is_its_events(two_events):
    from application.services import MatchRescheduleService
    from models import MatchRescheduleRequest, RescheduleRequestKind

    player = await make_member(discord_id=7090)

    async def ask(n):
        cup = await Tournament.create(name=f'Cup {n}')
        match = await Match.create(tournament=cup, scheduled_at=NOW)
        return await MatchRescheduleRequest.create(
            match=match, requested_by=player, kind=RescheduleRequestKind.CANCEL, reason='Sick',
        )

    req_a, req_b = await _in_each(two_events, ask)
    a, b = two_events
    service = MatchRescheduleService()
    assert _ids(await service.list_pending(event_id=a)) == [req_a.id]
    assert _ids(await service.list_pending(event_id=b)) == [req_b.id]
    assert _ids(await service.list_pending()) == sorted([req_a.id, req_b.id])
