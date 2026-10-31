"""The in-app help section's own article directory.

The loading, snippet-splitting and caching all live in
``application/content/catalog.py``, which the event handbook shares. What is
help-specific is only the root and the module-level convenience functions the
rest of the app already calls, so that is all this module is.
"""

from __future__ import annotations

from pathlib import Path

from application.content import ContentArticle, ContentSnippet, catalog_for

__all__ = [
    'HelpArticle',
    'HelpSnippet',
    'all_articles',
    'get_article',
    'get_snippet',
    'reload_articles',
]

CONTENT_DIR = Path(__file__).parent / 'content'

HelpArticle = ContentArticle
HelpSnippet = ContentSnippet


def _catalog():
    return catalog_for(CONTENT_DIR)


def reload_articles() -> None:
    """Drop the parsed-article cache. Used by tests and by a dev reload."""
    _catalog().reload()


def all_articles() -> list[HelpArticle]:
    """Every shipped help article, ordered for display. Parsed once per process."""
    return _catalog().articles()


def get_article(slug: str) -> HelpArticle | None:
    return _catalog().get(slug)


def get_snippet(name: str) -> HelpSnippet | None:
    """Look up a snippet by its global name. ``None`` when no article defines it."""
    return _catalog().snippet(name)
