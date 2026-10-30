"""The Sent messages log never crosses communities.

A DM's text can carry a staff-private note, so one community's staff must never
read, count or update another community's rows.
"""

from application.repositories import SentMessageRepository
from application.tenant_context import tenant_scope
from models import SentMessage, SentMessageStatus
from tests.factories import make_user


async def test_sent_messages_do_not_leak_across_tenants(two_tenants):
    a, b = two_tenants
    person = await make_user(discord_id=761, username='in-both')
    with tenant_scope(a.id):
        mine = await SentMessageRepository.create(
            recipient_id=person.id, discord_id='761', title='A only', body='secret A note')
    with tenant_scope(b.id):
        await SentMessageRepository.create(
            recipient_id=person.id, discord_id='761', title='B only', body='B note')
        assert [m.title for m in await SentMessageRepository.list(None, None)] == ['B only']
        assert await SentMessageRepository.count(None, None, search='secret') == 0
        assert await SentMessageRepository.get(mine.id) is None
        assert sum((await SentMessageRepository.totals(None, None)).values()) == 1
        assert await SentMessageRepository.update(mine.id, b.id, status=SentMessageStatus.FAILED) == 0

    assert (await SentMessage.get(id=mine.id)).status == SentMessageStatus.PENDING
