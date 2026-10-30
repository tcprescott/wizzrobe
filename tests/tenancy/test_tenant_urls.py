"""Pure tenant-URL path helpers (``application/utils/tenant_urls.py``).

These guard the path-mode return-target logic: a shared-cookie login must land
back inside the *originating* tenant, never a different community whose stale
referrer is still in the session.
"""

from application.utils.tenant_urls import (
    encoded_host_mismatch,
    sanitize_return_path,
    strip_root_path,
    tenant_home,
)


class TestTenantHome:
    def test_path_mode(self):
        assert tenant_home('/t/sgl') == '/t/sgl/'

    def test_bare_platform_host(self):
        assert tenant_home('') == '/'


class TestStripRootPath:
    """The client-side navigate contract.

    ``nicegui.js`` prepends the client's ``options.prefix`` to every absolute
    path handed to ``ui.navigate.to``::

        open: (msg) => { const url = msg.path.startsWith("/") ? options.prefix + msg.path : msg.path; ... }

    That is the framework's behaviour, not a bug to route around: most of this
    codebase relies on it (see the comments in ``theme/dialog/qr_label_dialog.py``
    and ``theme/tables/match_slots.py``). The fix for a doubled prefix is to hand
    it a **tenant-local** path — never to re-add the prefix here. If a future
    reader "fixes" this helper by prepending ``root_path``, these tests fail.
    """

    def test_strips_the_prefix_in_path_mode(self):
        assert strip_root_path('/t/sgl', '/t/sgl/equipment/3') == '/equipment/3'

    def test_never_returns_a_path_that_still_carries_the_prefix(self):
        for path in ('/t/sgl', '/t/sgl/', '/t/sgl/equipment/3'):
            assert not strip_root_path('/t/sgl', path).startswith('/t/sgl')

    def test_tenant_root_becomes_the_local_root(self):
        assert strip_root_path('/t/sgl', '/t/sgl') == '/'
        assert strip_root_path('/t/sgl', '/t/sgl/') == '/'

    def test_keeps_the_query_string(self):
        assert strip_root_path('/t/sgl', '/t/sgl/admin?tab=equipment') == '/admin?tab=equipment'

    def test_host_mode_passes_through_untouched(self):
        assert strip_root_path('', '/equipment/3') == '/equipment/3'

    def test_a_path_outside_the_prefix_is_left_alone(self):
        assert strip_root_path('/t/sgl', '/t/other/equipment/3') == '/t/other/equipment/3'

    def test_a_prefix_lookalike_is_not_stripped(self):
        assert strip_root_path('/t/sgl', '/t/sglother/admin') == '/t/sglother/admin'

    def test_degenerate_inputs_fall_back_to_the_local_root(self):
        assert strip_root_path('/t/sgl', '') == '/'
        assert strip_root_path('/t/sgl', None) == '/'
        assert strip_root_path('/t/sgl', 12345) == '/'

    def test_a_missing_root_path_is_tolerated(self):
        assert strip_root_path(None, '/equipment/3') == '/equipment/3'


class TestSanitizeReturnPath:
    def test_honors_referrer_within_same_tenant(self):
        assert sanitize_return_path('/t/sgl', '/t/sgl/admin') == '/t/sgl/admin'

    def test_honors_referrer_with_query_within_tenant(self):
        assert sanitize_return_path('/t/sgl', '/t/sgl/admin/reports?report=capacity') == '/t/sgl/admin/reports?report=capacity'

    def test_rejects_referrer_from_other_tenant(self):
        assert sanitize_return_path('/t/sgl', '/t/other/admin') == '/t/sgl/'

    def test_rejects_referrer_that_is_a_prefix_lookalike(self):
        assert sanitize_return_path('/t/sgl', '/t/sglother/admin') == '/t/sgl/'

    def test_rejects_auth_route_referrer(self):
        assert sanitize_return_path('/t/sgl', '/t/sgl/login') == '/t/sgl/'
        assert sanitize_return_path('/t/sgl', '/t/sgl/oauth/callback') == '/t/sgl/'

    def test_none_referrer_falls_back_to_home(self):
        assert sanitize_return_path('/t/sgl', None) == '/t/sgl/'

    def test_empty_referrer_falls_back_to_home(self):
        assert sanitize_return_path('/t/sgl', '') == '/t/sgl/'

    def test_non_string_referrer_falls_back_to_home(self):
        assert sanitize_return_path('/t/sgl', 12345) == '/t/sgl/'

    def test_bare_host_honors_tenant_qualified_referrer(self):
        assert sanitize_return_path('', '/t/sgl/admin') == '/t/sgl/admin'

    def test_bare_host_rejects_auth_route(self):
        assert sanitize_return_path('', '/login') == '/'

    def test_tenant_root_exact_match_is_accepted(self):
        assert sanitize_return_path('/t/sgl', '/t/sgl') == '/t/sgl'


class TestEncodedHostMismatch:
    """The guard that catches a sheet of labels printed against a stale BASE_URL.

    The subtlety a naive check gets wrong: on a custom-domain tenant the encoded
    host is *supposed* to differ from BASE_URL. The comparison is against the
    tenant's own canonical base, so a custom-domain community printing from its
    own domain is silent.
    """

    def test_matching_hosts_are_silent(self):
        assert encoded_host_mismatch('http://localhost:8000/t/sgl/equipment/3',
                                     'localhost:8000') is None

    def test_a_stale_base_url_is_named(self):
        assert encoded_host_mismatch('https://wrong.example/t/sgl/equipment/3',
                                     'localhost:8000') == 'wrong.example'

    def test_a_custom_domain_browsed_on_itself_is_silent(self):
        assert encoded_host_mismatch('https://foo.gg/equipment/3', 'foo.gg') is None

    def test_a_custom_domain_browsed_elsewhere_is_named(self):
        assert encoded_host_mismatch('https://foo.gg/equipment/3',
                                     'localhost:8000') == 'foo.gg'

    def test_default_ports_do_not_count_as_a_mismatch(self):
        assert encoded_host_mismatch('https://foo.gg:443/equipment/3', 'foo.gg') is None
        assert encoded_host_mismatch('http://foo.gg/equipment/3', 'foo.gg:80') is None

    def test_a_non_default_port_still_counts(self):
        assert encoded_host_mismatch('http://foo.gg:8000/x', 'foo.gg') == 'foo.gg:8000'

    def test_case_and_trailing_dot_are_not_a_mismatch(self):
        assert encoded_host_mismatch('https://FOO.GG/equipment/3', 'foo.gg.') is None

    def test_a_missing_host_never_warns(self):
        assert encoded_host_mismatch('https://foo.gg/x', None) is None
        assert encoded_host_mismatch('https://foo.gg/x', '') is None
        assert encoded_host_mismatch(None, 'foo.gg') is None
        assert encoded_host_mismatch('', 'foo.gg') is None

    def test_a_malformed_host_never_warns(self):
        assert encoded_host_mismatch('https://foo.gg/x', 'not a host') is None
