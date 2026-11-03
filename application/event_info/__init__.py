"""Per-community event information: the shipped handbook for one tenant's event.

The counterpart to ``application/help``. Help explains **the app** and reads the
same for every community; this explains **the event** — when things happen, what
to bring on the day, who to find when something goes wrong — and belongs to one
community. Keeping them apart is the point: event logistics in the help articles
would go stale on a different clock and dilute what help is for.

A peer of ``application/services``: no database, no rules, just the tenant→
directory resolution. Which articles a reader may see is a rule and lives in
``application.services.EventInfoService``.
"""

from application.event_info.catalog import (
    BASE_PATH,
    CONTENT_ROOT,
    article_for_tenant,
    articles_for_tenant,
    guide_pdf_for_tenant,
    handbook_key,
    snippet_for_tenant,
    tenant_slugs,
)

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
