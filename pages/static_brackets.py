"""Static, heavily-cached public bracket views — the link you send spectators.

The NiceGUI bracket pages (``pages/brackets.py``) are excellent for the people
*running* a tournament: a match dialog, zoom, and a live repaint driven by the
event bus. They are also a websocket per open tab, on an app that runs a single
worker (docs/scaling-roadmap.md). A finals stream that puts a bracket link in
front of a few thousand viewers is a load problem the interactive page cannot be
made to solve — the socket *is* the feature.

These two routes are the answer: the same bracket, rendered server-side into a
plain HTML document with no client framework and no socket at all.

* ``GET /live/tournament/{tournament_id}/brackets`` — the stage index
* ``GET /live/brackets/{bracket_id}`` — one stage

They are plain FastAPI routes rather than ``@public_page`` because a NiceGUI
page is precisely what is being avoided; everything ``public_page`` does that
still applies is done here explicitly — tenant resolution (from the middleware
that already ran), the ``BRACKETS`` feature gate, and the anonymous-visitor
rule that a DRAFT or CANCELLED stage is not public (``BracketService``'s
viewer-aware reads, called with no viewer). Page-view
telemetry is deliberately **not** recorded: a DB write per spectator request is
the cost this surface exists to avoid, and the interactive pages still report.

Caching is two layers over one render:

* in-process (:class:`HtmlPageCache`), invalidated by ``BRACKET_*``/``MATCH_*``
  events for the tenant so a reported result shows up on the next request, with
  a TTL as the backstop for time-derived state no event announces; and
* HTTP — ``Cache-Control: public`` plus a strong ``ETag``, so a reader whose page
  reloaded itself on the meta-refresh timer usually gets a 304 with no body, and
  any CDN in front of the app can serve the burst without touching the app at
  all. The responses depend on nothing per-visitor (no session read, no
  ``Set-Cookie``), which is what makes ``public`` safe here.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from nicegui import app

from application.events import EventType, event_bus
from application.services import BracketService
from application.services.feature_flag_service import FeatureFlagService
from application.services.tenant_theme_service import TenantThemeService
from application.services.timezone_service import TimezoneService
from application.tenant_context import get_current_tenant_id
from application.timezone_context import tz_scope
from application.utils.html_cache import HtmlPageCache
from application.utils.route_params import parse_route_id
from models import Bracket, BracketFormat, FeatureFlag, Tournament
from theme.brackets import entry_avatars
from theme.brackets.static_view import (
    StaticBracketView,
    StaticIndexView,
    render_bracket_document,
    render_index_document,
    render_not_found_document,
)

logger = logging.getLogger(__name__)

# How long an un-invalidated render may be served, and how long a browser (or a
# CDN) may reuse a response. Both are short: the events do the real freshness
# work, and these only bound the state nothing publishes — a card whose derived
# status turns "imminent" because the clock moved.
_CACHE_TTL_SECONDS = 30
_HTTP_MAX_AGE = 30
# The document's own meta-refresh. Longer than the HTTP max-age so a reload
# always revalidates rather than repainting the same cached bytes; short enough
# that a spectator watching finals sees a result land without touching anything.
_PAGE_REFRESH_SECONDS = 60
_INDEX_REFRESH_SECONDS = 300

_cache = HtmlPageCache(ttl_seconds=_CACHE_TTL_SECONDS)

# The two event families that can change what these pages show — the same split
# the live view subscribes to (theme/brackets/live.py): BRACKET_* for the stage
# itself, MATCH_* because the cards mirror the scheduled match's live state.
# Matched by *tenant*, not by bracket id: invalidation is a version bump, so
# being generous costs one re-render of pages nobody may even be reading, and
# the alternative (resolving a bracket id inside a sync subscriber) would mean a
# query inside ``publish``.
_INVALIDATING_EVENTS = tuple(
    name for name in EventType.ALL
    if name.startswith('bracket.') or name.startswith('match.')
)

_subscribed = False


def _on_change(event) -> None:
    # A tenant-less event should not happen (every scoped path publishes inside
    # ``tenant_scope``), but if one does, serving a stale bracket is the worse
    # failure of the two — drop everything rather than guess which tenant.
    if event.tenant_id is None:
        _cache.clear()
        return
    _cache.invalidate_tenant(event.tenant_id)


def _no_store(body: str, status_code: int) -> PlainTextResponse:
    """An error response a cache must never keep — the condition is transient."""
    return PlainTextResponse(
        body, status_code=status_code, headers={'Cache-Control': 'no-store'},
    )


async def _not_found(request: Request) -> Response:
    """The static 404: an HTML page with a way home, never cached.

    Same words as the app's own not-found page, and the same answer for every
    reason — no such id, a draft or withdrawn stage, a tournament with nothing
    published — so a spectator's stale link reads as one thing and walking ids
    teaches nothing.
    """
    from application.services import TenantService

    try:
        community = await TenantService.current_community_name() or ''
    except Exception:
        community = ''
    theme = await _theme()
    return HTMLResponse(
        render_not_found_document(
            root_path=request.scope.get('root_path', '') or '',
            community=community,
            primary_color=theme.get('primary'),
            icons=theme,
        ),
        status_code=404,
        headers={'Cache-Control': 'no-store', 'X-Robots-Tag': 'noindex, nofollow'},
    )


def _respond(request: Request, tenant_id: Optional[int], key: str, body: str) -> Response:
    """Serve ``body`` with validators, or a 304 when the reader already has it."""
    entry = _cache.put(tenant_id, key, body)
    return _conditional(request, entry.etag, entry.body)


def _conditional(request: Request, etag: str, body: str) -> Response:
    headers = {
        'Cache-Control': f'public, max-age={_HTTP_MAX_AGE}, stale-while-revalidate=300',
        'ETag': etag,
        # The tenant is resolved from the path (or the Host), never from a
        # cookie, so a shared cache keying on the URL alone is correct.
        'Vary': 'Accept-Encoding',
        'X-Robots-Tag': 'noindex, nofollow',
    }
    if etag in [t.strip() for t in (request.headers.get('if-none-match') or '').split(',')]:
        return Response(status_code=304, headers=headers)
    return HTMLResponse(body, headers=headers)


async def _theme() -> dict[str, str]:
    """The tenant's palette and logo icons, so the static view is not visibly off-brand."""
    try:
        return await TenantThemeService.get_current_theme()
    except Exception:
        logger.debug('Static bracket view: falling back to the default palette', exc_info=True)
        return {}


async def _next_stage_advancement(
    service: BracketService, bracket: Bracket,
) -> Optional[dict]:
    """The next stage's advancement rule — drives the cut line on group/Swiss tables."""
    brackets = await service.list_brackets(bracket.tournament_id)
    nxt = next((b for b in brackets if b.stage_order == bracket.stage_order + 1), None)
    if nxt is None or not nxt.config:
        return None
    return (nxt.config or {}).get('advancement')


def create() -> None:
    """Register the static bracket routes and their cache invalidation."""
    global _subscribed
    if not _subscribed:
        event_bus.subscribe_sync(_on_change, _INVALIDATING_EVENTS)
        _subscribed = True

    # GET and HEAD: a link unfurler (Discord's among them) may probe with HEAD
    # first, and a 405 there reads as a dead link.
    @app.api_route(
        '/live/tournament/{tournament_id}/brackets', methods=['GET', 'HEAD'],
        include_in_schema=False,
    )
    async def static_bracket_index(tournament_id: str, request: Request) -> Response:
        tenant_id = get_current_tenant_id()
        if tenant_id is None:
            return _no_store('Not found', 404)
        if not await FeatureFlagService().is_enabled(FeatureFlag.BRACKETS):
            return await _not_found(request)
        tid = parse_route_id(tournament_id)
        if tid is None:
            return await _not_found(request)

        root = request.scope.get('root_path', '') or ''
        key = f'index:{tid}:{root}'
        cached = _cache.get(tenant_id, key)
        if cached is not None:
            return _conditional(request, cached.etag, cached.body)

        service = BracketService()
        # Anonymous always: no viewer, so a tournament with nothing published
        # (its name included) and every DRAFT/CANCELLED stage stay hidden.
        tournament = await service.get_visible_tournament(None, tid)
        if tournament is None:
            return await _not_found(request)

        # One render is cached and served to every spectator, so it cannot carry
        # any one viewer's clock — and these routes never run the page-build
        # resolver anyway. The community's own zone is the only correct answer.
        tz = await TimezoneService.tenant_timezone_name(tenant_id)

        brackets = await service.list_visible_brackets(None, tid)
        theme = await _theme()
        with tz_scope(tz):
            body = render_index_document(StaticIndexView(
                tournament_id=tid,
                tournament_name=tournament.name,
                brackets=brackets,
                generated_at=datetime.now(timezone.utc),
                root_path=root,
                primary_color=theme.get('primary'),
                icons=theme,
                refresh_seconds=_INDEX_REFRESH_SECONDS,
            ))
        return _respond(request, tenant_id, key, body)

    @app.api_route(
        '/live/brackets/{bracket_id}', methods=['GET', 'HEAD'], include_in_schema=False,
    )
    async def static_bracket_detail(bracket_id: str, request: Request) -> Response:
        tenant_id = get_current_tenant_id()
        if tenant_id is None:
            return _no_store('Not found', 404)
        if not await FeatureFlagService().is_enabled(FeatureFlag.BRACKETS):
            return await _not_found(request)
        stage_id = parse_route_id(bracket_id)
        if stage_id is None:
            return await _not_found(request)

        root = request.scope.get('root_path', '') or ''
        key = f'bracket:{stage_id}:{root}'
        cached = _cache.get(tenant_id, key)
        if cached is not None:
            return _conditional(request, cached.etag, cached.body)

        tz = await TimezoneService.tenant_timezone_name(tenant_id)

        service = BracketService()
        bracket = await service.get_visible_bracket(None, stage_id)
        if bracket is None:
            return await _not_found(request)

        tournament = await Tournament.get_or_none(
            id=bracket.tournament_id, tenant_id=tenant_id,
        )
        entrants = await service.list_entrants(bracket.tournament_id)
        entries = await service.list_entries(stage_id)
        matches = await service.list_matches(stage_id)
        live_state = await service.matchup_live_state(matches)
        advancement = (
            None
            if bracket.format in (BracketFormat.SINGLE_ELIM, BracketFormat.DOUBLE_ELIM)
            else await _next_stage_advancement(service, bracket)
        )

        entrant_name = {en.id: en.display_name for en in entrants}
        theme = await _theme()
        with tz_scope(tz):
            body = render_bracket_document(StaticBracketView(
                bracket=bracket,
                tournament_name=tournament.name if tournament else 'Tournament',
                entries=entries,
                matches=matches,
                entry_name={
                    e.id: entrant_name.get(e.entrant_id, 'Unknown') for e in entries
                },
                entry_avatar=entry_avatars(entrants, entries),
                live_state=live_state,
                advancement=advancement,
                generated_at=datetime.now(timezone.utc),
                root_path=root,
                primary_color=theme.get('primary'),
                icons=theme,
                refresh_seconds=_PAGE_REFRESH_SECONDS,
            ))
        return _respond(request, tenant_id, key, body)
