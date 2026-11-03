"""Every link an article offers opens something, in every community, for every reader.

Articles link to each other freely and are read in communities that switch
features off and by readers who hold no role. ``getting-started`` — the first
card on ``/help`` — linked ``/help/volunteering`` and ``/help/proctor`` in its
"depending on why you are here" table, and on two of the three dev communities
both rows landed on "That help article does not exist." The services now prune
a link the reader cannot follow (it keeps its words, loses the anchor); this
walks every article a reader is actually handed, in each shape of community,
and follows every internal link it still carries.
"""

import pytest

from application.content import Block, Span, internal_target, link_filter, prune_links
from application.services import EventInfoService, HelpService
from application.tenant_context import tenant_scope
from models import FeatureFlag, Role, Tenant, TenantFeatureFlag, UserRole
from tests.conftest import enable_all_flags
from tests.factories import make_user


def _links(blocks):
    for block in blocks:
        cells = [block.spans, *block.items, *block.headers]
        for row in block.rows:
            cells.extend(row)
        for spans in cells:
            for span in spans:
                if span.kind == 'link':
                    yield span.href


def _anchors(blocks) -> set[str]:
    return {b.anchor for b in blocks if b.kind == 'heading' and b.anchor}


async def _assert_every_link_resolves(user=None) -> int:
    """Follow every internal link in every article this reader can open here."""
    help_articles = {a.slug: a for a in await HelpService.list_articles(user)}
    event_on = await _flag_on(FeatureFlag.EVENT_INFO)
    event_articles = (
        {a.slug: a for a in await EventInfoService.list_articles(user)} if event_on else {}
    )
    followed = 0
    sources = [
        ('/help', slug, await HelpService.get_article(slug, user)) for slug in help_articles
    ] + [
        ('/event-info', slug, await EventInfoService.get_article(slug, user))
        for slug in event_articles
    ]
    for section, slug, article in sources:
        assert article is not None, f'{section}/{slug} is listed but does not open'
        for href in _links(article.blocks):
            target = internal_target(href)
            if target is None:
                continue
            followed += 1
            to_section, to_slug, anchor = target
            pool = help_articles if to_section == '/help' else event_articles
            if to_section == '/event-info':
                assert event_on, f'{section}/{slug} links {href} with no handbook here'
            if not to_slug:
                continue
            assert to_slug in pool, f'{section}/{slug} links {href}, which does not open here'
            if anchor:
                full = (
                    await HelpService.get_article(to_slug, user) if to_section == '/help'
                    else await EventInfoService.get_article(to_slug, user)
                )
                assert anchor in _anchors(full.blocks), f'{section}/{slug} links {href}: no such heading'
    return followed


async def _flag_on(flag: FeatureFlag) -> bool:
    from application.services.feature_flag_service import FeatureFlagService

    return await FeatureFlagService().is_enabled(flag)


async def _tenant(slug: str, *, flags=(), all_flags=False) -> Tenant:
    tenant = await Tenant.create(name=slug.title(), slug=slug)
    if all_flags:
        await enable_all_flags(tenant.id)
    for flag in flags:
        await TenantFeatureFlag.create(
            tenant_id=tenant.id, flag=flag.value, available=True, enabled=True,
        )
    return tenant


class TestEveryCommunityShape:
    async def test_every_flag_on(self, db):
        assert await _assert_every_link_resolves() > 0

    async def test_every_flag_off(self, db):
        tenant = await _tenant('bare')
        with tenant_scope(tenant.id):
            assert await _assert_every_link_resolves() > 0

    async def test_handbook_on_volunteers_off(self, db):
        tenant = await _tenant('handbook-only', flags=[FeatureFlag.EVENT_INFO])
        with tenant_scope(tenant.id):
            await _assert_every_link_resolves()

    @pytest.mark.parametrize('roles', [(), (Role.PROCTOR,), (Role.STAFF,)])
    async def test_the_full_handbook_for_each_kind_of_reader(self, db, roles):
        """``sgl26`` ships the largest handbook, with role-gated pages that link
        to each other and into help."""
        tenant = await _tenant('sgl26', all_flags=True)
        with tenant_scope(tenant.id):
            user = None
            if roles:
                user = await make_user(discord_id=4242)
                for role in roles:
                    await UserRole.create(user=user, role=role)
            assert await _assert_every_link_resolves(user) > 0

    async def test_the_full_handbook_with_volunteers_off(self, db):
        tenant = await _tenant('sgl26', flags=[FeatureFlag.EVENT_INFO])
        with tenant_scope(tenant.id):
            await _assert_every_link_resolves()


class TestWhatPruningKeeps:
    async def test_getting_started_keeps_its_rows_where_they_lead_somewhere(self, db):
        article = await HelpService.get_article('getting-started')
        hrefs = set(_links(article.blocks))
        assert {'/help/volunteering', '/help/proctor', '/help/player'} <= hrefs

    async def test_getting_started_unlinks_the_rows_that_would_dead_end(self, db):
        tenant = await _tenant('bare')
        with tenant_scope(tenant.id):
            article = await HelpService.get_article('getting-started')
        hrefs = set(_links(article.blocks))
        assert '/help/volunteering' not in hrefs
        assert '/help/proctor' not in hrefs
        assert '/help/player' in hrefs
        table = next(b for b in article.blocks if b.kind == 'table')
        rows = {''.join(s.text for s in row[0]): row[1] for row in table.rows}
        assert [s.kind for s in rows['Working shifts']] == ['text']
        assert rows['Working shifts'][0].text == 'Volunteering'
        assert rows['Playing'][0].kind == 'link'

    async def test_running_text_keeps_its_words(self, db):
        """The glossary's "See [Volunteering](/help/volunteering)" keeps the word."""
        tenant = await _tenant('bare')
        with tenant_scope(tenant.id):
            article = await HelpService.get_article('glossary')
        text = ' '.join(s.text for b in article.blocks for s in b.spans)
        assert 'Volunteering' in text


class TestPruneLinks:
    def _para(self, *spans):
        return Block('para', spans=tuple(spans))

    def test_a_readable_link_is_untouched(self):
        blocks = (self._para(Span('link', 'Crew', href='/help/crew')),)
        assert prune_links(blocks, link_filter({'crew'}, None)) == blocks

    def test_a_dead_link_becomes_its_label(self):
        out = prune_links(
            (self._para(Span('link', 'Proctor', href='/help/proctor#x')),),
            link_filter({'crew'}, None),
        )
        assert out[0].spans == (Span('text', 'Proctor'),)

    def test_off_site_and_other_app_links_are_not_article_links(self):
        readable = link_filter(set(), None)
        assert readable('https://example.com/help/x')
        assert readable('/home/my-schedule')
        assert readable('#anchor')

    def test_an_event_info_link_is_dead_without_a_handbook(self):
        readable = link_filter({'crew'}, None)
        assert not readable('/event-info/attending')
        assert not readable('/event-info')

    def test_a_dead_link_in_a_table_cell_is_unlinked_not_dropped(self):
        table = Block('table', rows=(
            ((Span('text', 'Proctoring'),), (Span('link', 'P', href='/help/proctor'),)),
        ))
        (out,) = prune_links((table,), link_filter(set(), None))
        assert out.rows == (((Span('text', 'Proctoring'),), (Span('text', 'P'),)),)

    def test_table_headers_are_pruned_too(self):
        table = Block(
            'table',
            headers=((Span('link', 'Proctor', href='/help/proctor'),),),
            rows=(((Span('text', 'x'),),),),
        )
        (out,) = prune_links((table,), link_filter(set(), None))
        assert out.headers == ((Span('text', 'Proctor'),),)

    def test_a_header_link_counts_as_a_section_to_look_up(self):
        from application.content import article_sections

        table = Block('table', headers=((Span('link', 'E', href='/event-info/x'),),))
        assert article_sections((table,)) == {'/event-info'}

    @pytest.mark.parametrize('href', [
        '/help/crew/', '/help/crew?ref=dm', '/help/crew/?ref=dm#approval', '/help/crew#approval',
    ])
    def test_a_trailing_slash_or_query_names_the_same_article(self, href):
        assert internal_target(href)[:2] == ('/help', 'crew')
        assert link_filter({'crew'}, None)(href)
        assert not link_filter(set(), None)(href)

    def test_a_section_index_with_a_trailing_slash_is_the_index(self):
        assert internal_target('/help/') == ('/help', '', '')
