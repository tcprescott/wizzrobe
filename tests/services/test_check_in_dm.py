"""The test DM the check-in desk sends, and what it records about reaching someone."""

# ruff: noqa: F811 — the shared fixtures are imported from check_in_support, and
# pytest injects them as same-named test arguments.

import pytest

from application.services.check_in_desk_service import CheckInDeskService
from application.services.discord.discord_service import DM_REFUSED, DM_USER_NOT_FOUND
from models import CheckInDmStatus, CheckInEntrant
from tests.factories import join_community, make_user
from tests.services.check_in_support import (  # noqa: F401 — fixtures
    client,
    desk,
    event,
    service,
    staff,
)


@pytest.fixture
def discord(monkeypatch):
    """Answer each DM with ``discord.reply`` and run the queue on ``await discord.flush()``."""
    from application.services import discord as discord_pkg
    from application.services.discord import DiscordService

    class Fake:
        def __init__(self) -> None:
            self.reply = (True, 'Message sent successfully.')
            self.sent: list = []
            self.pending: list = []

        async def flush(self) -> list:
            while self.pending:
                await self.pending.pop(0)
            return self.sent

    fake = Fake()

    async def send_dm(_self, user_id, message, view_factory=None, embed=None, link=None):
        fake.sent.append({'to': user_id, 'text': message, 'link': link})
        return fake.reply

    monkeypatch.setattr(DiscordService, 'send_dm', send_dm)
    monkeypatch.setattr(discord_pkg.discord_queue, 'enqueue', fake.pending.append)
    return fake


async def _member_walk_up(service, staff, event, *, check_in=True, **user_fields):
    person = await make_user(discord_id=4242, username='visitor', **user_fields)
    await join_community(person)
    return person, await service.add_walk_up(staff, event.id, user_id=person.id, check_in=check_in)


async def _status(entrant) -> CheckInDmStatus | None:
    return (await CheckInEntrant.get(id=entrant.id)).dm_status


async def test_checking_someone_in_dms_them_and_records_that_it_landed(service, staff, event, discord):
    person, entrant = await _member_walk_up(service, staff, event)
    assert await _status(entrant) == CheckInDmStatus.PENDING

    sent = await discord.flush()

    assert [dm['to'] for dm in sent] == [person.discord_id]
    assert 'SGL 2026' in sent[0]['text']
    assert sent[0]['link'] is not None
    assert await _status(entrant) == CheckInDmStatus.SENT


async def test_the_desk_check_in_sends_it_too(service, staff, desk, event, discord):
    _, entrant = await _member_walk_up(service, staff, event, check_in=False)
    assert await _status(entrant) is None

    await service.check_in(desk, entrant.id)
    await discord.flush()

    assert await _status(entrant) == CheckInDmStatus.SENT


@pytest.mark.parametrize('reply', [DM_REFUSED, DM_USER_NOT_FOUND])
async def test_discord_refusing_is_recorded_as_blocked(service, staff, event, discord, reply):
    discord.reply = (False, reply)
    _, entrant = await _member_walk_up(service, staff, event)

    await discord.flush()

    assert await _status(entrant) == CheckInDmStatus.BLOCKED


async def test_our_own_failure_is_not_blamed_on_them(service, staff, event, discord):
    discord.reply = (False, 'Discord bot is not connected. Please try again in a moment.')
    _, entrant = await _member_walk_up(service, staff, event)

    await discord.flush()

    assert await _status(entrant) == CheckInDmStatus.FAILED


async def test_dms_turned_off_in_wizzrobe_are_recorded_without_sending(service, staff, event, discord):
    _, entrant = await _member_walk_up(service, staff, event, dm_notifications=False)

    assert await discord.flush() == []
    assert await _status(entrant) == CheckInDmStatus.OPTED_OUT


async def test_no_account_means_no_dm(service, staff, event, discord):
    entrant = await service.add_walk_up(staff, event.id, name='Walk-in Wendy')

    assert await discord.flush() == []
    assert await _status(entrant) is None


async def test_the_desk_can_send_it_again_once_they_open_their_dms(service, staff, desk, event, discord):
    discord.reply = (False, DM_REFUSED)
    _, entrant = await _member_walk_up(service, staff, event)
    await discord.flush()

    discord.reply = (True, 'Message sent successfully.')
    await CheckInDeskService().send_test_dm(desk, entrant.id)
    assert await _status(entrant) == CheckInDmStatus.PENDING
    await discord.flush()

    assert await _status(entrant) == CheckInDmStatus.SENT


async def test_a_resend_needs_a_checked_in_linked_row_and_a_desk_role(service, staff, event, discord):
    _, waiting = await _member_walk_up(service, staff, event, check_in=False)
    named = await service.add_walk_up(staff, event.id, name='Walk-in Wendy')
    member = await make_user(discord_id=77, username='member')
    await join_community(member)

    with pytest.raises(ValueError, match='first'):
        await CheckInDeskService().send_test_dm(staff, waiting.id)
    with pytest.raises(ValueError, match='no one to DM'):
        await CheckInDeskService().send_test_dm(staff, named.id)
    with pytest.raises(PermissionError):
        await CheckInDeskService().send_test_dm(member, named.id)
    assert await discord.flush() == []


async def test_a_desk_volunteer_cannot_resend_for_a_closed_event(service, staff, desk, event, discord):
    from application.errors import NotFoundError
    from models import CheckInEventStatus

    _, entrant = await _member_walk_up(service, staff, event)
    await discord.flush()
    event.status = CheckInEventStatus.CLOSED
    await event.save(update_fields=['status'])

    with pytest.raises(NotFoundError):
        await CheckInDeskService().send_test_dm(desk, entrant.id)
    await CheckInDeskService().send_test_dm(staff, entrant.id)
