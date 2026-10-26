"""Broadcasters: the third crew role, live only on an event that turns it on."""

import pytest

from application.services import CrewService
from application.services import event_service as event_service_module
from application.services.event_service import NO_BROADCASTERS, EventService
from application.services.match.match_display_service import MatchDisplayService
from application.services.system_config_service import KEY_BROADCASTERS
from models import Broadcaster, Match, Role, Stage, Tournament, UserRole
from tests.factories import make_member, set_setting

pytestmark = pytest.mark.usefixtures("db")


async def _turn_on() -> None:
    await set_setting(KEY_BROADCASTERS, 'true')
    event_service_module._clear_cache()


async def _match(**tournament) -> Match:
    t = await Tournament.create(name='Cup', **tournament)
    stage = await Stage.create(name='Stage 1')
    return await Match.create(tournament=t, stage=stage)


async def test_signup_is_refused_on_an_event_without_broadcasters():
    match = await _match()
    user = await make_member(discord_id=9101, username='caster')
    with pytest.raises(ValueError, match=NO_BROADCASTERS.split('.')[0]):
        await CrewService().signup_crew(match.id, user, 'broadcaster')
    assert not await Broadcaster.filter(match=match).exists()


async def test_signup_and_approval_once_the_event_has_them():
    await _turn_on()
    match = await _match()
    user = await make_member(discord_id=9102, username='caster')
    staff = await make_member(discord_id=9103, username='staff')
    await UserRole.create(user=staff, role=Role.STAFF, tenant_id=1)
    await CrewService().signup_crew(match.id, user, 'broadcaster')
    row = await Broadcaster.get(match=match, user=user)
    assert not row.approved
    await CrewService().update_crew_approval(row, 'broadcaster', True, staff)
    assert (await Broadcaster.get(id=row.id)).approved


async def test_a_tournament_that_needs_none_refuses_them():
    await _turn_on()
    match = await _match(required_broadcasters=0)
    user = await make_member(discord_id=9104, username='caster')
    with pytest.raises(ValueError):
        await CrewService().signup_crew(match.id, user, 'broadcaster')


async def test_the_board_row_carries_broadcasters_only_where_the_event_has_them():
    match = await _match()
    rows = await MatchDisplayService().get_matches_for_display()
    row = next(r for r in rows if r['id'] == match.id)
    assert row['broadcasters_on'] is False
    assert row['crew_required']['broadcasters'] == 0

    await _turn_on()
    rows = await MatchDisplayService().get_matches_for_display()
    row = next(r for r in rows if r['id'] == match.id)
    assert row['broadcasters_on'] is True
    assert row['crew_required']['broadcasters'] == 1
    assert await EventService.match_wants_broadcaster(match)
