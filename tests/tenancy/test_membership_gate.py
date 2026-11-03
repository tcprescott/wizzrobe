"""A community is visible to the people who belong to it.

The audit's scenario: a platform user with no roles and no membership in a
brand-new community opened its home schedule, saw its first match with both
players' names, and was offered Sign Up as commentator and tracker. Both halves
are closed here — the page gate, and the crew-signup service behind the button,
because UI-only gating is not gating.

``_tenant_page`` ends in ``ui.page(path)(wrapper)``, so these swap that for a
pass-through and call the wrapper directly: the gate is plain async Python and
does not need a browser to exercise.
"""

from types import SimpleNamespace

import pytest

from application.services.crew_service import CrewService
from application.services.tenant_membership_service import TenantMembershipService
from application.tenant_context import tenant_scope
from middleware import auth as auth_module
from models import Match, Role, Tenant, TenantMembership, Tournament, User, UserRole


@pytest.fixture
def page_harness(monkeypatch):
    """Build a tenant page and record what it rendered instead of the body.

    Returns ``(build, rendered)`` — ``build(**kwargs)`` produces a callable page,
    and ``rendered`` accumulates ``('join'|'error'|'body', payload)``.
    """
    rendered = []

    monkeypatch.setattr(auth_module.ui, 'page', lambda *a, **kw: (lambda fn: fn))
    monkeypatch.setattr(auth_module, 'record_page_view', lambda *a, **kw: None)
    monkeypatch.setattr(auth_module, 'stash_client_tenant_id', lambda *_: None)
    monkeypatch.setattr(auth_module, 'stash_client_host_mode', lambda *_: None)

    session = {}
    monkeypatch.setattr(
        auth_module, 'app', SimpleNamespace(storage=SimpleNamespace(user=session)),
    )

    import theme.error_page as error_page
    import theme.join_page as join_page

    monkeypatch.setattr(
        join_page, 'render_join_page',
        lambda **kw: rendered.append(('join', kw)),
    )
    monkeypatch.setattr(
        error_page, 'render_error_page',
        lambda **kw: rendered.append(('error', kw)),
    )

    def build(**page_kwargs):
        @auth_module.protected_page('/harness', **page_kwargs)
        async def _page():
            rendered.append(('body', None))

        return _page

    return SimpleNamespace(build=build, rendered=rendered, session=session)


async def _member(discord_id: int, tenant: Tenant, username: str) -> User:
    user = await User.create(discord_id=discord_id, username=username)
    await TenantMembership.create(user=user, tenant=tenant)
    return user


class TestTheGate:
    async def test_a_non_member_sees_the_join_page_not_a_403(self, page_harness, db):
        stranger = await User.create(discord_id=8300, username='stranger')
        page_harness.session['discord_id'] = str(stranger.discord_id)
        page = page_harness.build()

        with tenant_scope(1):
            await page()

        kinds = [kind for kind, _ in page_harness.rendered]
        assert kinds == ['join']
        assert 'error' not in kinds

    async def test_a_non_member_cannot_reach_a_tenant_page(self, page_harness, db):
        stranger = await User.create(discord_id=8301, username='stranger')
        page_harness.session['discord_id'] = str(stranger.discord_id)
        page = page_harness.build()

        with tenant_scope(1):
            await page()

        assert ('body', None) not in page_harness.rendered

    async def test_a_member_reaches_it_normally(self, page_harness, db):
        tenant = await Tenant.get(id=1)
        member = await _member(8302, tenant, 'member')
        page_harness.session['discord_id'] = str(member.discord_id)
        page = page_harness.build()

        with tenant_scope(1):
            await page()

        assert page_harness.rendered == [('body', None)]

    async def test_a_signed_out_visitor_sees_the_join_page(self, page_harness, db):
        page = page_harness.build()
        with tenant_scope(1):
            await page()

        kind, kwargs = page_harness.rendered[0]
        assert kind == 'join'
        assert kwargs['user'] is None

    async def test_a_super_admin_reaches_every_tenant_without_membership(
        self, page_harness, two_tenants, db,
    ):
        tenant_a, tenant_b = two_tenants
        root = await User.create(discord_id=8303, username='root')
        await UserRole.create(user=root, role=Role.SUPER_ADMIN, tenant=None)
        page_harness.session['discord_id'] = str(root.discord_id)
        page = page_harness.build()

        for tenant in (tenant_a, tenant_b):
            with tenant_scope(tenant.id):
                await page()

        assert page_harness.rendered == [('body', None), ('body', None)]
        assert await TenantMembership.filter(user=root).count() == 0

    async def test_a_role_holder_is_a_member_and_reaches_it(self, page_harness, db):
        """Wave 2's invariant, end to end.

        Without it, granting someone STAFF would leave them locked out of the
        community they administer.
        """
        from application.services.user_service import UserService

        boss = await _member(8304, await Tenant.get(id=1), 'boss')
        await UserRole.create(user=boss, role=Role.STAFF, tenant_id=1)
        newcomer = await User.create(discord_id=8305, username='newcomer')
        with tenant_scope(1):
            await UserService().grant_role(newcomer, Role.PROCTOR, boss)

        page_harness.session['discord_id'] = str(newcomer.discord_id)
        page = page_harness.build()
        with tenant_scope(1):
            await page()

        assert page_harness.rendered == [('body', None)]

    async def test_membership_in_one_tenant_grants_nothing_in_another(
        self, page_harness, two_tenants, db,
    ):
        """The audit's exact scenario: a platform user opening a new community."""
        tenant_a, tenant_b = two_tenants
        person = await _member(8306, tenant_a, 'person')
        page_harness.session['discord_id'] = str(person.discord_id)
        page = page_harness.build()

        with tenant_scope(tenant_a.id):
            await page()
        with tenant_scope(tenant_b.id):
            await page()

        assert [kind for kind, _ in page_harness.rendered] == ['body', 'join']

    async def test_the_join_page_knows_a_request_is_already_pending(
        self, page_harness, db,
    ):
        stranger = await User.create(discord_id=8307, username='stranger')
        await TenantMembershipService().request_to_join(stranger, 1)
        page_harness.session['discord_id'] = str(stranger.discord_id)
        page = page_harness.build()

        with tenant_scope(1):
            await page()

        _, kwargs = page_harness.rendered[0]
        assert kwargs['pending'] is True

    async def test_the_join_page_names_the_community(self, page_harness, db):
        stranger = await User.create(discord_id=8308, username='stranger')
        page_harness.session['discord_id'] = str(stranger.discord_id)
        page = page_harness.build()

        with tenant_scope(1):
            await page()

        _, kwargs = page_harness.rendered[0]
        assert kwargs['tenant_name'] == 'Default'
        assert kwargs['tenant_id'] == 1

    async def test_the_membership_gate_runs_after_the_feature_gate(
        self, page_harness, db,
    ):
        """A feature the community has off is 404 for everyone, member or not.

        Ordering matters: showing a non-member a join page for a feature that
        does not exist here would advertise it.
        """
        from models import FeatureFlag, TenantFeatureFlag

        await TenantFeatureFlag.filter(
            tenant_id=1, flag=FeatureFlag.BRACKETS.value,
        ).update(available=False, enabled=False)
        stranger = await User.create(discord_id=8309, username='stranger')
        page_harness.session['discord_id'] = str(stranger.discord_id)
        page = page_harness.build(feature=FeatureFlag.BRACKETS)

        with tenant_scope(1):
            await page()

        kind, kwargs = page_harness.rendered[0]
        assert kind == 'error' and kwargs['status_code'] == 404

    async def test_the_gate_renders_in_place_so_a_deep_link_survives(
        self, page_harness, monkeypatch, db,
    ):
        """A Discord DM links to a match page.

        The door is rendered *at that URL* rather than redirected away from, so
        once the request is approved a reload lands where the link pointed —
        rather than at the community root with the context lost.
        """
        navigations = []
        monkeypatch.setattr(
            auth_module.ui, 'navigate',
            SimpleNamespace(to=lambda *a, **kw: navigations.append(a)),
        )
        stranger = await User.create(discord_id=8318, username='stranger')
        page_harness.session['discord_id'] = str(stranger.discord_id)
        page = page_harness.build()

        with tenant_scope(1):
            await page()

        assert navigations == []
        assert page_harness.rendered[0][0] == 'join'

    async def test_an_inactive_community_is_not_conflated_with_membership(
        self, page_harness, db,
    ):
        """Deliberately *not* gated on ``Tenant.is_active``.

        An inactive community is a separate concern with its own handling;
        folding it in here would make both harder to reason about.
        """
        tenant = await Tenant.get(id=1)
        tenant.is_active = False
        await tenant.save()
        member = await _member(8310, tenant, 'member')
        page_harness.session['discord_id'] = str(member.discord_id)
        page = page_harness.build()

        with tenant_scope(1):
            await page()

        assert page_harness.rendered == [('body', None)]


class TestWhatStaysPublic:
    def test_a_public_page_is_not_login_gated(self):
        from middleware.auth import _matches_protected_route, protected_routes, public_page

        @public_page('/gate-test-public')
        async def _page():
            pass

        assert '/gate-test-public' not in protected_routes
        assert not _matches_protected_route('/gate-test-public')

    async def test_public_bracket_is_reachable_by_a_non_member(self, page_harness, monkeypatch, db):
        rendered = page_harness.rendered
        stranger = await User.create(discord_id=8311, username='stranger')
        page_harness.session['discord_id'] = str(stranger.discord_id)

        @auth_module.public_page('/gate-test-public-body')
        async def _page():
            rendered.append(('body', None))

        with tenant_scope(1):
            await _page()

        assert rendered == [('body', None)]

    async def test_public_bracket_is_reachable_signed_out(self, page_harness, db):
        rendered = page_harness.rendered

        @auth_module.public_page('/gate-test-public-anon')
        async def _page():
            rendered.append(('body', None))

        with tenant_scope(1):
            await _page()

        assert rendered == [('body', None)]

    def test_the_real_spectator_routes_still_use_public_page(self):
        """The bracket views are the ones that must survive the gate.

        Reading the decorators rather than importing ``frontend`` (which builds
        a live Discord OAuth client): a route that swapped to ``protected_page``
        would start showing spectators the join door.
        """
        import ast
        from pathlib import Path

        source = (Path(__file__).resolve().parents[2] / 'pages/brackets.py').read_text()
        decorators = [
            node.func.id
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in {'public_page', 'protected_page'}
        ]
        assert decorators == ['public_page', 'public_page'], decorators


class TestCrewSignupAtTheService:
    async def test_crew_signup_refuses_a_non_member_at_the_service(self, db):
        """F5's measured symptom was the *Sign Up button*, not the page.

        The page gate hides the surface; this is what stops the REST endpoint
        and the Discord button, which never see a page at all.
        """
        tenant = await Tenant.get(id=1)
        tournament = await Tournament.create(name='T', tenant=tenant)
        match = await Match.create(tournament=tournament, tenant=tenant)
        stranger = await User.create(discord_id=8312, username='stranger')

        with pytest.raises(ValueError) as exc:
            await CrewService().signup_crew(match.id, stranger, 'commentator')
        assert 'not a member' in str(exc.value)

    async def test_a_member_can_still_sign_up(self, db):
        tenant = await Tenant.get(id=1)
        tournament = await Tournament.create(name='T', tenant=tenant)
        match = await Match.create(tournament=tournament, tenant=tenant)
        member = await _member(8313, tenant, 'member')

        await CrewService().signup_crew(match.id, member, 'commentator')
        assert await match.commentators.filter(user=member).exists()


class TestTheTenantHome:
    """The home page is registered with a bare ``ui.page``, not ``_tenant_page``.

    The same function also renders the platform community picker (no tenant,
    must stay anonymous), so it cannot simply be decorated — it calls the shared
    ``enforce_membership`` itself. That split is exactly the kind of thing the
    gate's blast-radius review exists to catch: it is where the audit measured
    the symptom, and it is the one tenant route the decorator never saw.
    """

    def test_the_home_page_calls_the_shared_gate(self):
        import ast
        from pathlib import Path

        source = (Path(__file__).resolve().parents[2] / 'pages/home.py').read_text()
        calls = [
            node.func.id
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]
        assert 'enforce_membership' in calls

    async def test_a_non_member_gets_the_door(self, page_harness, db):
        stranger = await User.create(discord_id=8400, username='stranger')
        assert await auth_module.enforce_membership(1, stranger) is True
        assert page_harness.rendered[0][0] == 'join'

    async def test_a_member_passes_through(self, page_harness, db):
        member = await _member(8401, await Tenant.get(id=1), 'member')
        assert await auth_module.enforce_membership(1, member) is False
        assert page_harness.rendered == []

    async def test_a_super_admin_passes_through(self, page_harness, db):
        root = await User.create(discord_id=8402, username='root')
        await UserRole.create(user=root, role=Role.SUPER_ADMIN, tenant=None)
        assert await auth_module.enforce_membership(1, root) is False
        assert page_harness.rendered == []

    async def test_an_anonymous_visitor_gets_the_door(self, page_harness, db):
        assert await auth_module.enforce_membership(1, None) is True
        assert page_harness.rendered[0][1]['user'] is None


class TestDiscordAutoJoin:
    """The gate's second way through, end to end: no join page, just the body."""

    @pytest.fixture
    def in_the_server(self, monkeypatch):
        import application.services.discord as discord_pkg

        class _InGuild:
            async def is_guild_member(self, guild_id, user_id):
                return True, True

        monkeypatch.setattr(discord_pkg, 'DiscordService', _InGuild)

    async def _open_the_door(self) -> None:
        from models import SystemConfiguration

        await SystemConfiguration.create(name='discord_auto_join', value='true', tenant_id=1)
        await Tenant.filter(id=1).update(discord_guild_id=555)

    async def test_a_server_member_reaches_the_page(self, page_harness, in_the_server, db):
        await self._open_the_door()
        newcomer = await User.create(discord_id=8320, username='newcomer')
        page_harness.session['discord_id'] = str(newcomer.discord_id)
        page = page_harness.build()

        with tenant_scope(1):
            await page()

        assert page_harness.rendered == [('body', None)]
        assert await TenantMembership.exists(user=newcomer, tenant_id=1)

    async def test_switched_off_the_door_stays_shut(self, page_harness, in_the_server, db):
        await Tenant.filter(id=1).update(discord_guild_id=555)
        newcomer = await User.create(discord_id=8321, username='newcomer')
        page_harness.session['discord_id'] = str(newcomer.discord_id)
        page = page_harness.build()

        with tenant_scope(1):
            await page()

        assert [kind for kind, _ in page_harness.rendered] == ['join']
