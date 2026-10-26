"""``EventInfoService`` — the three rules the event handbook needs a service for.

The handbook is one community's own event documentation, so unlike help it is
gated three ways: the whole section behind ``EVENT_INFO``, an article behind its
own optional ``feature:``, and an article behind ``roles:``. The last one is what
lets the page stay public while the floor procedure the room staff work from is
not: the filter subtracts from what a reader sees, it never redirects them.

The ``db`` fixture provisions the default tenant (id 1, slug ``default``) with
every flag on, and that slug is the one the dev fixture handbook ships under — so
these run against real shipped content rather than a synthetic directory.
"""

import pytest

from application.errors import FeatureDisabledError
from application.services import EventInfoService
from application.tenant_context import tenant_scope
from models import Role, Tenant, Tournament, UserRole
from tests.conftest import enable_all_flags
from tests.factories import make_user


async def make_tournament(**kwargs) -> Tournament:
    return await Tournament.create(tenant_id=1, **kwargs)


@pytest.fixture
async def bare_tenant(db) -> Tenant:
    """A community with no feature-flag rows: everything optional is off."""
    return await Tenant.create(name='Bare', slug='bare')


@pytest.fixture
async def handbookless_tenant(db) -> Tenant:
    """A community granted the feature that ships no handbook directory."""
    tenant = await Tenant.create(name='Nothing Here', slug='nothing-here')
    await enable_all_flags(tenant.id)
    return tenant


async def _user_with(*roles: Role, discord_id: int = 5150):
    user = await make_user(discord_id=discord_id)
    for role in roles:
        await UserRole.create(user=user, role=role)
    return user


class TestTheSectionGate:
    async def test_listing_is_refused_where_the_feature_is_off(self, bare_tenant):
        """Not an empty list — a refusal. The service half of the gate is what
        stops a caller with no gate of its own reaching the section at all."""
        with tenant_scope(bare_tenant.id):
            with pytest.raises(FeatureDisabledError):
                await EventInfoService.list_articles()

    async def test_fetching_an_article_is_refused_where_the_feature_is_off(self, bare_tenant):
        with tenant_scope(bare_tenant.id):
            with pytest.raises(FeatureDisabledError):
                await EventInfoService.get_article('attending')

    async def test_fetching_a_snippet_is_refused_where_the_feature_is_off(self, bare_tenant):
        with tenant_scope(bare_tenant.id):
            with pytest.raises(FeatureDisabledError):
                await EventInfoService.get_snippet('check-in')


class TestTenantScoping:
    async def test_a_community_sees_its_own_handbook(self, db):
        slugs = {a.slug for a in await EventInfoService.list_articles()}
        assert 'attending' in slugs

    async def test_a_community_with_no_handbook_sees_nothing(self, handbookless_tenant):
        """An empty section, not an error. A community that has just been granted
        the feature and written nothing is exactly this state."""
        with tenant_scope(handbookless_tenant.id):
            assert await EventInfoService.list_articles() == []
            assert await EventInfoService.get_article('attending') is None

    async def test_one_communitys_article_is_not_readable_from_another(self, handbookless_tenant):
        """The isolation that matters here: content is filed under the tenant's
        slug, so a granted community must not read a neighbour's handbook."""
        with tenant_scope(handbookless_tenant.id):
            assert await EventInfoService.get_article('proctoring') is None
            assert await EventInfoService.get_snippet('check-in') is None

    async def test_an_unknown_slug_is_none(self, db):
        assert await EventInfoService.get_article('no-such-article') is None


class TestRoleGating:
    async def test_a_role_gated_article_is_hidden_from_a_signed_out_reader(self, db):
        slugs = {a.slug for a in await EventInfoService.list_articles()}
        assert 'proctoring' not in slugs

    async def test_a_role_gated_article_is_hidden_from_a_reader_without_the_role(self, db):
        user = await _user_with(Role.TRIFORCE_SUBMITTER)
        slugs = {a.slug for a in await EventInfoService.list_articles(user)}
        assert 'proctoring' not in slugs

    @pytest.mark.parametrize('role', [Role.VOLUNTEER, Role.PROCTOR, Role.STAFF])
    async def test_any_one_of_the_named_roles_is_enough(self, db, role):
        """Roles are an OR, matching the Volunteer hub's own gate — a proctor who
        was never given the Volunteer role still needs the room procedure."""
        user = await _user_with(role)
        slugs = {a.slug for a in await EventInfoService.list_articles(user)}
        assert 'proctoring' in slugs

    async def test_fetching_a_role_gated_article_directly_is_refused(self, db):
        """Same answer as a missing article: a reader should not learn that a
        page exists for people with more access than them."""
        assert await EventInfoService.get_article('proctoring') is None
        user = await _user_with(Role.PROCTOR)
        assert await EventInfoService.get_article('proctoring', user) is not None

    async def test_a_public_article_stays_readable_signed_out(self, db):
        assert await EventInfoService.get_article('attending') is not None


class TestSnippets:
    async def test_a_public_snippet_resolves_signed_out(self, db):
        snippet = await EventInfoService.get_snippet('check-in')
        assert snippet is not None
        assert snippet.article_slug == 'attending'

    async def test_a_snippet_links_back_into_its_own_section(self, db):
        """A popup opened from a handbook snippet must not send the reader to
        ``/help`` — the article it is quoting does not live there."""
        snippet = await EventInfoService.get_snippet('check-in')
        assert snippet.href.startswith('/event-info/attending')

    async def test_a_snippet_inherits_its_articles_role_gate(self, db):
        """A snippet is a region of an article, so it can never be more readable
        than the article it was cut from."""
        assert await EventInfoService.get_snippet('proctor-handover') is None
        user = await _user_with(Role.PROCTOR)
        assert await EventInfoService.get_snippet('proctor-handover', user) is not None

    async def test_an_unknown_snippet_is_none(self, db):
        assert await EventInfoService.get_snippet('no-such-snippet') is None


class TestTheTournamentList:
    async def test_it_lists_active_tournaments_and_leaves_out_tests(self, db):
        await make_tournament(name='Real Cup', rules_url='https://example.com/rules')
        await make_tournament(name='Integration Check', is_test=True)
        await make_tournament(name='Old Cup', is_active=False)
        rows = await EventInfoService.list_tournaments()
        names = [r.name for r in rows]
        assert 'Real Cup' in names
        assert 'Integration Check' not in names
        assert 'Old Cup' not in names
        assert next(r for r in rows if r.name == 'Real Cup').rules_url == 'https://example.com/rules'

    async def test_it_is_refused_where_the_feature_is_off(self, bare_tenant):
        with tenant_scope(bare_tenant.id):
            with pytest.raises(FeatureDisabledError):
                await EventInfoService.list_tournaments()

    async def test_each_row_carries_pool_plus_bonus(self, db):
        from datetime import datetime, timezone
        from decimal import Decimal

        synced = datetime(2026, 10, 19, 1, 0, tzinfo=timezone.utc)
        await make_tournament(name='Pooled', prize_pool=Decimal('1701'), prize_bonus=Decimal('500'),
                              sglive_slug='wwr', prize_pool_synced_at=synced)
        await make_tournament(name='Typed', prize_pool=Decimal('50'))
        await make_tournament(name='Zero', prize_pool=Decimal('0'))
        rows = {r.name: r for r in await EventInfoService.list_tournaments()}
        assert rows['Pooled'].prize_total == Decimal('2201')
        assert rows['Pooled'].prize_synced_at == synced
        assert rows['Typed'].prize_total == Decimal('50')
        assert rows['Typed'].prize_synced_at is None
        assert rows['Zero'].prize_total is None

    async def test_no_pool_shows_where_payouts_are_off(self, db):
        from decimal import Decimal

        from models import FeatureFlag, TenantFeatureFlag
        from tests.conftest import DEFAULT_TEST_TENANT_ID

        await make_tournament(name='Pooled', prize_pool=Decimal('100'))
        await TenantFeatureFlag.filter(
            tenant_id=DEFAULT_TEST_TENANT_ID, flag=FeatureFlag.PAYOUTS.value,
        ).update(enabled=False)
        rows = await EventInfoService.list_tournaments()
        assert rows[0].prize_total is None

    async def test_another_communitys_tournaments_never_appear(self, handbookless_tenant):
        await make_tournament(name='Default Cup')
        with tenant_scope(handbookless_tenant.id):
            assert await EventInfoService.list_tournaments() == []


class TestTheEventGuide:
    async def test_a_community_gets_the_pdf_it_ships(self, db):
        guide = await EventInfoService.get_guide()
        assert guide is not None
        assert guide.filename.endswith('.pdf')
        assert guide.path.read_bytes().startswith(b'%PDF')
        assert guide.size_bytes == guide.path.stat().st_size

    async def test_a_community_with_no_handbook_has_no_guide(self, handbookless_tenant):
        with tenant_scope(handbookless_tenant.id):
            assert await EventInfoService.get_guide() is None

    async def test_it_is_refused_where_the_feature_is_off(self, bare_tenant):
        with tenant_scope(bare_tenant.id):
            with pytest.raises(FeatureDisabledError):
                await EventInfoService.get_guide()


@pytest.mark.parametrize(('size', 'label'), [(200, '1 KB'), (203_025, '198 KB'), (3 * 1024 * 1024, '3.0 MB')])
def test_the_guide_size_reads_for_a_person(size, label, tmp_path):
    from application.services import EventGuide

    assert EventGuide(path=tmp_path, filename='g.pdf', size_bytes=size).size_label == label


class TestEventFolders:
    """A community's handbook sits in ``content/<community>/``; an event may have its own inside it."""

    @pytest.fixture
    def content(self, tmp_path, monkeypatch):
        from application.event_info import catalog

        monkeypatch.setattr(catalog, 'CONTENT_ROOT', tmp_path)
        for folder in ('acme', 'acme/y2025', 'other/y2026'):
            (tmp_path / folder).mkdir(parents=True)
            (tmp_path / folder / 'attending.md').write_text('# x\n')
        return catalog

    def test_an_event_with_a_folder_reads_its_own(self, content):
        assert content.handbook_key('acme', ['y2025']) == 'acme/y2025'

    def test_an_event_without_one_falls_back_to_the_next_candidate(self, content):
        assert content.handbook_key('acme', ['y2026', 'y2025']) == 'acme/y2025'

    def test_with_no_event_folder_it_reads_the_communitys(self, content):
        assert content.handbook_key('acme', ['y2026']) == 'acme'

    def test_another_communitys_event_folder_is_never_reached(self, content):
        """Event slugs are unique only within a community."""
        assert content.handbook_key('acme', ['y2026']) == 'acme'
        assert content.handbook_key('acme', ['../other/y2026']) == 'acme'

    async def test_the_service_reads_the_current_events_folder(self, db, content):
        from application.event_context import event_scope
        from models import Event, EventStatus

        await Tenant.filter(id=1).update(slug='acme')
        default = await Event.get(tenant_id=1, is_default=True)
        default.slug = 'y2025'
        await default.save(update_fields=['slug'])
        nxt = await Event.create(tenant_id=1, name='Next', slug='y2026', status=EventStatus.LIVE)
        with event_scope(nxt.id):
            assert await EventInfoService._tenant_slug() == 'acme/y2025'
        with event_scope(default.id):
            assert await EventInfoService._tenant_slug() == 'acme/y2025'
