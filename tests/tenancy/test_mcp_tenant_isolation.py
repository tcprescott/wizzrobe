"""Tenant isolation for the MCP surface.

The REST API gets most of this for free: its tenant is derived from the token
and bound by a router-level dependency that resets in a ``finally``. MCP has no
such dependency — one platform-wide token can name a different community on
every call — so the binding is done per call in ``mcpserver/registry.py`` and
has to be proved here rather than assumed.

Two families of assertion:

* **Scoping** — a call naming community A never returns community B's rows.
* **Hygiene** — the binding is released after every call, and interleaved calls
  on one connection do not inherit each other's community. This is the part
  that would silently pass with a broken implementation if it were only ever
  exercised one call at a time.
"""

from application.services.auth_service import AuthService  # noqa: F401  (documents the gate under test)
from application.tenant_context import get_current_tenant_id, tenant_scope
from models import Match, Role, Tournament, User, UserRole
from tests.mcp.conftest import call_tool, create_oauth_token, mcp_session


async def _seed(tenant, label: str):
    """A tournament and a match inside one community."""
    with tenant_scope(tenant.id):
        tournament = await Tournament.create(name=f'{label} Cup')
        match = await Match.create(tournament=tournament, title=f'{label} Match')
    return tournament, match


async def _staff_everywhere(tenants) -> tuple[User, str]:
    """One user holding STAFF in every given community, with one OAuth token.

    This is the shape the isolation risk actually takes: a single credential
    that is legitimately entitled in more than one place, where only the call's
    own argument decides which place it acts in.
    """
    with tenant_scope(tenants[0].id):
        user, raw = await create_oauth_token(username='multi-staff', roles=[Role.STAFF])
    for tenant in tenants[1:]:
        await UserRole.create(user=user, role=Role.STAFF, tenant_id=tenant.id)
    return user, raw


class TestScoping:
    async def test_each_community_returns_only_its_own_matches(self, two_tenants):
        tenant_a, tenant_b = two_tenants
        _, match_a = await _seed(tenant_a, 'Alpha')
        _, match_b = await _seed(tenant_b, 'Beta')
        _, raw = await _staff_everywhere(two_tenants)

        async with mcp_session() as client:
            err_a, from_a = await call_tool(
                client, raw, 'list_matches', tenant=tenant_a.slug,
            )
            err_b, from_b = await call_tool(
                client, raw, 'list_matches', tenant=tenant_b.slug,
            )
        assert not err_a and not err_b, (from_a, from_b)
        assert [m['id'] for m in from_a['result']] == [match_a.id]
        assert [m['id'] for m in from_b['result']] == [match_b.id]

    async def test_list_users_returns_only_this_communitys_members(self, two_tenants):
        """Identity is global; the people list is not.

        A session resolves one community per call, so listing users had to stop
        returning every account on the platform — which is a bigger leak than the
        admin table, because it is machine-readable.
        """
        from models import TenantMembership

        tenant_a, tenant_b = two_tenants
        here = await User.create(discord_id=91001, username='alpha-member')
        there = await User.create(discord_id=91002, username='beta-member')
        await TenantMembership.create(user=here, tenant=tenant_a)
        await TenantMembership.create(user=there, tenant=tenant_b)
        _, raw = await _staff_everywhere(two_tenants)

        async with mcp_session() as client:
            err_a, from_a = await call_tool(
                client, raw, 'list_users', tenant=tenant_a.slug,
            )
            err_b, from_b = await call_tool(
                client, raw, 'list_users', tenant=tenant_b.slug,
            )
        assert not err_a and not err_b, (from_a, from_b)
        names_a = {u['username'] for u in from_a['result']}
        names_b = {u['username'] for u in from_b['result']}
        assert 'alpha-member' in names_a and 'beta-member' not in names_a
        assert 'beta-member' in names_b and 'alpha-member' not in names_b

    async def test_cross_community_row_is_not_found_not_forbidden(self, two_tenants):
        """The message must not confirm that the other community's row exists.

        `forbidden` would leak the existence of a match the caller may not know
        about; `not_found` is indistinguishable from a bad id.
        """
        tenant_a, tenant_b = two_tenants
        await _seed(tenant_a, 'Alpha')
        _, match_b = await _seed(tenant_b, 'Beta')
        _, raw = await _staff_everywhere(two_tenants)

        async with mcp_session() as client:
            is_error, payload = await call_tool(
                client, raw, 'get_match', match_id=match_b.id, tenant=tenant_a.slug,
            )
        assert is_error
        assert payload.startswith('Error executing tool get_match: not_found:'), payload

    async def test_audit_rows_do_not_cross_communities(self, two_tenants):
        tenant_a, tenant_b = two_tenants
        user, raw = await _staff_everywhere(two_tenants)
        from application.services import AuditService

        with tenant_scope(tenant_b.id):
            await AuditService().write_log(user, 'match.created', {'secret': 'b-only'})

        async with mcp_session() as client:
            is_error, payload = await call_tool(
                client, raw, 'list_audit_log', tenant=tenant_a.slug,
            )
        assert not is_error, payload
        assert all('b-only' not in str(item['details']) for item in payload['items'])

    async def test_every_domain_scopes_to_the_named_community(self, two_tenants):
        """One assertion per newly-exposed service, not just per tool.

        Each of these reaches its rows through a different service, so scoping
        is re-established once per domain rather than inherited. A tool wired to
        an unscoped read would pass every single-tenant test and fail only here.
        """
        from models import Equipment, Preset, Webhook

        tenant_a, tenant_b = two_tenants
        _, raw = await _staff_everywhere(two_tenants)
        with tenant_scope(tenant_b.id):
            await Equipment.create(asset_number=1, name='B console')
            await Preset.create(name='B preset', randomizer='alttpr', settings={})
            await Webhook.create(name='B hook', url='https://b.test', secret='x')

        async with mcp_session() as client:
            for tool in ('list_equipment', 'list_presets', 'list_webhooks'):
                is_error, payload = await call_tool(
                    client, raw, tool, tenant=tenant_a.slug,
                )
                assert not is_error, (tool, payload)
                assert payload['result'] == [], (tool, payload)


class TestTenantArgument:
    async def test_unknown_slug_and_unentitled_slug_look_identical(self, two_tenants):
        """Response-diffing must not enumerate the platform's communities.

        A caller with no role in `tenant-b` and a caller naming a slug that was
        never created should get the same shape of answer; otherwise the error
        text becomes a directory.
        """
        tenant_a, tenant_b = two_tenants
        with tenant_scope(tenant_a.id):
            _, raw = await create_oauth_token(username='a-only', roles=[Role.STAFF])

        async with mcp_session() as client:
            unknown_err, unknown = await call_tool(
                client, raw, 'list_matches', tenant='no-such-community',
            )
            other_err, other = await call_tool(
                client, raw, 'list_matches', tenant=tenant_b.slug,
            )
        assert unknown_err and other_err
        # Byte-identical, not merely both errors: otherwise the error text enumerates communities.
        assert 'not_found:' in unknown
        assert unknown.replace('no-such-community', tenant_b.slug) == other

    async def test_missing_tenant_argument_is_rejected(self, two_tenants):
        """A community-scoped tool must never silently pick a community."""
        tenant_a, _ = two_tenants
        await _seed(tenant_a, 'Alpha')
        _, raw = await _staff_everywhere(two_tenants)

        async with mcp_session() as client:
            is_error, payload = await call_tool(client, raw, 'list_matches')
        assert is_error
        assert 'not_found:' in payload
        assert 'list_tenants' in payload


class TestContextHygiene:
    async def test_binding_is_released_after_every_call(self, two_tenants):
        """A leaked binding would make the *next* caller inherit this community."""
        tenant_a, _ = two_tenants
        await _seed(tenant_a, 'Alpha')
        _, raw = await _staff_everywhere(two_tenants)

        # The autouse fixture binds a tenant, so assert it's unchanged, not None.
        ambient = get_current_tenant_id()
        async with mcp_session() as client:
            is_error, _ = await call_tool(
                client, raw, 'list_matches', tenant=tenant_a.slug,
            )
            assert not is_error
            assert get_current_tenant_id() == ambient
        assert get_current_tenant_id() == ambient

    async def test_interleaved_calls_do_not_inherit_each_other(self, two_tenants):
        """The assertion a single-call test cannot make.

        A stateful transport, or a binding set without a matching reset, passes
        every one-call-at-a-time test and fails exactly here — which is also how
        it would fail in production, under concurrent use.
        """
        tenant_a, tenant_b = two_tenants
        _, match_a = await _seed(tenant_a, 'Alpha')
        _, match_b = await _seed(tenant_b, 'Beta')
        _, raw = await _staff_everywhere(two_tenants)

        expected = {tenant_a.slug: [match_a.id], tenant_b.slug: [match_b.id]}
        async with mcp_session() as client:
            for slug in (tenant_a.slug, tenant_b.slug, tenant_a.slug, tenant_b.slug):
                is_error, payload = await call_tool(
                    client, raw, 'list_matches', tenant=slug,
                )
                assert not is_error, payload
                assert [m['id'] for m in payload['result']] == expected[slug], slug

    async def test_orientation_lists_every_entitled_community(self, two_tenants):
        """Per-tenant roles must be read inside each community's own binding.

        Reading them once outside would report one community's roles for all.
        """
        tenant_a, tenant_b = two_tenants
        _, raw = await _staff_everywhere(two_tenants)

        async with mcp_session() as client:
            is_error, payload = await call_tool(client, raw, 'whoami')
        assert not is_error, payload
        slugs = {t['slug'] for t in payload['tenants']}
        assert slugs == {tenant_a.slug, tenant_b.slug}
        for entry in payload['tenants']:
            assert Role.STAFF.value in entry['roles'], entry
