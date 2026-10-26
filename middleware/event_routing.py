"""Keep a page's URL naming the event it shows.

``TenantMiddleware`` strips ``/e/<slug>`` into ``root_path`` and binds that
event; a path without one binds the community's default event. These helpers
are the page side of that:

* an **event page** reached by a bare link redirects (302) to the default
  event's ``/e/`` form of the same path and query, so what the reader shares or
  bookmarks keeps pointing at this event after the next one goes live. 302, not
  301: the default changes every year.
* a **row page** (a bracket, a check-in event, a meetup) redirects to the
  ``/e/`` form of *its row's* event when the URL names another, or none: a row
  decides its own event, so last year's bracket opens in last year's event.
* a **community page** (Members, Settings, Help) never redirects.

Cross-event links have to be built here rather than with ``ui.navigate.to``,
which prefixes every root-relative path with the current page's ``root_path``,
the event included.
"""

from typing import Literal, Optional
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import RedirectResponse

from application.services.event_service import EventService
from application.tenant_context import get_current_tenant_id
from middleware.tenant import COMMUNITY_ROOT_SCOPE_KEY, EVENT_SLUG_SCOPE_KEY

#: How a page relates to events: shows one (``event``), shows one row that
#: decides its own (``row``), or belongs to the whole community (``community``).
PageScope = Literal['event', 'row', 'community']

#: Where the page decorator records the page's scope on the request, for the
#: header's event switcher (a row page switches to the other event's home).
PAGE_SCOPE_KEY = 'wizzrobe.page_scope'


def page_request() -> Optional[Request]:
    """The request the page being built answers, or None outside one (a test, a handler)."""
    from nicegui import context

    try:
        return context.client.request
    except (AttributeError, RuntimeError):
        return None


def url_event_slug(request: Optional[Request]) -> Optional[str]:
    """The event slug the URL named, or None for a bare link."""
    if request is None:
        return None
    return request.scope.get(EVENT_SLUG_SCOPE_KEY)


def community_root(request: Request) -> str:
    """``root_path`` up to the community: ``/t/<slug>``, or ``''`` on its own domain."""
    return request.scope.get(COMMUNITY_ROOT_SCOPE_KEY, request.scope.get('root_path', ''))


def local_url(request: Request) -> str:
    """The page's path and query below the community and event prefixes."""
    url = request.url
    return f'{url.path}?{url.query}' if url.query else url.path


def event_href(request: Request, slug: str, path: str = '/') -> str:
    """The browser-ready path of ``path`` in event ``slug`` of this community."""
    forwarded = quote(request.headers.get('X-Forwarded-Prefix', ''), safe="/:@!$&'()*+,;=%")
    return f'{forwarded}{community_root(request)}/e/{slug}{path}'


def _redirect(request: Request, slug: str) -> RedirectResponse:
    return RedirectResponse(event_href(request, slug, local_url(request)), status_code=302)


async def default_event_redirect(request: Optional[Request]) -> Optional[RedirectResponse]:
    """A bare link to an event page → the same page in the default event."""
    if request is None or url_event_slug(request) is not None:
        return None
    tenant_id = get_current_tenant_id()
    if tenant_id is None:
        return None
    default = await EventService.default_event(tenant_id)
    return _redirect(request, default.slug) if default else None


async def row_event_redirect(request: Optional[Request], event_id: Optional[int]) -> Optional[RedirectResponse]:
    """A row page whose URL doesn't name the row's own event → the row's event."""
    tenant_id = get_current_tenant_id()
    if request is None or tenant_id is None or event_id is None:
        return None
    event = await EventService.ref(tenant_id, event_id)
    if event is None or url_event_slug(request) == event.slug:
        return None
    return _redirect(request, event.slug)
