"""A DM button runs in the event of the row it's about, not the default event."""

from types import SimpleNamespace

import sentry_sdk

from application.event_context import get_current_event_id
from discordbot._ack_common import _run_parsed
from discordbot._tenant import RowScope, assignment_scope, match_scope
from models import Event, EventStatus, Match, Tournament
from tests.factories import make_member


async def _second_event() -> Event:
    return await Event.create(tenant_id=1, name='SGL 2027', slug='sgl27', status=EventStatus.LIVE, is_default=False)


async def test_a_match_resolves_to_its_own_event(db):
    event = await _second_event()
    tournament = await Tournament.create(name='A Cup', event_id=event.id)
    match = await Match.create(tournament=tournament)

    assert await match_scope(match.id) == RowScope(1, event.id)
    assert await match_scope(match.id + 1000) is None
    assert await assignment_scope(12345) is None


async def test_the_handler_runs_inside_the_rows_event(db):
    event = await _second_event()
    user = await make_member(discord_id=4242)
    seen: list = []

    async def handle(interaction, parsed, account, send) -> None:
        seen.append(get_current_event_id())

    async def send(message: str) -> None:
        pass

    async def resolve(_parsed):
        return RowScope(1, event.id)

    with sentry_sdk.isolation_scope():
        await _run_parsed(
            SimpleNamespace(user=SimpleNamespace(id=int(user.discord_id))), 'x:1',
            send=send, log_label='test', parse=lambda cid: cid,
            resolve_tenant=resolve, not_found_message='gone',
            handle=handle, unexpected_error_message='boom',
        )

    assert seen == [event.id]
