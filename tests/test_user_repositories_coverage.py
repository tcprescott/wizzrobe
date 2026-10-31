"""Direct unit tests for the identity repositories: users and their roles.

Split from ``test_repositories_coverage.py`` when that module approached the
800-line guideline. These two are the pair that stands apart from the rest:
``User`` is the one global model — no tenant column — and ``UserRole`` is how a
global identity holds per-tenant authority, so their queries are the ones where
scoping behaves differently from every other repository in the sibling module.

Repositories perform no Discord I/O, so no queue stub is needed.
"""

import pytest

from application.repositories.user_repository import UserRepository
from application.repositories.user_role_repository import UserRoleRepository
from models import Role, RoleSource, User, UserRole
from tests.factories import make_user


class TestUserRepository:
    async def test_get_by_id_and_discord_id(self, db):
        u = await make_user(1, "alice")
        assert (await UserRepository.get_by_id(u.id)).id == u.id
        assert (await UserRepository.get_by_discord_id(1)).id == u.id
        assert await UserRepository.get_by_id(9999) is None
        assert await UserRepository.get_by_discord_id(424242) is None

    async def test_get_all_orders_by_username(self, db):
        await make_user(1, "charlie")
        await make_user(2, "alice")
        await make_user(3, "bob")
        users = await UserRepository.get_all()
        assert [u.username for u in users] == ["alice", "bob", "charlie"]

    async def test_get_all_filters_by_role_distinct(self, db):
        u1 = await make_user(1, "alice")
        u2 = await make_user(2, "bob")
        await UserRole.create(user=u1, role=Role.STAFF)
        await UserRole.create(user=u1, role=Role.PROCTOR)
        await UserRole.create(user=u2, role=Role.PROCTOR)

        staff = await UserRepository.get_all(role=Role.STAFF)
        assert [u.id for u in staff] == [u1.id]

        proctors = await UserRepository.get_all(role=Role.PROCTOR)
        assert {u.id for u in proctors} == {u1.id, u2.id}

    async def test_get_all_has_discord_returns_users(self, db):
        u = await make_user(1, "alice")
        users = await UserRepository.get_all(has_discord=True)
        assert [x.id for x in users] == [u.id]

    async def test_search_by_name_returns_matches(self, db):
        await make_user(1, "alice", display_name="Alice Wonder")
        results = await UserRepository.search_by_name("ali")
        assert any(u.username == "alice" for u in results)

    async def test_search_by_name_matches_display_name(self, db):
        await make_user(1, "xyz", display_name="Alice Wonder")
        results = await UserRepository.search_by_name("wonder")
        assert any(u.username == "xyz" for u in results)

    async def test_create_sets_fields(self, db):
        u = await UserRepository.create(
            username="dave", discord_id=77, display_name="Dave", pronouns="he/him", is_active=False
        )
        assert u.username == "dave"
        assert u.discord_id == 77
        assert u.display_name == "Dave"
        assert u.pronouns == "he/him"
        assert u.is_active is False

    async def test_get_or_create_by_discord_id(self, db):
        user, created = await UserRepository.get_or_create_by_discord_id(555, "newbie")
        assert created is True
        assert user.discord_id == 555
        again, created2 = await UserRepository.get_or_create_by_discord_id(555, "ignored")
        assert created2 is False
        assert again.id == user.id
        assert again.username == "newbie"

    async def test_update_sets_fields_and_persists(self, db):
        u = await make_user(1, "alice")
        await UserRepository.update(u, username="alice2", is_active=False)
        refreshed = await User.get(id=u.id)
        assert refreshed.username == "alice2"
        assert refreshed.is_active is False

    async def test_delete_removes_row(self, db):
        u = await make_user(1, "alice")
        await UserRepository.delete(u)
        assert await User.get_or_none(id=u.id) is None

    async def test_update_discord_info_persists_username(self, db):
        u = await make_user(1, "alice")
        await UserRepository.update_discord_info(u, username="alice_dc")
        assert (await User.get(id=u.id)).username == "alice_dc"

    async def test_update_discord_info_persists_avatar_hash(self, db):
        u = await make_user(1, "alice")
        await UserRepository.update_discord_info(u, username="alice_dc", avatar="abc")
        assert (await User.get(id=u.id)).discord_avatar == "abc"

    async def test_update_discord_info_leaves_avatar_alone_when_not_given(self, db):
        u = await make_user(1, "alice")
        await UserRepository.set_discord_avatar(u, "abc")
        await UserRepository.update_discord_info(u, username="alice_dc")
        assert (await User.get(id=u.id)).discord_avatar == "abc"

    async def test_update_discord_info_takes_no_non_field_arguments(self, db):
        u = await make_user(1, "alice")
        with pytest.raises(TypeError):
            await UserRepository.update_discord_info(u, username="alice_dc", discriminator="0001")

    async def test_set_discord_avatar_reports_whether_it_changed(self, db):
        u = await make_user(1, "alice")
        assert await UserRepository.set_discord_avatar(u, "abc") is True
        assert await UserRepository.set_discord_avatar(u, "abc") is False
        assert await UserRepository.set_discord_avatar(u, None) is True
        assert (await User.get(id=u.id)).discord_avatar is None


class TestUserRoleRepository:
    async def test_add_creates_new_role(self, db):
        u = await make_user(1, "alice")
        granter = await make_user(2, "granter")
        ur = await UserRoleRepository.add(u, Role.STAFF, granted_by=granter, source=RoleSource.MANUAL)
        assert ur.role == Role.STAFF
        assert ur.source == RoleSource.MANUAL
        assert (await ur.granted_by).id == granter.id

    async def test_add_defaults_to_manual_source(self, db):
        u = await make_user(1, "alice")
        ur = await UserRoleRepository.add(u, Role.PROCTOR)
        assert ur.source == RoleSource.MANUAL

    async def test_manual_grant_pins_a_discord_role(self, db):
        u = await make_user(1, "alice")
        granter = await make_user(2, "granter")
        await UserRole.create(user=u, role=Role.STAFF, source=RoleSource.DISCORD)
        ur = await UserRoleRepository.add(u, Role.STAFF, granted_by=granter, source=RoleSource.MANUAL)
        assert ur.source == RoleSource.MANUAL
        assert (await ur.granted_by).id == granter.id
        refreshed = await UserRole.get(id=ur.id)
        assert refreshed.source == RoleSource.MANUAL

    async def test_add_existing_discord_role_again_does_not_pin(self, db):
        u = await make_user(1, "alice")
        await UserRole.create(user=u, role=Role.STAFF, source=RoleSource.DISCORD)
        ur = await UserRoleRepository.add(u, Role.STAFF, source=RoleSource.DISCORD)
        assert ur.source == RoleSource.DISCORD

    async def test_remove_returns_deleted_count(self, db):
        u = await make_user(1, "alice")
        await UserRole.create(user=u, role=Role.STAFF)
        deleted = await UserRoleRepository.remove(u, Role.STAFF)
        assert deleted == 1
        assert await UserRoleRepository.remove(u, Role.STAFF) == 0

    async def test_list_for_user(self, db):
        u = await make_user(1, "alice")
        other = await make_user(2, "bob")
        await UserRole.create(user=u, role=Role.STAFF)
        await UserRole.create(user=u, role=Role.PROCTOR)
        await UserRole.create(user=other, role=Role.VOLUNTEER)
        rows = await UserRoleRepository.list_for_user(u)
        assert {r.role for r in rows} == {Role.STAFF, Role.PROCTOR}

    async def test_list_for_user_by_source(self, db):
        u = await make_user(1, "alice")
        await UserRole.create(user=u, role=Role.STAFF, source=RoleSource.MANUAL)
        await UserRole.create(user=u, role=Role.PROCTOR, source=RoleSource.DISCORD)
        manual = await UserRoleRepository.list_for_user_by_source(u, RoleSource.MANUAL)
        assert {r.role for r in manual} == {Role.STAFF}
        discord = await UserRoleRepository.list_for_user_by_source(u, RoleSource.DISCORD)
        assert {r.role for r in discord} == {Role.PROCTOR}

    async def test_list_users_with_role(self, db):
        u1 = await make_user(1, "alice")
        u2 = await make_user(2, "bob")
        await make_user(3, "carol")
        await UserRole.create(user=u1, role=Role.STAFF)
        await UserRole.create(user=u2, role=Role.STAFF)
        users = await UserRoleRepository.list_users_with_role(Role.STAFF)
        assert {u.id for u in users} == {u1.id, u2.id}
        assert await UserRoleRepository.list_users_with_role(Role.EQUIPMENT_MANAGER) == []
        u2.is_active = False
        await u2.save(update_fields=['is_active'])
        active = await UserRoleRepository.list_users_with_role(Role.STAFF, active_only=True)
        assert {u.id for u in active} == {u1.id}
