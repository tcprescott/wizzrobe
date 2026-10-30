"""The cached spectator bracket routes and the cache behind them.

The routes exist to keep thousands of followers off the websocket, so the two
properties worth pinning are the ones that would silently undo that: they must
**not** be NiceGUI pages (a ``@public_page`` would mint a client and a socket),
and a repeat request must be served from the cache rather than re-queried.
"""

import pages.static_brackets as static_brackets
from application.events import Event, EventType
from application.tenant_context import tenant_scope
from application.utils.html_cache import HtmlPageCache
from middleware.auth import _matches_protected_route
from middleware.public_cache import is_public_cacheable_path


class TestHtmlPageCache:
    def test_a_stored_page_comes_back_with_a_stable_etag(self):
        cache = HtmlPageCache()
        entry = cache.put(1, 'bracket:7', '<p>hi</p>')
        again = cache.get(1, 'bracket:7')
        assert again is not None
        assert again.body == '<p>hi</p>'
        assert again.etag == entry.etag
        assert entry.etag.startswith('"') and entry.etag.endswith('"')

    def test_different_bodies_get_different_etags(self):
        cache = HtmlPageCache()
        assert cache.put(1, 'a', 'one').etag != cache.put(1, 'b', 'two').etag

    def test_one_tenants_entry_never_satisfies_anothers_key(self):
        cache = HtmlPageCache()
        cache.put(1, 'bracket:7', 'tenant one')
        assert cache.get(2, 'bracket:7') is None

    def test_invalidating_a_tenant_drops_only_that_tenants_pages(self):
        cache = HtmlPageCache()
        cache.put(1, 'bracket:7', 'one')
        cache.put(2, 'bracket:7', 'two')
        cache.invalidate_tenant(1)
        assert cache.get(1, 'bracket:7') is None
        assert cache.get(2, 'bracket:7') is not None

    def test_a_page_stored_after_the_bump_survives(self):
        """Invalidation is a version bump, so the *next* render must stick."""
        cache = HtmlPageCache()
        cache.put(1, 'bracket:7', 'stale')
        cache.invalidate_tenant(1)
        cache.put(1, 'bracket:7', 'fresh')
        assert cache.get(1, 'bracket:7').body == 'fresh'

    def test_an_entry_expires_on_the_ttl(self):
        # A negative TTL expires every stored entry, testing expiry without sleeping.
        cache = HtmlPageCache(ttl_seconds=-1)
        cache.put(1, 'bracket:7', 'body')
        assert cache.get(1, 'bracket:7') is None

    def test_the_cache_is_bounded(self):
        cache = HtmlPageCache(max_entries=3)
        for i in range(10):
            cache.put(1, f'k{i}', 'x')
        assert len(cache._entries) == 3


class TestEventInvalidation:
    def test_a_bracket_event_invalidates_that_tenants_pages(self):
        static_brackets._cache.put(11, 'bracket:7', 'cached')
        static_brackets._cache.put(12, 'bracket:9', 'other tenant')
        with tenant_scope(11):
            static_brackets._on_change(
                Event.create(EventType.BRACKET_MATCH_COMPLETED, {'bracket_id': 7}),
            )
        assert static_brackets._cache.get(11, 'bracket:7') is None
        assert static_brackets._cache.get(12, 'bracket:9') is not None

    def test_a_tenantless_event_clears_everything(self):
        """Serving a stale bracket is worse than re-rendering a few pages."""
        static_brackets._cache.put(11, 'bracket:7', 'cached')
        static_brackets._on_change(
            Event(event_type=EventType.MATCH_FINISHED, payload={'match_id': 1}),
        )
        assert static_brackets._cache.get(11, 'bracket:7') is None

    def test_the_subscribed_events_cover_both_families(self):
        """Bracket *and* match events — the cards mirror the scheduled match."""
        subscribed = set(static_brackets._INVALIDATING_EVENTS)
        assert EventType.BRACKET_MATCH_COMPLETED in subscribed
        assert EventType.BRACKET_ADVANCED in subscribed
        assert EventType.MATCH_STARTED in subscribed
        assert EventType.MATCH_RESULT_RECORDED in subscribed
        assert EventType.VOLUNTEER_ASSIGNED not in subscribed
        assert EventType.TENANT_MEMBER_ADDED not in subscribed


class TestRoutes:
    def test_the_routes_are_registered_and_anonymous(self):
        static_brackets.create()
        from nicegui import app

        paths = {getattr(r, 'path', None) for r in app.routes}
        assert '/live/brackets/{bracket_id}' in paths
        assert '/live/tournament/{tournament_id}/brackets' in paths
        assert not _matches_protected_route('/live/brackets/7')
        assert not _matches_protected_route('/live/tournament/3/brackets')

    def test_registering_twice_subscribes_once(self):
        """``create()`` is idempotent — a double registration would double-bump."""
        static_brackets.create()
        static_brackets.create()
        assert static_brackets._subscribed is True

    def test_error_responses_are_never_cached(self):
        response = static_brackets._no_store('Not found', 404)
        assert response.status_code == 404
        assert response.headers['cache-control'] == 'no-store'


class TestPublicCachePaths:
    """Which responses get their session cookie stripped.

    ``Cache-Control: public`` and a ``Set-Cookie`` cannot both be right: a shared
    cache storing that pair hands one visitor's session to everyone after them.
    The matcher decides where the cookie comes off, so it has to be exact.
    """

    def test_the_static_bracket_routes_match_with_and_without_a_tenant_prefix(self):
        assert is_public_cacheable_path('/live/brackets/7')
        assert is_public_cacheable_path('/t/demo/live/brackets/7')
        assert is_public_cacheable_path('/live/tournament/3/brackets')
        assert is_public_cacheable_path('/t/demo-two/live/tournament/3/brackets')

    def test_nothing_else_loses_its_cookie(self):
        for path in (
            '/', '/home/schedule', '/admin', '/login',
            '/brackets/7',
            '/tournament/3/brackets',
            '/live/brackets/7/edit',
            '/api/brackets',
            '/_nicegui/main.js',
        ):
            assert not is_public_cacheable_path(path), path


def _endpoint(path: str, method: str = 'GET'):
    from nicegui import app

    static_brackets.create()
    return next(
        r.endpoint for r in app.routes
        if getattr(r, 'path', None) == path and method in getattr(r, 'methods', ())
    )


def _request(path: str, root_path: str = '/t/default'):
    from starlette.requests import Request

    return Request({
        'type': 'http', 'method': 'GET', 'path': path, 'root_path': root_path,
        'query_string': b'', 'headers': [],
    })


class TestNotFound:
    """Every way a spectator link can miss is one HTML 404 with a way home."""

    async def _assert_not_found(self, response):
        assert response.status_code == 404
        assert response.headers['content-type'].startswith('text/html')
        assert response.headers['cache-control'] == 'no-store'
        body = bytes(response.body).decode()
        assert 'Page not found' in body
        assert 'href="/t/default/"' in body

    async def test_a_malformed_or_out_of_range_id(self, db):
        detail = _endpoint('/live/brackets/{bracket_id}')
        index = _endpoint('/live/tournament/{tournament_id}/brackets')
        for raw in ('abc', '99999999999', '-1', '0'):
            await self._assert_not_found(await detail(raw, _request(f'/live/brackets/{raw}')))
            await self._assert_not_found(
                await index(raw, _request(f'/live/tournament/{raw}/brackets')),
            )

    async def test_an_unpublished_tournament_does_not_give_its_name_away(self, db):
        from models import Bracket, BracketFormat, BracketState, Tournament

        tournament = await Tournament.create(name='Secret Invitational')
        await Bracket.create(
            tournament=tournament, name='Main', format=BracketFormat.SINGLE_ELIM,
            state=BracketState.DRAFT,
        )
        index = _endpoint('/live/tournament/{tournament_id}/brackets')
        response = await index(str(tournament.id), _request('/live/x'))
        await self._assert_not_found(response)
        assert 'Secret Invitational' not in bytes(response.body).decode()

    def test_head_is_answered(self):
        """Some link unfurlers probe with HEAD first; a 405 reads as dead."""
        assert _endpoint('/live/brackets/{bracket_id}', 'HEAD')
        assert _endpoint('/live/tournament/{tournament_id}/brackets', 'HEAD')


class TestRootPathCaching:
    """One community is served both on its own domain and under /t/<slug>."""

    async def test_each_root_gets_its_own_links(self, db):
        from models import Bracket, BracketFormat, BracketState, Tournament

        tournament = await Tournament.create(name='Open')
        await Bracket.create(
            tournament=tournament, name='Main', format=BracketFormat.SINGLE_ELIM,
            state=BracketState.ACTIVE,
        )
        index = _endpoint('/live/tournament/{tournament_id}/brackets')
        path = f'/live/tournament/{tournament.id}/brackets'
        prefixed = bytes((await index(str(tournament.id), _request(path))).body).decode()
        bare = bytes((await index(str(tournament.id), _request(path, root_path=''))).body).decode()
        assert 'href="/t/default/live/brackets/' in prefixed
        assert '/t/default' not in bare
