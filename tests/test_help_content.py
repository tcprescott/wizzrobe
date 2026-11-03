"""The in-app help: its parser, its catalogue, and the promises the app makes about it.

Three things are worth holding still here.

The **parser** is a security boundary, not a convenience. Help articles are never
rendered through ``ui.markdown`` — they are parsed into a closed block/span
vocabulary and drawn with native elements — so the tests that matter most are the
ones proving an article cannot smuggle a link target or a chip class past it.

The **catalogue** is a contract with the pages: every ``help_icon('x')`` call in
``pages/`` and ``theme/`` names a snippet, and a snippet that stops existing turns
that icon into nothing at all — silently. The cross-reference test below is the
only thing that catches an article rename.

The **vocabulary** must match the app's. Help that teaches a state name or a chip
tone the board does not use is worse than no help, so both are checked against
their real sources rather than against a copy.
"""

import ast
import re
from pathlib import Path

import pytest

from application.content.blocks import plain_text
from application.event_info import snippet_for_tenant, tenant_slugs
from application.help import (
    CHIP_TONES,
    STATE_STYLES,
    all_articles,
    get_article,
    get_snippet,
    parse_blocks,
    parse_inline,
    reload_articles,
    slugify,
)
from application.services.match.match_status import LEGACY_STATE_LABELS

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestInlineParsing:
    def test_plain_text_is_one_span(self):
        assert parse_inline('hello') == (('text', 'hello'),) or [
            (s.kind, s.text) for s in parse_inline('hello')
        ] == [('text', 'hello')]

    def test_bold_and_code_are_recognised(self):
        spans = parse_inline('a **bold** and `code` run')
        assert [(s.kind, s.text) for s in spans] == [
            ('text', 'a '), ('strong', 'bold'), ('text', ' and '),
            ('code', 'code'), ('text', ' run'),
        ]

    def test_italic_is_recognised_without_stealing_bolds_asterisks(self):
        """Ordered alternation: `**x**` must win over the single-asterisk branch,
        or every bold run renders as two stray italics."""
        spans = parse_inline('**bold** and *soft*')
        assert [(s.kind, s.text) for s in spans] == [
            ('strong', 'bold'), ('text', ' and '), ('em', 'soft'),
        ]

    def test_articles_do_not_leave_literal_asterisks_on_the_page(self):
        """Before italics were supported, `*follow*` rendered as the characters
        the author typed, in the middle of a sentence."""
        for article in all_articles():
            assert '*' not in plain_text(list(article.blocks)), \
                f'{article.slug} renders a literal asterisk'

    def test_an_internal_link_keeps_its_target(self):
        span = parse_inline('see [crew](/help/crew)')[1]
        assert (span.kind, span.text, span.href) == ('link', 'crew', '/help/crew')

    @pytest.mark.parametrize('href', [
        'javascript:alert(1)',
        'data:text/html;base64,PHNjcmlwdD4=',
        'vbscript:msgbox',
        'http://insecure.example.com',
        'file:///etc/passwd',
    ])
    def test_an_unsafe_link_target_degrades_to_plain_text(self, href):
        """No anchor is produced, and the label survives as text.

        Asserted as a property rather than an exact span list: a target
        containing its own bracket (``javascript:alert(1)``) ends the match early
        and leaves the remainder as a separate text span. That is cosmetically
        untidy in an article nobody should have written, and it is *not* a hole —
        no span with ``kind == 'link'`` is emitted either way, which is the thing
        that matters.
        """
        spans = parse_inline(f'[click me]({href})')
        assert not any(span.kind == 'link' for span in spans)
        assert all(span.href == '' for span in spans)
        assert 'click me' in ''.join(span.text for span in spans)

    def test_a_bare_https_address_becomes_a_link(self):
        """Staff paste seed links straight into tournament descriptions."""
        url = 'https://4-9-7.finalfantasyrandomizer.com/?s=00000000&f=7yYeU3N-shYk.mWHn'
        spans = parse_inline(f'Seed generation: {url}. Good luck')
        assert [(s.kind, s.text, s.href) for s in spans] == [
            ('text', 'Seed generation: ', ''),
            ('link', url, url),
            ('text', '.', ''),
            ('text', ' Good luck', ''),
        ]

    @pytest.mark.parametrize('text, url', [
        ('(see https://example.com/a)', 'https://example.com/a'),
        ('https://en.wikipedia.org/wiki/Foo_(bar)!', 'https://en.wikipedia.org/wiki/Foo_(bar)'),
        ('"https://example.com",', 'https://example.com'),
    ])
    def test_a_bare_address_leaves_the_sentence_punctuation_behind(self, text, url):
        links = [s for s in parse_inline(text) if s.kind == 'link']
        assert [(s.text, s.href) for s in links] == [(url, url)]

    @pytest.mark.parametrize('text', [
        'http://insecure.example.com',
        'javascript:alert(1)',
        '`https://example.com`',
        'https://',
    ])
    def test_only_a_complete_https_address_outside_code_is_linked(self, text):
        assert not any(s.kind == 'link' for s in parse_inline(text))

    def test_a_chip_carries_a_known_tone(self):
        span = parse_inline('{chip:pending:Awaiting approval}')[0]
        assert (span.kind, span.text, span.tone) == ('chip', 'Awaiting approval', 'pending')

    def test_an_unknown_chip_tone_falls_back_rather_than_emitting_a_dead_class(self):
        """A class with no CSS rule behind it renders as unstyled text in a pill
        shape — worse than the neutral tone it falls back to."""
        assert parse_inline('{chip:bogus:Hi}')[0].tone == 'neutral'

    def test_a_known_state_renders_as_a_state_span(self):
        span = parse_inline('{state:Confirmed}')[0]
        assert (span.kind, span.text) == ('state', 'Confirmed')

    def test_an_unknown_state_degrades_to_text(self):
        """The renderer indexes STATE_STYLES directly; an unknown name reaching
        it would be a KeyError mid-render."""
        assert parse_inline('{state:Nonsense}')[0].kind == 'text'

    def test_html_in_article_text_is_never_markup(self):
        """The whole point of the block model: there is no path from article text
        to markup, so a tag arrives as characters."""
        spans = parse_inline('<script>alert(1)</script>')
        assert all(s.kind == 'text' for s in spans)
        assert ''.join(s.text for s in spans) == '<script>alert(1)</script>'


class TestBlockParsing:
    def test_headings_get_anchors(self):
        block = parse_blocks('## Signing up')[0]
        assert (block.kind, block.level, block.anchor) == ('heading', 2, 'signing-up')

    def test_a_wrapped_list_item_stays_one_item(self):
        """A hard-wrapped bullet used to end the list and reopen it, rendering as
        two lists with a stray paragraph between them."""
        blocks = parse_blocks('- first line\n  continues here\n- second')
        assert len(blocks) == 1
        assert blocks[0].kind == 'bullets'
        assert [''.join(s.text for s in item) for item in blocks[0].items] == [
            'first line continues here', 'second',
        ]

    def test_a_wrapped_numbered_item_stays_one_item(self):
        blocks = parse_blocks('1. one\n   wrapped\n2. two')
        assert len(blocks) == 1
        assert len(blocks[0].items) == 2

    def test_a_table_keeps_its_headers_and_rows(self):
        blocks = parse_blocks('| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |')
        table = blocks[0]
        assert table.kind == 'table'
        assert [''.join(s.text for s in c) for c in table.headers] == ['A', 'B']
        assert len(table.rows) == 2

    def test_a_chip_inside_a_table_cell_survives(self):
        """Chips are colon-delimited precisely so they can live in the tables
        that explain what each status means."""
        blocks = parse_blocks(
            '| Chip | Meaning |\n|---|---|\n| {chip:ok:Confirmed} | done |'
        )
        assert blocks[0].rows[0][0][0].kind == 'chip'

    def test_a_blockquote_becomes_a_note(self):
        assert parse_blocks('> heads up')[0].kind == 'note'

    def test_parsing_never_loops_on_unrecognised_input(self):
        assert parse_blocks(':::\n???\n') is not None

    def test_slugify_matches_the_apps_tab_slug_shape(self):
        from theme.base import tab_slug
        for text in ('Signing up as crew', 'On Air', 'Check-in and stations'):
            assert slugify(text) == tab_slug(text)


class TestCatalogue:
    def setup_method(self):
        reload_articles()

    def test_every_shipped_article_parses_to_something(self):
        articles = all_articles()
        assert articles, 'no help articles loaded'
        for article in articles:
            assert article.title, f'{article.slug} has no title'
            assert article.summary, f'{article.slug} has no summary'
            assert article.blocks, f'{article.slug} parsed to nothing'

    def test_every_article_has_at_least_one_top_level_heading(self):
        """The article page builds its 'On this page' nav from these."""
        for article in all_articles():
            assert article.headings, f'{article.slug} has no h2'

    def test_slugs_are_unique(self):
        slugs = [a.slug for a in all_articles()]
        assert len(slugs) == len(set(slugs))

    def test_a_snippet_deep_links_to_the_heading_it_sits_under(self):
        snippet = get_snippet('match-states')
        assert snippet.article_slug == 'schedule-board'
        assert snippet.anchor == 'match-states'
        assert get_article('schedule-board').headings != []

    def test_snippet_content_is_part_of_its_article_not_a_second_copy(self):
        """The popup quotes a region of the article. If the marker leaked into
        the rendered body, or the region were stored separately, the two could
        drift — which is the whole thing this design avoids."""
        article = get_article('schedule-board')
        body = plain_text(list(article.blocks))
        snippet_body = plain_text(list(get_snippet('match-states').blocks))
        assert ':::' not in body
        assert snippet_body[:60] in body

    def test_search_narrows_as_terms_are_added(self):
        """AND over terms, not OR.

        Asserted as a relationship between result sets rather than against a
        fixed slug list — "crew" and "withdraw" both appear in the glossary and
        the notifications article, which is correct and would make an exact
        expectation a trap for the next person who edits the prose.
        """
        def hits(*terms):
            return {
                a.slug for a in all_articles()
                if all(term in a.search_text for term in terms)
            }

        crew, withdraw = hits('crew'), hits('withdraw')
        assert hits('crew', 'withdraw') == crew & withdraw
        assert hits('crew', 'zzzznotaword') == set()

    def test_search_text_is_lowercased_and_markup_free(self):
        for article in all_articles():
            assert article.search_text == article.search_text.lower()
            assert '{chip:' not in article.search_text
            assert '**' not in article.search_text


class TestVocabularyMatchesTheApp:
    """Help that teaches a word the app does not use is a defect, not a typo."""

    def test_the_states_help_explains_are_the_ones_the_board_shows(self):
        assert set(STATE_STYLES) == set(LEGACY_STATE_LABELS.values())

    def test_every_state_style_names_a_class_the_stylesheet_defines(self):
        css = (REPO_ROOT / 'static' / 'css' / 'styles.css').read_text(encoding='utf-8')
        for icon, tone in STATE_STYLES.values():
            assert icon, 'a state with no icon renders as a blank box'
            assert f'.{tone}' in css, f'{tone} has no rule behind it'

    def test_every_chip_tone_has_a_stylesheet_rule(self):
        css = (REPO_ROOT / 'static' / 'css' / 'styles.css').read_text(encoding='utf-8')
        for tone in CHIP_TONES:
            assert f'.wiz-chip--{tone}' in css

    def test_no_article_names_an_unknown_state_or_tone(self):
        """The parser degrades these to plain text, so a typo is invisible on the
        page — this is the only place it surfaces."""
        for path in sorted((REPO_ROOT / 'application' / 'help' / 'content').glob('*.md')):
            text = path.read_text(encoding='utf-8')
            for tone in re.findall(r'\{chip:([a-z-]+):', text):
                assert tone in CHIP_TONES, f'{path.name} uses chip tone {tone!r}'
            for state in re.findall(r'\{state:([^}]+)\}', text):
                assert state in STATE_STYLES, f'{path.name} uses state {state!r}'


class TestPagesAndArticlesAgree:
    """The cross-reference nothing else catches."""

    @staticmethod
    def _referenced_snippets() -> set[str]:
        """Every snippet name a page wires, however it is passed.

        ``help_icon('a', ('b', 'Label'))``, ``resolve_help(['a', ...])``,
        ``section_panel(help_topics=[...])`` and the availability editor's
        ``help_snippet='a'`` all name snippets; a regex on the first argument
        missed the rest of a multi-topic call.
        """
        def names_in(node: ast.AST) -> set[str]:
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return {node.value}
            if isinstance(node, ast.Tuple) and node.elts:
                return names_in(node.elts[0])
            if isinstance(node, (ast.List, ast.Tuple)):
                return set().union(*(names_in(e) for e in node.elts)) if node.elts else set()
            if isinstance(node, ast.IfExp):
                return names_in(node.body) | names_in(node.orelse)
            return set()

        names: set[str] = set()
        for directory in ('pages', 'theme'):
            for path in (REPO_ROOT / directory).rglob('*.py'):
                for call in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                    if not isinstance(call, ast.Call):
                        continue
                    func = call.func
                    called = func.attr if isinstance(func, ast.Attribute) else getattr(func, 'id', '')
                    if called in ('help_icon', 'resolve_help'):
                        for arg in call.args:
                            names |= (
                                set().union(*(names_in(e) for e in arg.elts))
                                if isinstance(arg, ast.List) else names_in(arg)
                            )
                    for keyword in call.keywords:
                        if keyword.arg == 'help_topics' and isinstance(keyword.value, ast.List):
                            for element in keyword.value.elts:
                                names |= names_in(element)
                        elif keyword.arg == 'help_snippet':
                            names |= names_in(keyword.value)
        return {name for name in names if re.fullmatch(r'[a-z0-9-]+', name)}

    def test_every_help_icon_in_the_app_names_a_snippet_that_exists(self):
        """A renamed article silently turns its icons into nothing — the icon
        hides itself rather than raising, which is right at runtime and useless
        for catching the rename.

        Both sections count. ``help_icon`` resolves help first and then the
        community's event handbook, so a snippet that moved between them is still
        wired; one that exists in *neither* is the rename this catches.
        """
        def known(name: str) -> bool:
            if get_snippet(name) is not None:
                return True
            return any(snippet_for_tenant(t, name) is not None for t in tenant_slugs())

        missing = {n for n in self._referenced_snippets() if not known(n)}
        assert not missing, f'help_icon references no such snippet: {sorted(missing)}'

    def test_the_dev_tenant_carries_every_snippet_the_app_wires(self):
        """Otherwise the dev loop and the browser sweep silently lose icons.

        Event-handbook snippets are per tenant, so an icon wired to one only
        opens for a community that ships it. The ``default`` fixture is what the
        dev run and ``/ui-validation`` exercise, so anything the app references
        and help does not own has to exist there too.
        """
        missing = {
            name for name in self._referenced_snippets()
            if get_snippet(name) is None and snippet_for_tenant('default', name) is None
        }
        assert not missing, (
            'the default dev handbook is missing snippets the app wires icons to: '
            f'{sorted(missing)}'
        )

    def test_the_app_actually_uses_help(self):
        assert len(self._referenced_snippets()) >= 8

    def test_internal_article_links_resolve(self):
        slugs = {a.slug for a in all_articles()}
        for path in sorted((REPO_ROOT / 'application' / 'help' / 'content').glob('*.md')):
            text = path.read_text(encoding='utf-8')
            for target in re.findall(r'\]\(/help/([a-z0-9-]+)', text):
                assert target in slugs, f'{path.name} links to missing /help/{target}'

    def test_anchor_links_resolve_to_a_real_heading(self):
        by_slug = {a.slug: {anchor for anchor, _ in a.headings} for a in all_articles()}
        for path in sorted((REPO_ROOT / 'application' / 'help' / 'content').glob('*.md')):
            text = path.read_text(encoding='utf-8')
            for slug, anchor in re.findall(r'\]\(/help/([a-z0-9-]+)#([a-z0-9-]+)\)', text):
                assert anchor in by_slug.get(slug, set()), \
                    f'{path.name} links to /help/{slug}#{anchor}, which has no such heading'


class TestGlossaryOrder:
    """Each term sits in its alphabetical slot, under the heading for its letter.

    That's what lets several help PRs add terms at once: each lands where the
    alphabet puts it, so two PRs only touch the same lines when they add
    neighbouring words, and the merge is "keep both".
    """

    GLOSSARY = REPO_ROOT / 'application' / 'help' / 'content' / 'glossary.md'
    LETTER_RANGE = re.compile(r'^## ([A-Z])–([A-Z])$')
    TERM = re.compile(r'^\*\*(.+?)\*\*:')

    def _sections(self) -> dict[tuple[str, str], list[str]]:
        sections: dict[tuple[str, str], list[str]] = {}
        current = None
        for line in self.GLOSSARY.read_text(encoding='utf-8').splitlines():
            if line.startswith('## '):
                match = self.LETTER_RANGE.match(line)
                current = sections.setdefault((match[1], match[2]), []) if match else None
            elif current is not None and (term := self.TERM.match(line)):
                current.append(term[1])
        return sections

    def test_terms_are_alphabetical_within_each_section(self):
        for (first, last), terms in self._sections().items():
            assert terms == sorted(terms, key=str.casefold), f'{first}–{last} is out of order: {terms}'

    def test_each_term_is_under_its_own_letter(self):
        for (first, last), terms in self._sections().items():
            for term in terms:
                assert first <= term[0].upper() <= last, f'{term!r} belongs outside {first}–{last}'
