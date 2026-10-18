"""The health board's per-row route out of the problem it just named.

The tenant board reports Challonge · *Credential warning* · "Token expiring
soon", under a blurb that says to "reconnect before it becomes an outage" — and
offered no link to the tab where reconnecting happens. That is the same finding
the admin-reports audit shipped a fix for, in a place that audit did not reach.

The rule is deliberately narrow, and these tests pin the narrowness: only a row
a *tenant's* staff can actually act on gets a link. A racetime bot is
platform-managed, so a link from a community's board would land them on a page
that cannot fix it.
"""

from datetime import datetime, timezone

from application.services.service_health_service import ProbeResult, ServiceStatus
from pages.admin_tabs.admin_service_health import _route_out
from pages.service_health_view import _CATEGORY_LABEL, _rows


def _result(key, *, status=ServiceStatus.CREDENTIAL_WARNING, category='integrations'):
    return ProbeResult(
        key=key, label=key.title(), category=category, status=status,
        message='Token expiring soon', checked_at=datetime.now(timezone.utc),
    )


class TestRouteOut:
    def test_an_unhealthy_challonge_links_to_integrations(self):
        action = _route_out(_result('challonge'))
        assert action is not None
        text, url = action
        assert 'Challonge' in text
        assert url == '/admin/settings/integrations'

    def test_a_healthy_challonge_offers_nothing(self):
        # Nothing to reconnect; a link here would be an invitation to fix what
        # is not broken.
        assert _route_out(_result('challonge', status=ServiceStatus.HEALTHY)) is None

    def test_a_racetime_bot_offers_nothing_even_when_down(self):
        """Platform-managed: a community's staff cannot fix it, so say nothing.

        A link that lands somewhere unable to help is worse than no link — it
        costs the reader the trip before they learn that.
        """
        assert _route_out(
            _result('racetime_bots', status=ServiceStatus.DOWN, category='racetime')
        ) is None


class TestRowRendering:
    def test_an_action_reaches_the_row_the_template_reads(self):
        rows = _rows([_result('challonge')], _route_out)
        assert rows[0]['action'] == 'Reconnect Challonge under Settings → Integrations'
        assert rows[0]['action_url'] == '/admin/settings/integrations'

    def test_no_action_leaves_both_fields_empty(self):
        # The slot branches on `action_url`, so an empty string must be what a
        # row without a route carries — not a missing key.
        rows = _rows([_result('challonge', status=ServiceStatus.HEALTHY)], _route_out)
        assert rows[0]['action'] == ''
        assert rows[0]['action_url'] == ''

    def test_no_action_for_at_all_still_renders(self):
        rows = _rows([_result('challonge')])
        assert rows[0]['action_url'] == ''

    def test_the_category_column_is_not_a_machine_slug(self):
        rows = _rows([_result('challonge'), _result('racetime_bots', category='racetime')])
        assert [r['category'] for r in rows] == ['Integrations', 'Racetime']

    def test_an_unmapped_category_is_still_readable(self):
        # A probe added later must not print its slug raw while nobody notices.
        rows = _rows([_result('x', category='object_storage')])
        assert rows[0]['category'] == 'Object Storage'
        assert 'object_storage' not in _CATEGORY_LABEL
