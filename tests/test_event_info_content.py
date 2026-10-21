"""The shipped event handbooks parse, and their links go somewhere.

The counterpart to ``test_help_content.py``'s ``TestCatalogue``, over content
that is filed per tenant. No database and no service: this is about the files.

One rule here has no equivalent in help. A handbook article may name ``roles:``,
and that is a *gate* — an article whose front matter is subtly wrong does not
render badly, it renders to the wrong audience. So the parse of that field is
asserted directly rather than inferred from the page.
"""

import re
from pathlib import Path

import pytest

from application.event_info import (
    CONTENT_ROOT,
    article_for_tenant,
    articles_for_tenant,
    snippet_for_tenant,
    tenant_slugs,
)
from models import Role

ALL_ARTICLES = [
    (slug, article)
    for slug in tenant_slugs()
    for article in articles_for_tenant(slug)
]


def test_some_handbooks_ship():
    """A guard on the fixtures themselves: an empty content tree would make
    every parametrized test below vacuously pass."""
    assert 'sgl26' in tenant_slugs()
    assert 'default' in tenant_slugs(), 'the dev fixture handbook is what /ui-validation renders'
    assert len(ALL_ARTICLES) >= 4


class TestEveryArticleParses:
    @pytest.mark.parametrize('tenant,article', ALL_ARTICLES, ids=lambda v: getattr(v, 'slug', v))
    def test_it_has_a_title_and_a_body(self, tenant, article):
        assert article.title
        assert article.blocks, f'{tenant}/{article.slug} parsed to nothing'

    @pytest.mark.parametrize('tenant,article', ALL_ARTICLES, ids=lambda v: getattr(v, 'slug', v))
    def test_it_opens_with_a_top_level_heading_somewhere(self, tenant, article):
        assert any(b.kind == 'heading' and b.level == 2 for b in article.blocks), \
            f'{tenant}/{article.slug} has no ## heading, so it gets no contents list'

    @pytest.mark.parametrize('tenant', tenant_slugs())
    def test_slugs_are_unique_within_a_tenant(self, tenant):
        slugs = [a.slug for a in articles_for_tenant(tenant)]
        assert len(slugs) == len(set(slugs)), f'{tenant} has duplicate article slugs: {slugs}'


class TestRoleGates:
    def test_the_role_gated_articles_parse_to_real_roles(self):
        """A typo in ``roles:`` drops that name rather than falling open, so an
        article meant for room staff must be asserted to actually carry them."""
        for tenant in tenant_slugs():
            article = article_for_tenant(tenant, 'proctoring')
            assert article is not None, f'{tenant} ships no proctoring article'
            assert set(article.roles) == {Role.VOLUNTEER, Role.PROCTOR, Role.STAFF}

    def test_the_reader_facing_articles_are_public(self):
        """The inverse, and the one that would leak: an article nobody meant to
        gate must not have picked up roles."""
        role_gated = {'proctoring'}
        for tenant, article in ALL_ARTICLES:
            if article.slug not in role_gated:
                assert article.roles == (), \
                    f'{tenant}/{article.slug} is unexpectedly role-gated'


class TestLinks:
    @staticmethod
    def _sources():
        return [(p, p.read_text(encoding='utf-8')) for p in sorted(CONTENT_ROOT.rglob('*.md'))]

    def test_internal_handbook_links_resolve(self):
        for path, text in self._sources():
            tenant = path.parent.name
            slugs = {a.slug for a in articles_for_tenant(tenant)}
            for target in re.findall(r'\]\(/event-info/([a-z0-9-]+)', text):
                assert target in slugs, \
                    f'{tenant}/{path.name} links to missing /event-info/{target}'

    def test_anchor_links_resolve_to_a_real_heading(self):
        for path, text in self._sources():
            tenant = path.parent.name
            by_slug = {a.slug: {anchor for anchor, _ in a.headings}
                       for a in articles_for_tenant(tenant)}
            for slug, anchor in re.findall(r'\]\(/event-info/([a-z0-9-]+)#([a-z0-9-]+)\)', text):
                assert anchor in by_slug.get(slug, set()), \
                    f'{tenant}/{path.name} links to /event-info/{slug}#{anchor}, no such heading'

    def test_links_into_help_resolve(self):
        """A handbook deliberately defers to help for app mechanics, so those
        links are load-bearing — and nothing else checks this direction."""
        from application.help import all_articles

        help_slugs = {a.slug for a in all_articles()}
        for path, text in self._sources():
            for target in re.findall(r'\]\(/help/([a-z0-9-]+)', text):
                assert target in help_slugs, \
                    f'{path.parent.name}/{path.name} links to missing /help/{target}'

    def test_anchored_links_into_help_resolve(self):
        """The gap the slug check leaves. A handbook deep-linking a *section* of
        a help article breaks silently when that heading is renamed — and help's
        own anchor test only scans help's own files, so this direction had
        nothing watching it."""
        from application.help import all_articles

        by_slug = {a.slug: {anchor for anchor, _ in a.headings} for a in all_articles()}
        for path, text in self._sources():
            for slug, anchor in re.findall(r'\]\(/help/([a-z0-9-]+)#([a-z0-9-]+)\)', text):
                assert anchor in by_slug.get(slug, set()), \
                    f'{path.parent.name}/{path.name} links to /help/{slug}#{anchor}, no such heading'

    def test_no_unsafe_link_targets_survive_parsing(self):
        """The parser degrades an unsafe target to plain text; this asserts no
        article is relying on one having been rendered as a link."""
        for _tenant, article in ALL_ARTICLES:
            for block in article.blocks:
                for span in block.spans:
                    if span.kind == 'link':
                        assert span.href.startswith(('/', '#', 'https://', 'mailto:'))


class TestSnippets:
    def test_a_snippet_knows_which_section_it_came_from(self):
        """``base_path`` is what stops a handbook popup deep-linking into /help."""
        snippet = snippet_for_tenant('default', 'check-in')
        assert snippet is not None
        assert snippet.base_path == '/event-info'
        assert snippet.href.startswith('/event-info/')

    def test_a_snippet_is_a_region_not_a_second_copy(self):
        """The whole point of the mechanism: the popup's words are the article's
        words, so the two cannot drift."""
        snippet = snippet_for_tenant('default', 'check-in')
        article = article_for_tenant('default', 'attending')
        article_text = ' '.join(
            span.text for block in article.blocks for span in block.spans
        )
        snippet_opening = snippet.blocks[0].spans[0].text
        assert snippet_opening in article_text

    def test_the_moved_snippets_kept_their_names(self):
        """These names are wired to icons in pages/ and theme/. Renaming one on
        the way out of help would turn its icon into nothing, silently."""
        moved = {
            'check-in', 'player-room', 'player-stage', 'player-trouble',
            'player-turnover', 'proctor-room', 'proctor-escalation',
            'proctor-handover',
        }
        assert {n for n in moved if snippet_for_tenant('sgl26', n) is None} == set()


class TestTheSlugMismatchWarning:
    """The compensating control for the one failure this design cannot check.

    Directory names live in the repo and tenant slugs live in the database, so a
    mismatch renders the empty state and looks exactly like "nobody has written
    anything yet". The log line is the only signal.
    """

    def test_an_unknown_slug_warns_with_the_path_it_looked_for(self, caplog):
        from application.event_info import catalog

        catalog._warned.discard('no-such-community')
        with caplog.at_level('WARNING', logger='application.event_info.catalog'):
            assert articles_for_tenant('no-such-community') == []
        assert 'no-such-community' in caplog.text
        assert 'must equal the tenant slug' in caplog.text

    def test_it_warns_once_rather_than_on_every_page_build(self, caplog):
        from application.event_info import catalog

        catalog._warned.discard('quiet-after-the-first')
        with caplog.at_level('WARNING', logger='application.event_info.catalog'):
            articles_for_tenant('quiet-after-the-first')
            assert 'quiet-after-the-first' in caplog.text
            # caplog accumulates across the whole test, so the second call's
            # silence is only visible once the first call's record is dropped.
            caplog.clear()
            articles_for_tenant('quiet-after-the-first')
        assert caplog.text == ''

    def test_a_shipped_slug_does_not_warn(self, caplog):
        with caplog.at_level('WARNING', logger='application.event_info.catalog'):
            assert articles_for_tenant('sgl26')
        assert caplog.text == ''


def test_content_lives_inside_the_package():
    """``.dockerignore`` drops ``docs`` and root-level ``*.md`` from the build
    context, so a handbook filed anywhere else would not reach the image."""
    assert CONTENT_ROOT == Path(__file__).resolve().parent.parent / \
        'application' / 'event_info' / 'content'


def test_an_embed_line_parses_to_an_embed_block():
    from application.content import parse_blocks

    blocks = parse_blocks('Intro.\n\n:::tournaments\n\nAfter.')
    assert [b.kind for b in blocks] == ['para', 'embed', 'para']
    assert blocks[1].name == 'tournaments'


def test_an_unknown_embed_shows_as_text_rather_than_vanishing():
    from application.content import parse_blocks

    blocks = parse_blocks(':::tournamnets')
    assert blocks[0].kind == 'para'
    assert blocks[0].spans[0].text == ':::tournamnets'


def test_the_sgl_schedule_embeds_the_live_tournament_list():
    article = article_for_tenant('sgl26', 'schedule')
    assert any(b.kind == 'embed' and b.name == 'tournaments' for b in article.blocks)


def test_the_sgl_handbook_ships_the_organisers_guide():
    from application.event_info import guide_pdf_for_tenant

    guide = guide_pdf_for_tenant('sgl26')
    assert guide is not None and guide.name == 'SGLive-2026-Event-Guide.pdf'
    assert guide.read_bytes().startswith(b'%PDF')


def test_an_unsafe_slug_never_reaches_the_filesystem():
    from application.event_info import guide_pdf_for_tenant

    assert guide_pdf_for_tenant('../sgl26') is None


@pytest.mark.parametrize('tenant,article', ALL_ARTICLES, ids=lambda v: getattr(v, 'slug', v))
def test_no_article_takes_the_guide_viewers_path(tenant, article):
    """``/event-info/guide`` is the in-app guide viewer, registered ahead of the
    article route, so an article named ``guide`` could never be opened."""
    assert article.slug != 'guide'
