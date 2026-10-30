"""Loading and indexing a directory of shipped prose articles.

The mechanics both article sections share: a small front-matter header, optional
named ``:::snippet`` regions, heading anchors, and a parse cache. What differs
between them is only *which directory* — help has one, the event handbook has one
per community — so the loader takes a root and the sections own their roots.

Articles live inside the package rather than at the repo root because
``.dockerignore`` drops ``docs`` and root-level ``*.md`` from the build context:
this is runtime content, not project documentation, and putting it beside the
loader keeps it in the image without a negation rule someone has to remember.

A snippet is not a second copy of the text: it marks a span of the article that a
popup elsewhere in the app also shows, so the popup and the article cannot drift.
Popups deep-link back to the heading the snippet sits under.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from application.content.blocks import Block, parse_blocks, plain_text, slugify
from models import FeatureFlag, Role

logger = logging.getLogger(__name__)

__all__ = [
    'AUDIENCE_EVERYONE',
    'AUDIENCE_STAFF',
    'ContentArticle',
    'ContentCatalog',
    'ContentSnippet',
    'catalog_for',
    'reload_all',
]

_FRONT_MATTER_RE = re.compile(r'^---\s*\n(?P<body>.*?)\n---\s*\n', re.DOTALL)
_SNIPPET_OPEN_RE = re.compile(r'^:::snippet\s+(?P<name>[a-z0-9-]+)\s*$')
_SNIPPET_CLOSE = ':::'

AUDIENCE_EVERYONE = 'everyone'
#: Written for the people who run the community. Listed in its own group and
#: readable only by someone who can open Admin here.
AUDIENCE_STAFF = 'staff'


@dataclass(frozen=True)
class ContentSnippet:
    """A named region of an article, rendered inline in a popup.

    ``anchor`` is the heading the region sits under, so "Read more" lands on the
    part of the article the popup was quoting rather than the top of the page.
    ``base_path`` is the section the article belongs to, because a snippet may
    now come from either section and the popup has to link back to the right one.
    """

    name: str
    article_slug: str
    article_title: str
    anchor: str
    blocks: tuple[Block, ...]
    base_path: str = '/help'
    #: The text of the heading ``anchor`` slugs: what a help menu calls the topic.
    heading: str = ''

    @property
    def href(self) -> str:
        """Where "Read more" goes — the article, deep-linked to the region."""
        target = f'{self.base_path}/{self.article_slug}'
        return f'{target}#{self.anchor}' if self.anchor else target


@dataclass(frozen=True)
class ContentArticle:
    slug: str
    title: str
    summary: str
    icon: str
    order: int
    feature: FeatureFlag | None
    #: Roles that may read this article. Empty means anyone, including signed-out
    #: readers. A reader holding *any* one of them qualifies.
    roles: tuple[Role, ...]
    audience: str
    blocks: tuple[Block, ...]
    snippets: dict[str, ContentSnippet]
    search_text: str

    @property
    def headings(self) -> list[tuple[str, str]]:
        """``(anchor, text)`` for each top-level heading — the in-article nav."""
        return [
            (block.anchor, ''.join(span.text for span in block.spans))
            for block in self.blocks
            if block.kind == 'heading' and block.level == 2
        ]


def _parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    match = _FRONT_MATTER_RE.match(text)
    if not match:
        return {}, text
    meta: dict[str, str] = {}
    for line in match.group('body').splitlines():
        key, _, value = line.partition(':')
        if _:
            meta[key.strip()] = value.strip()
    return meta, text[match.end():]


def _resolve_feature(raw: str) -> FeatureFlag | None:
    """Map a front-matter ``feature:`` name to its flag, or ``None``.

    An unknown name is logged and treated as ungated rather than raising: a typo
    in an article header must not take the section down, and the failure mode
    that hides an article silently is the harder one to notice.
    """
    if not raw:
        return None
    try:
        return FeatureFlag[raw.upper()]
    except KeyError:
        logger.warning('article names unknown feature flag %r; treating as ungated', raw)
        return None


def _resolve_roles(raw: str) -> tuple[Role, ...]:
    """Map a front-matter ``roles:`` list to roles, dropping unknown names.

    Degrades the *closed* way, unlike ``feature:``: a typo drops that name, and
    an article that names roles keeps the ones it got right rather than falling
    open to everyone. An article whose every name is a typo becomes unreadable,
    which is a visible authoring bug; falling open would be an invisible leak.
    """
    names = [part.strip() for part in raw.split(',') if part.strip()]
    roles: list[Role] = []
    for name in names:
        try:
            roles.append(Role[name.upper()])
        except KeyError:
            logger.warning('article names unknown role %r; dropping it', name)
    return tuple(roles)


def _resolve_audience(raw: str) -> str:
    """Map a front-matter ``audience:`` to one of the known audiences.

    Closed, like ``roles:``: a misspelt ``audience: staf`` must not publish a
    staff article to everyone, so any value but ``everyone`` means staff.
    """
    value = raw.strip().lower()
    if not value or value == AUDIENCE_EVERYONE:
        return AUDIENCE_EVERYONE
    if value != AUDIENCE_STAFF:
        logger.warning('article names unknown audience %r; treating as staff', raw)
    return AUDIENCE_STAFF


def _split_snippets(body: str) -> tuple[str, dict[str, tuple[int, int]]]:
    """Strip the ``:::snippet`` markers, returning the body and line ranges.

    The markers are removed so the region renders as ordinary article content;
    the ranges are recorded against the *stripped* line numbering so a snippet
    can be re-parsed from exactly the lines the article shows.
    """
    kept: list[str] = []
    ranges: dict[str, tuple[int, int]] = {}
    open_name: str | None = None
    open_at = 0
    for line in body.splitlines():
        opening = _SNIPPET_OPEN_RE.match(line.strip())
        if opening:
            if open_name is not None:
                logger.warning('snippet %r opened inside %r; ignoring', opening.group('name'), open_name)
                continue
            open_name = opening.group('name')
            open_at = len(kept)
            continue
        if line.strip() == _SNIPPET_CLOSE and open_name is not None:
            ranges[open_name] = (open_at, len(kept))
            open_name = None
            continue
        kept.append(line)
    if open_name is not None:
        logger.warning('snippet %r never closed; taking it to end of article', open_name)
        ranges[open_name] = (open_at, len(kept))
    return '\n'.join(kept), ranges


def _heading_before(lines: list[str], start: int) -> str:
    """The text of the nearest heading at or above ``start``."""
    for index in range(min(start, len(lines)) - 1, -1, -1):
        heading = re.match(r'^#{2,4}\s+(.*)$', lines[index].strip())
        if heading:
            return re.sub(r'[*`]', '', heading.group(1).strip())
    return ''


def _load_article(path: Path, base_path: str) -> ContentArticle | None:
    try:
        raw = path.read_text(encoding='utf-8')
    except OSError:
        logger.exception('could not read article %s', path)
        return None

    meta, body = _parse_front_matter(raw)
    stripped, ranges = _split_snippets(body)
    lines = stripped.splitlines()
    blocks = parse_blocks(stripped)
    slug = meta.get('slug') or path.stem
    title = meta.get('title') or slug.replace('-', ' ').capitalize()

    snippets = {
        name: ContentSnippet(
            name=name,
            article_slug=slug,
            article_title=title,
            anchor=slugify(_heading_before(lines, start)),
            heading=_heading_before(lines, start),
            blocks=tuple(parse_blocks('\n'.join(lines[start:end]))),
            base_path=base_path,
        )
        for name, (start, end) in ranges.items()
    }

    try:
        order = int(meta.get('order', '100'))
    except ValueError:
        order = 100

    return ContentArticle(
        slug=slug,
        title=title,
        summary=meta.get('summary', ''),
        icon=meta.get('icon', 'help_outline'),
        order=order,
        feature=_resolve_feature(meta.get('feature', '')),
        roles=_resolve_roles(meta.get('roles', '')),
        audience=_resolve_audience(meta.get('audience', '')),
        blocks=tuple(blocks),
        snippets=snippets,
        search_text=' '.join([title, meta.get('summary', ''), plain_text(blocks)]).lower(),
    )


class ContentCatalog:
    """The parsed articles under one directory, parsed once and kept.

    A root that does not exist yields **no articles** rather than raising: an
    event handbook is per-community, and a community that has not been given one
    is an empty section, not a broken page.
    """

    def __init__(self, root: Path, base_path: str = '/help') -> None:
        self.root = root
        self.base_path = base_path
        self._articles: list[ContentArticle] | None = None
        self._snippets: dict[str, ContentSnippet] = {}

    def reload(self) -> None:
        """Drop the parsed-article cache. Used by tests and by a dev reload."""
        self._articles = None
        self._snippets = {}

    def articles(self) -> list[ContentArticle]:
        """Every article under this root, ordered for display."""
        if self._articles is None:
            articles = [
                article
                for path in sorted(self.root.glob('*.md'))
                if (article := _load_article(path, self.base_path)) is not None
            ]
            articles.sort(key=lambda a: (a.order, a.title))
            index: dict[str, ContentSnippet] = {}
            for article in articles:
                for name, snippet in article.snippets.items():
                    if name in index:
                        logger.warning(
                            'duplicate snippet %r in %s and %s; keeping the first',
                            name, index[name].article_slug, article.slug,
                        )
                        continue
                    index[name] = snippet
            self._articles = articles
            self._snippets = index
        return list(self._articles)

    def get(self, slug: str) -> ContentArticle | None:
        return next((article for article in self.articles() if article.slug == slug), None)

    def snippet(self, name: str) -> ContentSnippet | None:
        """Look up a snippet by its name within this root. ``None`` when absent."""
        self.articles()
        return self._snippets.get(name)


_catalogs: dict[Path, ContentCatalog] = {}


def catalog_for(root: Path, base_path: str = '/help') -> ContentCatalog:
    """The catalogue for ``root``, shared so each directory parses once.

    Keyed by path rather than instantiated per call: the event handbook resolves
    a root per request from the tenant's slug, and re-parsing a directory on
    every page build would put the whole section's I/O on the request path.
    """
    catalog = _catalogs.get(root)
    if catalog is None:
        catalog = _catalogs[root] = ContentCatalog(root, base_path)
    return catalog


def reload_all() -> None:
    """Drop every catalogue's cache."""
    for catalog in _catalogs.values():
        catalog.reload()
