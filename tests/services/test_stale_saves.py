"""A write through a row loaded earlier keeps what changed on it since.

Pages hold the ``user`` they built with, and a whole-row save from that copy
put back any column someone else had written in the meantime.
"""

from datetime import datetime, timezone

import pytest

from application.repositories._save import save_fields
from application.repositories.tenant_join_request_repository import TenantJoinRequestRepository
from application.repositories.user_repository import UserRepository
from application.services.challonge_service import ChallongeService
from application.tenant_context import require_tenant_id
from models import JoinRequestStatus, TenantJoinRequest, User
from tests.factories import make_member, make_user

pytestmark = pytest.mark.usefixtures("bypass_auth")


async def test_save_fields_writes_only_the_named_columns_and_bumps_updated_at(db):
    held = await make_user(1, display_name='Alice')
    before = held.updated_at
    await User.filter(id=held.id).update(timezone='Europe/London')

    held.display_name = 'Ally'
    await save_fields(held, 'display_name')

    saved = await User.get(id=held.id)
    assert saved.display_name == 'Ally'
    assert saved.timezone == 'Europe/London'
    assert saved.updated_at > before


async def test_save_fields_maps_a_foreign_key_to_its_id_column(db):
    actor = await make_member(1, 'staff')
    asker = await make_user(2)
    held = await TenantJoinRequestRepository.upsert_pending(asker, require_tenant_id(), 'hi')
    await TenantJoinRequest.filter(id=held.id).update(message='edited')

    await TenantJoinRequestRepository.decide(
        held, JoinRequestStatus.APPROVED, actor, datetime.now(timezone.utc),
    )

    saved = await TenantJoinRequest.get(id=held.id)
    assert saved.decided_by_id == actor.id
    assert saved.message == 'edited'


async def test_unlinking_challonge_from_a_held_user_keeps_their_new_timezone(db):
    actor = await make_member(1, 'staff')
    held = await make_member(
        2, 'player', challonge_user_id='77', challonge_username='p',
        challonge_linked_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    await UserRepository.set_timezone(await User.get(id=held.id), 'America/Chicago')

    await ChallongeService().unlink_player(held, actor)

    saved = await User.get(id=held.id)
    assert saved.challonge_user_id is None
    assert saved.timezone == 'America/Chicago'
