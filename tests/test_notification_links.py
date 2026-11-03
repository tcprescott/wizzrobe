"""The call-to-action targets on Discord DMs.

A notification that asks for something and then leaves the reader to go find the
control is the failure mode these guard against. Three distinct things get
checked: the paths are the ones the pages actually serve, the builders survive a
tenant they cannot resolve (a DM must never be lost over its button), and the
matchup-ready link points at a surface an *entrant* can act on.
"""

from unittest.mock import AsyncMock, patch

import pytest

from application.services import notification_links as links
from application.utils import app_links
from application.utils.discord_messages import DMLink


class TestPaths:
    """The bare paths, which must match what the pages register."""

    def test_the_player_schedule_path_carries_the_matchup_id(self):
        assert app_links.player_schedule_url(42) == '/home/player?schedule=42'

    def test_the_admin_board_filters_to_one_match(self):
        assert app_links.admin_url(app_links.SCHEDULE, match_id=7) == \
            '/admin/match-schedule?match_id=7'

    def test_empty_params_are_dropped_rather_than_sent_blank(self):
        """A shift with no start must not produce ``?day=``.

        The board reads an empty ``day`` as a filter, not as its absence, and
        would show nothing at all.
        """
        assert app_links.admin_url(app_links.VOL_SCHEDULE, day=None) == \
            '/admin/volunteer-shifts'

    def test_a_date_is_normalised_to_iso(self):
        from datetime import date
        assert app_links.admin_url(app_links.VOL_SCHEDULE, day=date(2026, 3, 4)) == \
            '/admin/volunteer-shifts?day=2026-03-04'


class TestLinkBuilding:
    @pytest.fixture(autouse=True)
    def _event(self):
        with patch.object(links, 'event_slug', AsyncMock(return_value='sgl26')):
            yield

    async def test_a_link_is_absolute_and_tenant_qualified(self):
        """A relative path is meaningless in a DM, which has no request context.

        The link names the event it was sent from, so the page opens in that
        event's scope even after the community's default moves on.
        """
        tenant = type('T', (), {'slug': 'acme', 'domain': None})()
        with patch.object(links, '_current_tenant', AsyncMock(return_value=tenant)):
            link = await links.player_schedule(9)
        assert link is not None
        assert link.label == 'Pick a time'
        assert link.url.endswith('/t/acme/e/sgl26/home/player?schedule=9')
        assert link.url.startswith('http')

    async def test_a_custom_domain_wins_over_the_platform_prefix(self):
        tenant = type('T', (), {'slug': 'acme', 'domain': 'play.acme.gg'})()
        with patch.object(links, '_current_tenant', AsyncMock(return_value=tenant)):
            link = await links.admin_users()
        assert link == DMLink('Review the request', 'https://play.acme.gg/e/sgl26/admin/members')

    async def test_no_tenant_yields_no_link_rather_than_a_broken_one(self):
        with patch.object(links, '_current_tenant', AsyncMock(return_value=None)):
            assert await links.home_tournaments() is None

    async def test_crew_and_agree_targets_land_on_their_controls(self):
        tenant = type('T', (), {'slug': 'acme', 'domain': None})()
        with patch.object(links, '_current_tenant', AsyncMock(return_value=tenant)):
            crew = await links.crew_match(4)
            agree = await links.player_agree(11)
        assert crew.url.endswith('/t/acme/e/sgl26/home/my-crew?match=4')
        assert agree.url.endswith('/t/acme/e/sgl26/home/player?agree=11')

    async def test_a_failed_lookup_costs_the_button_not_the_dm(self):
        """Every caller is a best-effort notifier that swallows Discord failures.

        A builder that raised here would abort the send loop above it, and the
        recipients would lose the message entirely over a decoration on it.
        """
        with patch.object(links, '_current_tenant', AsyncMock(side_effect=RuntimeError('db down'))):
            assert await links.admin_match(3) is None

    async def test_the_rebook_variant_relabels_the_button(self):
        tenant = type('T', (), {'slug': 'acme', 'domain': None})()
        with patch.object(links, '_current_tenant', AsyncMock(return_value=tenant)):
            link = await links.player_schedule(9, label='Pick a new time')
        assert link.label == 'Pick a new time'

    def test_link_for_tenant_needs_no_ambient_scope(self):
        """The qualifier worker holds the row already; re-resolving it is a query."""
        tenant = type('T', (), {'slug': 'acme', 'domain': None})()
        link = links.link_for_tenant(tenant, 'Submit or forfeit', '/qualifiers/4')
        assert link.url.endswith('/t/acme/qualifiers/4')

    def test_a_qualifier_verdict_lands_on_the_runners_own_runs(self):
        """The leaderboard is hidden from players until close, so it is not the target."""
        tenant = type('T', (), {'slug': 'acme', 'domain': None})()
        link = links.player_qualifier_runs(tenant, 4)
        assert link.label == 'See your runs'
        assert link.url.endswith('/t/acme/qualifiers/4?focus=runs')
        assert links.player_qualifier(tenant, 4, label='Submit or forfeit').url.endswith(
            '/t/acme/qualifiers/4')

    def test_link_for_tenant_tolerates_a_missing_tenant(self):
        assert links.link_for_tenant(None, 'x', '/y') is None


class TestMatchupReadyPointsWhereAnEntrantCanAct:
    """The regression this whole change exists for.

    ``notify_matchup_ready`` used to link to ``/brackets/<id>``. That page's
    Schedule button is ``is_staff``-gated, so the two players the DM addressed
    arrived at a dialog whose only button was Close — the one message in the app
    whose entire purpose is "go book this" ended nowhere.
    """

    async def test_the_button_opens_the_schedule_dialog_not_the_bracket_page(self):
        from application.services._bracket.notifications import BracketNotificationMixin

        tenant = type('T', (), {'slug': 'acme', 'domain': None})()
        with patch.object(links, '_current_tenant', AsyncMock(return_value=tenant)):
            link = await BracketNotificationMixin._schedule_link(77)

        assert '/brackets/' not in link.url
        assert link.url.endswith('/home/player?schedule=77')

    async def test_it_is_keyed_on_the_matchup_not_the_bracket(self):
        """One bracket holds many matchups; the id has to select the right dialog."""
        from application.services._bracket.notifications import BracketNotificationMixin

        tenant = type('T', (), {'slug': 'acme', 'domain': None})()
        with patch.object(links, '_current_tenant', AsyncMock(return_value=tenant)):
            first = await BracketNotificationMixin._schedule_link(1)
            second = await BracketNotificationMixin._schedule_link(2)
        assert first.url != second.url


class TestLinkButtonRendering:
    """``_with_link_button`` — how a DMLink reaches Discord."""

    def test_it_composes_with_the_buttons_a_dm_already_has(self):
        """Acknowledge *and* View, not one instead of the other."""
        import discord

        from application.services.discord.discord_service import _with_link_button

        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label='Acknowledge', custom_id='ack:1'))
        result = _with_link_button(view, DMLink('View your matches', 'https://x/home/player'))

        labels = [item.label for item in result.children]
        assert labels == ['Acknowledge', 'View your matches']

    def test_it_creates_a_view_for_a_dm_that_had_none(self):
        from application.services.discord.discord_service import _with_link_button

        result = _with_link_button(None, DMLink('Pick a time', 'https://x/home/player'))
        assert result is not None
        assert [item.label for item in result.children] == ['Pick a time']

    def test_no_link_leaves_the_view_untouched(self):
        from application.services.discord.discord_service import _with_link_button

        assert _with_link_button(None, None) is None

    def test_a_blank_url_adds_no_button(self):
        """Discord rejects an empty link URL outright, so guard before building."""
        from application.services.discord.discord_service import _with_link_button

        assert _with_link_button(None, DMLink('Nowhere', '')) is None


class TestPushMirrorGetsTheSameTarget:
    async def test_the_link_becomes_the_notification_tap_target(self):
        """Otherwise a phone notification asking for something opens the last page
        the reader happened to be on."""
        from application.services.discord import discord_service

        captured = {}

        class _FakePush:
            @staticmethod
            def is_configured():
                return True

            async def mirror_dm(self, discord_id, message, navigate=None, silent=False, ref=None):
                captured['navigate'] = navigate

        with patch.object(discord_service, 'WebPushService', _FakePush), \
                patch.object(discord_service.event_dispatch_queue, 'enqueue') as enqueue:
            discord_service._mirror_dm_to_web_push(
                1, 'msg', navigate='https://x/home/player?schedule=5',
            )
            coro = enqueue.call_args[0][0]
            await coro

        assert captured['navigate'] == 'https://x/home/player?schedule=5'


class TestTheDeepLinkParamDoesNotShadowATab:
    """`home()` takes `schedule`/`reschedule`/`match` query params.

    A param shadowed an imported tab function once, so that tab built `None` and
    rendered "This section failed to load" for every viewer — a page-wide
    regression from a notification change, invisible to a service test. No tab
    function is named for a param any more, which is what this pins.
    """

    def test_no_deep_link_param_shadows_an_imported_tab(self):
        import inspect

        import pages.home as home

        src = inspect.getsource(home.create)
        params = {'section', 'request', 'schedule', 'reschedule', 'match'}
        imported_tabs = {
            name for name, value in vars(home).items()
            if callable(value) and name.endswith(('_tab', '_dashboard'))
        }
        assert not (params & imported_tabs)
        assert "'content': tournaments_tab" in src

    def test_the_page_still_accepts_the_deep_link_param(self):
        import inspect

        import pages.home as home

        src = inspect.getsource(home.create)
        assert 'schedule: str | None = None' in src
        assert 'deep_link_ids(' in src


@pytest.mark.parametrize('builder,expected_label', [
    (links.player_schedule, 'Pick a time'),
    (links.admin_match, 'Open the match'),
])
async def test_default_labels_read_as_actions(builder, expected_label):
    """A button labelled with a noun ("Match") tells the reader nothing about
    what pressing it does."""
    tenant = type('T', (), {'slug': 'acme', 'domain': None})()
    with patch.object(links, '_current_tenant', AsyncMock(return_value=tenant)):
        link = await builder(1)
    assert link.label == expected_label


def test_the_review_backlog_dm_does_not_claim_a_pending_run_blocks_the_runner():
    """A pending run holds only its own pool slot, the same as an approved one."""
    from application.utils.discord_messages_qualifier import qualifier_review_queue_dm

    body = qualifier_review_queue_dm('Spring Q', waiting=3, oldest_hours=5)
    assert 'cannot start' not in body
    assert '3 runs awaiting review' in body
