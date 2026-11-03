"""Coming back to the page you signed in from — and never to anywhere else.

A Discord DM's button is usually opened on a phone with no session. The reader
lands on the join door (home) or is bounced to ``/login`` (a protected page), and
the post-login target is what decides whether the DM's "open this dialog" ever
happens. Two ways in carry it: the ``?next=`` a sign-in button adds, and the
``referrer_path`` ``AuthMiddleware`` stores. Both must keep the query string,
and both must refuse anything a browser could resolve to another origin —
``next`` is attacker-controlled, so it is an open redirect if it is not.
"""

import pytest

from application.utils.tenant_urls import (
    login_path,
    return_path_for_login,
    safe_local_path,
    safe_next,
    sanitize_return_path,
)
from middleware.auth import referrer_for


class TestSafeLocalPath:
    @pytest.mark.parametrize('path', [
        '/',
        '/home/my-schedule?match=1',
        '/admin/schedule?match_id=1&x=%2F',
        '/brackets/1',
        '/help/crew',
    ])
    def test_a_plain_same_origin_path_is_kept(self, path):
        assert safe_local_path(path) == path

    @pytest.mark.parametrize('path', [
        'https://evil.example/',
        'http://evil.example',
        '//evil.example/x',
        '/\\evil.example',
        '\\\\evil.example',
        '/%2F%2Fevil.example',
        'javascript:alert(1)',
        'evil.example/x',
        'login',
        'home/schedule',
        '/home\r\nLocation: https://evil.example',
        '/home\t',
        '/ home',
        '/../t/other/admin',
        '/home/../../t/other',
        '/%2e%2e/t/other',
        '/.%2E/t/other',
        '/login',
        '/login?next=/x',
        '/logout',
        '/oauth/callback',
        None,
        '',
        12345,
        '/' + 'a' * 5000,
    ])
    def test_anything_else_is_refused(self, path):
        assert safe_local_path(path) is None


class TestLoginPath:
    def test_carries_the_page_and_its_query(self):
        assert login_path('/home/my-schedule?match=1') == \
            '/login?next=%2Fhome%2Fmy-schedule%3Fmatch%3D1'

    def test_home_needs_no_next(self):
        assert login_path('/') == '/login'

    def test_an_unsafe_page_is_dropped_not_carried(self):
        assert login_path('//evil.example') == '/login'
        assert login_path(None) == '/login'

    def test_is_absolute_so_it_cannot_resolve_against_a_section(self):
        """The join door's button used the relative ``'login'``, which from
        ``/home/schedule`` resolved to ``/home/login`` — the door again."""
        assert login_path('/home/schedule').startswith('/login')


class TestReturnPathForLogin:
    def test_next_wins_and_is_tenant_qualified(self):
        assert return_path_for_login('/t/sgl', '/brackets/1', '/t/sgl/admin') == '/t/sgl/brackets/1'

    def test_next_keeps_its_query(self):
        assert return_path_for_login('/t/sgl', '/home/my-schedule?match=1', None) == \
            '/t/sgl/home/my-schedule?match=1'

    def test_host_mode_has_no_prefix(self):
        assert return_path_for_login('', '/brackets/1', None) == '/brackets/1'

    @pytest.mark.parametrize('evil', [
        '//evil.example', 'https://evil.example', '/\\evil.example', '/../t/other/admin',
    ])
    def test_an_unsafe_next_falls_back_and_never_leaves(self, evil):
        assert return_path_for_login('/t/sgl', evil, None) == '/t/sgl/'
        assert return_path_for_login('', evil, None) == '/'

    def test_an_unsafe_next_falls_back_to_the_stored_referrer(self):
        assert return_path_for_login('/t/sgl', '//evil.example', '/t/sgl/admin') == '/t/sgl/admin'

    def test_without_next_the_referrer_rules_apply(self):
        assert return_path_for_login('/t/sgl', None, '/t/other/admin') == '/t/sgl/'


class TestStoredReferrer:
    def test_the_middleware_keeps_the_query(self):
        """``/admin/schedule?match_id=1`` came back as ``/admin/schedule``."""
        assert referrer_for('/t/sgl', '/admin/schedule', 'match_id=1') == \
            '/t/sgl/admin/schedule?match_id=1'

    def test_no_query_adds_no_question_mark(self):
        assert referrer_for('/t/sgl', '/admin', '') == '/t/sgl/admin'

    def test_a_queried_referrer_survives_sanitising(self):
        stored = referrer_for('/t/sgl', '/admin/schedule', 'match_id=1')
        assert sanitize_return_path('/t/sgl', stored) == stored

    def test_a_protocol_relative_referrer_is_refused_in_host_mode(self):
        """Host mode has no prefix to anchor on, so the shape check is all there is."""
        assert sanitize_return_path('', '//evil.example/x') == '/'


class TestOneGate:
    """The cross-host handoffs' ``safe_next`` is the same gate with a ``/``
    fallback, so a ``next`` carried across hosts is never looser than one that
    stays on this host."""

    @pytest.mark.parametrize('path', [
        '//evil.example', '/%2F%2Fevil.example', '/../t/other', '/%2e%2e/t/other', '/login',
    ])
    def test_handoff_next_refuses_what_login_refuses(self, path):
        assert safe_local_path(path) is None
        assert safe_next(path) == '/'

    def test_a_plain_path_survives_the_handoff(self):
        assert safe_next('/admin/schedule?match_id=1') == '/admin/schedule?match_id=1'
