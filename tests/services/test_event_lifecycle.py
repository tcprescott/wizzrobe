"""EventService lifecycle: create, go live, make default, archive, unarchive,
delete a draft, and "Copy setup from" on create."""

from datetime import date

import pytest

from application.event_context import event_scope
from application.services.audit_service import AuditActions
from application.services.event_service import EventService, suggest_slug
from models import (
    AuditLog,
    Event,
    EventStatus,
    Match,
    Role,
    Stage,
    Station,
    Tournament,
    TournamentPlayers,
    User,
    UserRole,
    VolunteerPosition,
)


@pytest.fixture
async def staff(db) -> User:
    user = await User.create(discord_id=7100, username='staff')
    await UserRole.create(user=user, role=Role.STAFF)
    return user


@pytest.fixture
async def player(db) -> User:
    return await User.create(discord_id=7200, username='player')


@pytest.fixture
async def default_event(db) -> Event:
    return await Event.get(tenant_id=1, is_default=True)


def test_suggest_slug_lowercases_and_hyphenates():
    assert suggest_slug('SG Live 2027!') == 'sg-live-2027'


async def test_staff_create_a_draft_that_is_not_the_default(staff):
    event, copied = await EventService.create_event(
        staff, name='SG Live 2027', starts_on=date(2027, 10, 21), ends_on=date(2027, 10, 24),
    )
    assert (event.status, event.is_default, event.slug) == (EventStatus.DRAFT, False, 'sg-live-2027')
    assert copied == {}
    assert await AuditLog.filter(action=AuditActions.EVENT_CREATED).exists()


async def test_a_player_cannot_create_an_event(player):
    with pytest.raises(PermissionError):
        await EventService.create_event(player, name='Mine')


@pytest.mark.parametrize('kwargs, message', [
    ({'name': '  '}, 'name'),
    ({'name': 'X', 'slug': 'Bad Slug'}, 'link name'),
    ({'name': 'X', 'slug': 'admin'}, 'reserved'),
    ({'name': 'X', 'starts_on': date(2027, 1, 2), 'ends_on': date(2027, 1, 1)}, 'end'),
])
async def test_create_refuses_bad_input(staff, kwargs, message):
    with pytest.raises(ValueError, match=message):
        await EventService.create_event(staff, **kwargs)


async def test_a_taken_name_gets_a_numbered_slug(staff):
    first, _ = await EventService.create_event(staff, name='Winter')
    second, _ = await EventService.create_event(staff, name='Winter')
    assert (first.slug, second.slug) == ('winter', 'winter-2')


async def test_a_draft_goes_live_then_becomes_the_default(staff, default_event):
    event, _ = await EventService.create_event(staff, name='Next')
    with pytest.raises(ValueError, match='live'):
        await EventService.make_default(staff, event.id)
    await EventService.go_live(staff, event.id)
    await EventService.make_default(staff, event.id)
    assert await EventService.default_event_id(1) == event.id
    await default_event.refresh_from_db()
    assert default_event.is_default is False


async def test_a_live_events_slug_is_fixed(staff, default_event):
    with pytest.raises(ValueError, match="can't change"):
        await EventService.update_event(staff, default_event.id, slug='renamed')
    renamed = await EventService.update_event(staff, default_event.id, name='Renamed')
    assert renamed.name == 'Renamed'


async def test_archiving_the_default_needs_a_new_default_when_another_is_live(staff, default_event):
    other, _ = await EventService.create_event(staff, name='Other')
    await EventService.go_live(staff, other.id)
    with pytest.raises(ValueError, match='default'):
        await EventService.archive(staff, default_event.id)
    await EventService.archive(staff, default_event.id, new_default_id=other.id)
    await default_event.refresh_from_db()
    assert default_event.status == EventStatus.ARCHIVED and default_event.is_default is False
    assert await EventService.default_event_id(1) == other.id


async def test_the_only_live_event_stays_the_default_when_archived(staff, default_event):
    await EventService.archive(staff, default_event.id)
    await default_event.refresh_from_db()
    assert default_event.is_default is True
    await EventService.unarchive(staff, default_event.id)
    await default_event.refresh_from_db()
    assert default_event.status == EventStatus.LIVE and default_event.archived_at is None


async def test_archive_check_counts_what_is_still_open(staff, default_event):
    tournament = await Tournament.create(name='Cup', event=default_event)
    await Match.create(tournament=tournament, event=default_event)
    check = await EventService.archive_check(default_event.id)
    assert check.open_matches == 1 and check.anything_open


async def test_only_a_draft_without_matches_can_be_deleted(staff, default_event):
    with pytest.raises(ValueError, match='draft'):
        await EventService.delete_draft(staff, default_event.id)
    draft, _ = await EventService.create_event(staff, name='Scratch')
    await EventService.delete_draft(staff, draft.id)
    assert not await Event.filter(id=draft.id).exists()


async def test_copy_setup_brings_setup_and_leaves_people_and_last_years_links(staff, player, default_event):
    with event_scope(default_event.id):
        cup = await Tournament.create(
            name='Cup', event=default_event, challonge_tournament_id='abc', bracket_url='https://x',
            signups_close_at=None,
        )
        await TournamentPlayers.create(tournament=cup, user=player)
        await Stage.create(name='Main', event=default_event)
        await Station.create(name='1', event=default_event)
        await VolunteerPosition.create(name='Proctor', event=default_event)
    default_event.config = {'station_format': 'numbered', 'volunteer_comp_tiers': '4, 8'}
    await default_event.save()

    event, copied = await EventService.create_event(
        staff, name='Copy', copy_from=default_event.id, copy=frozenset({'tournaments', 'stages'}),
    )
    assert copied['tournaments'] == 1 and copied['stages'] == 1 and copied['stations'] == 1
    new_cup = await Tournament.get(event_id=event.id)
    assert new_cup.name == 'Cup'
    assert new_cup.challonge_tournament_id is None and new_cup.bracket_url is None
    assert not await TournamentPlayers.filter(tournament=new_cup).exists()
    assert not await VolunteerPosition.filter(event_id=event.id).exists()
    await event.refresh_from_db()
    assert event.config == {'station_format': 'numbered'}
    assert await AuditLog.filter(action=AuditActions.EVENT_SETUP_COPIED).exists()


async def test_copy_refuses_an_unknown_group(staff, default_event):
    with pytest.raises(ValueError, match='Unknown'):
        await EventService.create_event(staff, name='X', copy_from=default_event.id, copy=frozenset({'players'}))
