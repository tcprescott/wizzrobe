"""The Watch page at ``/live``: its content, its static rendering and the live/static switch.

What a stranger from a tweet sees: every stage's stream with what's on now and
next (player names are public), what's coming up on stream, and each
tournament's bracket. Built once per change for every reader; answered
statically when staff say so or a rush of arrivals trips it.
"""

import asyncio
from datetime import time, timedelta

import pytest
from starlette.requests import Request

import pages.static_watch as static_watch
import pages.watch_data as watch_data
from application.services.system_config_service import (
    KEY_WATCH_AUTO_THRESHOLD,
    KEY_WATCH_MODE,
    SystemConfigService,
)
from application.tenant_context import tenant_scope
from application.utils import live_audience
from middleware.auth import _matches_protected_route
from middleware.public_cache import _drop_cookie_vary, _is_public, is_public_cacheable_path
from models import (
    Commentator,
    GeneratedSeeds,
    Match,
    MatchPlayers,
    Role,
    Stage,
    Tournament,
    UserRole,
)
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.factories import make_user
from theme.watch_view import (
    STATIC_REFRESH_SECONDS,
    WatchMatch,
    WatchStage,
    WatchTournament,
    WatchView,
    render_watch_document,
)


@pytest.fixture(autouse=True)
def _fresh_state():
    watch_data._entries.clear()
    watch_data._settings.clear()
    live_audience.reset()
    yield
    watch_data._entries.clear()
    watch_data._settings.clear()
    live_audience.reset()


async def _staff():
    user = await make_user(discord_id=8001, username='staff-watch')
    await UserRole.create(user=user, role=Role.STAFF, tenant_id=DEFAULT_TEST_TENANT_ID)
    return user


async def _match_on(stage: Stage, tournament: Tournament, hour: int, *,
                    days_ahead: int = 0, seated: bool = False) -> Match:
    from application.utils.timezone import combine_local, now_local, today_local

    match = await Match.create(
        tournament=tournament, stage=stage,
        scheduled_at=combine_local(today_local() + timedelta(days=days_ahead), time(hour, 0)),
        seated_at=now_local() if seated else None,
        generated_seed=await GeneratedSeeds.create(
            seed_url='https://alttpr.com/h/abcd1234', generator='alttpr',
        ),
    )
    for n in (1, 2):
        player = await make_user(
            discord_id=8100 + match.id * 10 + n, username=f'w{match.id}-{n}',
            display_name=f'Runner {match.id}-{n}',
        )
        await MatchPlayers.create(match=match, user=player)
    return match


class TestBuildWatchView:
    async def test_stages_show_the_stream_and_who_is_playing(self, db):
        with tenant_scope(DEFAULT_TEST_TENANT_ID):
            stage = await Stage.create(
                name='Main Stage', stream_url='https://twitch.tv/Main',
                tenant_id=DEFAULT_TEST_TENANT_ID,
            )
            cup = await Tournament.create(name='Cup')
            live = await _match_on(stage, cup, 23, seated=True)
            caster = await make_user(discord_id=8900, username='caster', display_name='Caster')
            await Commentator.create(match=live, user=caster, approved=True, tenant_id=DEFAULT_TEST_TENANT_ID)
            view = await watch_data.build_watch_view(DEFAULT_TEST_TENANT_ID)

        [main] = view.stages
        assert main.channel == 'main'
        assert main.channel_name == 'Main'
        assert main.stream_url == 'https://twitch.tv/Main'
        assert ' vs ' in main.on_now.players
        assert main.on_now.commentators == 'Caster'

    async def test_stages_sort_by_channel_name_in_natural_order(self, db):
        with tenant_scope(DEFAULT_TEST_TENANT_ID):
            for name, channel in (
                ('Air', 'SpeedGaming4'), ('Earth', 'SpeedGaming'), ('Fire', 'SpeedGaming10'),
                ('Water', 'SpeedGaming3'), ('Lobby', None), ('Wind', 'speedgaming2'),
            ):
                await Stage.create(
                    name=name, stream_url=f'https://twitch.tv/{channel}' if channel else 'https://youtube.com/x',
                    tenant_id=DEFAULT_TEST_TENANT_ID,
                )
            view = await watch_data.build_watch_view(DEFAULT_TEST_TENANT_ID)

        assert [s.name for s in view.stages] == ['Earth', 'Wind', 'Water', 'Air', 'Fire', 'Lobby']

    async def test_later_matches_are_coming_up_by_day(self, db):
        with tenant_scope(DEFAULT_TEST_TENANT_ID):
            stage = await Stage.create(name='Main', tenant_id=DEFAULT_TEST_TENANT_ID)
            cup = await Tournament.create(name='Cup')
            await _match_on(stage, cup, 12, days_ahead=1)
            await _match_on(stage, cup, 12, days_ahead=2)
            view = await watch_data.build_watch_view(DEFAULT_TEST_TENANT_ID)

        # No stream and nothing today: the stage column is left out.
        assert view.stages == []
        assert len(view.coming) == 2
        assert [m.stage for d in view.coming for m in d.matches] == ['Main', 'Main']

    async def test_tournaments_with_a_bracket_or_a_streamed_match_are_listed(self, db):
        with tenant_scope(DEFAULT_TEST_TENANT_ID):
            stage = await Stage.create(name='Main', tenant_id=DEFAULT_TEST_TENANT_ID)
            await Tournament.create(
                name='Linked', bracket_url='https://example.challonge.com/cup',
                rules_url='https://example.com/rules', challonge_state='group_stages_underway',
            )
            await Tournament.create(name='Hidden fixture')
            streamed = await Tournament.create(name='Streamed')
            await _match_on(stage, streamed, 12, days_ahead=1)
            view = await watch_data.build_watch_view(DEFAULT_TEST_TENANT_ID)

        by_name = {t.name: t for t in view.tournaments}
        assert set(by_name) == {'Linked', 'Streamed'}
        assert by_name['Linked'].status == 'Groups underway'
        assert by_name['Linked'].tone == 'live'
        assert by_name['Linked'].rules_url == 'https://example.com/rules'
        assert by_name['Streamed'].bracket_url == ''

    async def test_a_tournament_shows_its_pool_plus_bonus(self, db):
        from datetime import datetime, timezone
        from decimal import Decimal

        with tenant_scope(DEFAULT_TEST_TENANT_ID):
            await Tournament.create(
                name='Synced', bracket_url='https://example.challonge.com/a',
                prize_pool=Decimal('1701'), prize_bonus=Decimal('500'), sglive_slug='wwr',
                prize_pool_synced_at=datetime.now(timezone.utc),
            )
            await Tournament.create(
                name='Typed', bracket_url='https://example.challonge.com/b', prize_pool=Decimal('50'),
            )
            await Tournament.create(name='None', bracket_url='https://example.challonge.com/c')
            view = await watch_data.build_watch_view(DEFAULT_TEST_TENANT_ID)

        by_name = {t.name: t for t in view.tournaments}
        assert by_name['Synced'].prize_total == Decimal('2201')
        assert by_name['Synced'].prize_synced
        assert by_name['Typed'].prize_total == Decimal('50')
        assert not by_name['Typed'].prize_synced
        assert by_name['None'].prize_total is None

    async def test_no_pool_shows_where_payouts_are_off(self, db):
        from decimal import Decimal

        from models import FeatureFlag, TenantFeatureFlag

        with tenant_scope(DEFAULT_TEST_TENANT_ID):
            await Tournament.create(
                name='Cup', bracket_url='https://example.challonge.com/a', prize_pool=Decimal('100'),
            )
            await TenantFeatureFlag.filter(
                tenant_id=DEFAULT_TEST_TENANT_ID, flag=FeatureFlag.PAYOUTS.value,
            ).update(enabled=False)
            view = await watch_data.build_watch_view(DEFAULT_TEST_TENANT_ID)

        assert view.tournaments[0].prize_total is None

    async def test_a_pool_change_refreshes_the_page(self, db):
        before = watch_data.watch_version(DEFAULT_TEST_TENANT_ID)
        watch_data.subscribe()
        from application.events import Event, EventType, event_bus

        with tenant_scope(DEFAULT_TEST_TENANT_ID):
            event_bus.publish(Event.create(EventType.TOURNAMENT_PRIZE_POOL_UPDATED, {'tournament_id': 1}, None))
        await asyncio.sleep(0)
        assert watch_data.watch_version(DEFAULT_TEST_TENANT_ID) > before

    async def test_one_build_serves_every_reader_until_something_changes(self, db, monkeypatch):
        calls = []

        async def fake_build(tenant_id):
            calls.append(tenant_id)
            return WatchView(community='C')

        monkeypatch.setattr(watch_data, 'build_watch_view', fake_build)
        await watch_data.get_watch_view(DEFAULT_TEST_TENANT_ID)
        await watch_data.get_watch_view(DEFAULT_TEST_TENANT_ID)
        assert len(calls) == 1

        from application.events import Event, EventType
        with tenant_scope(DEFAULT_TEST_TENANT_ID):
            watch_data._on_change(Event.create(EventType.MATCH_STARTED, {'match_id': 1}))
        await watch_data.get_watch_view(DEFAULT_TEST_TENANT_ID)
        assert len(calls) == 2


class TestRender:
    def test_user_text_is_escaped_and_unsafe_links_are_not_drawn(self):
        body = render_watch_document(WatchView(
            community='<b>Com</b>',
            stages=[WatchStage(
                id=1,
                name='<img src=x onerror=alert(1)>',
                stream_url='javascript:alert(1)',
                up_next=[WatchMatch(time='19:00', tournament='T', players='<script>x</script>')],
            )],
            tournaments=[WatchTournament(id=2, name='T', bracket_url='javascript:alert(2)')],
        ))
        assert '<img src=x' not in body
        assert '<script>x' not in body
        assert 'javascript:' not in body
        assert '&lt;b&gt;Com&lt;/b&gt;' in body

    def test_a_prize_pool_draws_as_a_chip_with_the_sglive_footnote(self):
        from decimal import Decimal

        body = render_watch_document(WatchView(
            community='C',
            tournaments=[
                WatchTournament(id=1, name='Cup', prize_total=Decimal('2201'), prize_synced=True),
                WatchTournament(id=2, name='Plain'),
            ],
        ))
        assert 'wiz-chip--ok' in body
        assert '$2,201 prize pool' in body
        assert body.count('prize pool</span>') == 1
        assert 'sglive.speedgaming.org</a> every 15 minutes' in body

    def test_no_footnote_without_a_synced_pool(self):
        from decimal import Decimal

        body = render_watch_document(WatchView(
            community='C',
            tournaments=[WatchTournament(id=1, name='Cup', prize_total=Decimal('50'))],
        ))
        assert '$50 prize pool' in body
        assert 'sglive' not in body

    def test_it_updates_in_place_instead_of_reloading(self):
        body = render_watch_document(
            WatchView(community='C'), root_path='/t/sgl', refresh_url='/t/sgl/live/static',
        )
        assert 'http-equiv="refresh"' not in body
        assert 'data-refresh-url="/t/sgl/live/static"' in body
        assert str(STATIC_REFRESH_SECONDS * 1000) in body

    def test_native_brackets_carry_the_tenant_prefix_and_twitch_stages_get_a_player(self):
        body = render_watch_document(WatchView(
            community='C',
            stages=[WatchStage(id=4, name='Main', stream_url='https://twitch.tv/main', channel='main')],
            tournaments=[WatchTournament(id=3, name='Cup', bracket_url='/live/tournament/3/brackets')],
        ), root_path='/t/sgl')
        assert 'href="/t/sgl/live/tournament/3/brackets"' in body
        assert 'data-channel="main"' in body
        assert 'Get involved' not in body


class TestStaticSwitch:
    def _request(self) -> Request:
        return Request({
            'type': 'http', 'method': 'GET', 'path': '/live', 'root_path': '/t/default',
            'headers': [], 'query_string': b'',
        })

    async def test_live_by_default_and_every_arrival_counts(self, db):
        with tenant_scope(DEFAULT_TEST_TENANT_ID):
            assert await static_watch.serve_static_arrival(self._request()) is None
        assert live_audience.snapshot(DEFAULT_TEST_TENANT_ID).arrivals_last_minute == 1

    async def test_staff_can_set_it_static(self, db, monkeypatch):
        async def fake_view(tenant_id):
            return WatchView(community='C')

        monkeypatch.setattr(static_watch, 'get_watch_view', fake_view)
        with tenant_scope(DEFAULT_TEST_TENANT_ID):
            await SystemConfigService.set_validated(KEY_WATCH_MODE, 'static', await _staff())
            response = await static_watch.serve_static_arrival(self._request())
        assert response is not None
        assert b'data-refresh-url="/t/default/live/static"' in response.body

    async def test_a_rush_trips_it_static(self, db, monkeypatch):
        async def fake_view(tenant_id):
            return WatchView(community='C')

        monkeypatch.setattr(static_watch, 'get_watch_view', fake_view)
        with tenant_scope(DEFAULT_TEST_TENANT_ID):
            await SystemConfigService.set_validated(KEY_WATCH_AUTO_THRESHOLD, '2', await _staff())
            assert await static_watch.serve_static_arrival(self._request()) is None
            assert await static_watch.serve_static_arrival(self._request()) is None
            assert await static_watch.serve_static_arrival(self._request()) is not None
        assert live_audience.snapshot(DEFAULT_TEST_TENANT_ID).tripped
        live_audience.clear_trip(DEFAULT_TEST_TENANT_ID)
        assert not live_audience.is_tripped(DEFAULT_TEST_TENANT_ID)

    async def test_a_bad_mode_is_refused(self, db):
        with tenant_scope(DEFAULT_TEST_TENANT_ID):
            with pytest.raises(ValueError):
                await SystemConfigService.set_validated(KEY_WATCH_MODE, 'sometimes', await _staff())


class TestAudience:
    def test_viewers_and_their_peak(self):
        live_audience.viewer_joined(5, 'a')
        live_audience.viewer_joined(5, 'b')
        live_audience.viewer_left(5, 'a')
        snap = live_audience.snapshot(5)
        assert snap.live_viewers == 1
        assert snap.peak == 2
        assert snap.peak_at is not None


class TestRoutes:
    def test_registered_anonymous_and_cookie_free_only_when_public(self):
        static_watch.create()
        from nicegui import app

        assert '/live/static' in {getattr(r, 'path', None) for r in app.routes}
        assert not _matches_protected_route('/live')
        assert is_public_cacheable_path('/t/sgl/live')
        assert is_public_cacheable_path('/t/sgl/live/static')
        assert not is_public_cacheable_path('/t/sgl/lively')
        assert _is_public([(b'cache-control', b'public, max-age=30')])
        assert not _is_public([(b'cache-control', b'no-store')])
        assert not _is_public([])


def test_vary_cookie_is_dropped_so_a_cdn_can_share_one_entry():
    assert _drop_cookie_vary(b'Accept-Encoding, Cookie, Accept-Encoding') == b'Accept-Encoding'
    assert _drop_cookie_vary(b'Cookie') == b''


def test_the_static_page_offers_the_dark_mode_toggle_and_honours_the_cookie():
    body = render_watch_document(WatchView(community='C'))
    assert 'id="wizw-theme"' in body
    assert 'aria-label="Toggle dark mode"' in body
    assert 'wiz_dark' in body


def test_a_twitch_stage_names_its_channel_as_the_link_spells_it():
    from application.utils.twitch_embed import twitch_channel, twitch_channel_name

    assert twitch_channel_name('https://www.twitch.tv/SpeedGaming') == 'SpeedGaming'
    assert twitch_channel('https://www.twitch.tv/SpeedGaming') == 'speedgaming'
    assert twitch_channel_name('https://youtube.com/@sg') is None
    body = render_watch_document(WatchView(
        community='C',
        stages=[WatchStage(id=1, name='Main', stream_url='https://twitch.tv/SpeedGaming',
                           channel='speedgaming', channel_name='SpeedGaming')],
    ))
    assert 'twitch.tv/SpeedGaming' in body


def test_twitch_stages_are_tiles_led_by_the_stream_still():
    from datetime import datetime, timedelta, timezone

    started = datetime.now(timezone.utc) - timedelta(minutes=42)
    body = render_watch_document(WatchView(
        community='C',
        stages=[
            WatchStage(id=1, name='Main', stream_url='https://twitch.tv/SpeedGaming',
                       channel='speedgaming', channel_name='SpeedGaming',
                       on_now=WatchMatch(time='20:00', players='A vs B', started_at=started)),
            WatchStage(id=2, name='Side', stream_url='https://twitch.tv/sg2', channel='sg2',
                       on_now=WatchMatch(time='21:00', players='C vs D')),
        ],
    ))
    assert 'previews-ttv/live_user_speedgaming-640x360.jpg' in body
    assert 'referrerpolicy="no-referrer"' in body
    assert body.count('class="wiz-onair"') == 1
    assert f'data-since="{int(started.timestamp())}"' in body
    assert '42 min in' in body
    assert 'Getting ready' in body
    assert '1 live' in body


def test_live_for_counts_minutes_then_hours():
    from datetime import datetime, timedelta, timezone

    from theme.watch_view import live_for

    now = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
    assert live_for(None, now) == ''
    assert live_for(now - timedelta(minutes=5), now) == '5 min in'
    assert live_for(now - timedelta(minutes=72), now) == '1 h 12 min in'
    assert live_for(now + timedelta(minutes=1), now) == '0 min in'
