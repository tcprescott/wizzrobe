"""One community's event handbook: resolving a tenant to its article directory.

Where ``application/help`` documents the app — identical for everyone — this
section documents *an event*: when things happen, what to bring, who to find.
That content belongs to one community, so the articles are filed under the
tenant's slug and the loader picks the directory rather than the article.

**This is v1 of a surface meant to end up in the database and be staff-editable.**
Keeping the whole tenant→content resolution behind this one function is what
makes that change small: the service, the flag, the routes, the page and the nav
all sit above it and none of them knows the content is files today. The safe
document model (``application/content``) is already the one editable prose would
need, so that half is done.

A tenant with no directory has **no articles**, not an error — a community that
has just been granted the feature and not yet written anything is an empty
section, which is exactly what its readers should see.

**The directory name is the tenant's `slug`, exactly.** Nothing checks that at
import time, because the slugs live in the database and the directories live in
the repo, so a mismatch cannot fail loudly — it renders the empty state, which is
indistinguishable from "nobody has written anything yet". That is the sharpest
edge of filing content this way, and it has already been hit once (a handbook
filed under ``sgl`` for a tenant whose slug is ``sgl26``). The one-off warning
below is the compensating control: a tenant that reaches this module and has no
directory says so in the log.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Iterable

from application.content import ContentArticle, ContentSnippet, catalog_for

logger = logging.getLogger(__name__)

__all__ = [
    'BASE_PATH',
    'CONTENT_ROOT',
    'article_for_tenant',
    'articles_for_tenant',
    'guide_pdf_for_tenant',
    'handbook_key',
    'snippet_for_tenant',
    'tenant_slugs',
]

CONTENT_ROOT = Path(__file__).parent / 'content'

#: Where this section's articles are served. Stamped onto every snippet so a
#: help popup quoting one links back here rather than into ``/help``.
BASE_PATH = '/event-info'

# A slug reaches this module from the database and is used to build a path, so it
# is constrained to the shape Tenant.slug is validated into rather than trusted.
# Nothing in the app writes a slug with a separator in it; this is here so that
# staying true stops being a matter of trust.
_SAFE_SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9-]*$')


#: Slugs already warned about, so a misconfigured tenant logs once rather than on
#: every page build. Process-wide and not per-user, like the parse caches.
_warned: set[str] = set()


def _root_for(slug: str) -> Path | None:
    if not slug or not all(_SAFE_SLUG_RE.match(part) for part in slug.split('/', 1)):
        return None
    root = CONTENT_ROOT / slug
    if root.is_dir():
        return root
    if slug not in _warned:
        _warned.add(slug)
        logger.warning(
            'event handbook is enabled for tenant %r but no content directory exists at %s; '
            'the section will render empty. The directory name must equal the tenant slug.',
            slug, root,
        )
    return None


def handbook_key(tenant_slug: str, event_slugs: Iterable[str]) -> str:
    """Where a community's handbook is read from: ``<tenant>/<event>`` or ``<tenant>``.

    The first of ``event_slugs`` with a folder of its own inside the community's
    wins, so next year's event reads the community's handbook until a PR adds
    its folder. Event folders sit inside the community's because an event slug
    is unique only within its community: a top-level ``content/<event>/`` could
    hand one community's guide to another's event of the same name.
    """
    if not _SAFE_SLUG_RE.match(tenant_slug or ''):
        return tenant_slug
    for slug in event_slugs:
        if _SAFE_SLUG_RE.match(slug or '') and any((CONTENT_ROOT / tenant_slug / slug).glob('*.md')):
            return f'{tenant_slug}/{slug}'
    return tenant_slug


def articles_for_tenant(slug: str) -> list[ContentArticle]:
    """Every article shipped for ``slug``, ordered for display. Empty if none."""
    root = _root_for(slug)
    return catalog_for(root, BASE_PATH).articles() if root else []


def article_for_tenant(slug: str, article_slug: str) -> ContentArticle | None:
    """One article of ``slug``'s handbook, or ``None``."""
    root = _root_for(slug)
    return catalog_for(root, BASE_PATH).get(article_slug) if root else None


def snippet_for_tenant(slug: str, name: str) -> ContentSnippet | None:
    """One popup snippet from ``slug``'s handbook, unfiltered.

    Unfiltered on purpose — the caller has to re-read the owning article to apply
    the feature and role gates, exactly as the help section does, and a snippet
    carries no gate of its own.
    """
    root = _root_for(slug)
    return catalog_for(root, BASE_PATH).snippet(name) if root else None


def guide_pdf_for_tenant(slug: str) -> Path | None:
    """The organisers' own guide, shipped as a PDF beside the articles, or ``None``.

    One per community: the first ``*.pdf`` in the directory, by name. Its file
    name is what a reader's download is saved as, so name it for them.
    """
    root = _root_for(slug)
    if root is None:
        return None
    return next(iter(sorted(root.glob('*.pdf'))), None)


def tenant_slugs() -> list[str]:
    """Every tenant slug that ships a handbook. For tests and dev tooling."""
    if not CONTENT_ROOT.is_dir():
        return []
    return sorted(path.name for path in CONTENT_ROOT.iterdir() if path.is_dir())
