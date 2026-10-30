"""Each match board owns its filters — they are not a single shared setting.

Four ``MatchTableView`` instances (home schedule, player dashboard, admin
schedule, proctor board) live in one browser session. They used to persist
``state_filter`` / ``tournament_filter`` / ``stage_filter`` under those
bare names, so changing the State filter on the admin Schedule tab silently
retargeted the proctor station and both home boards — and whichever board was
visited last overwrote every per-board default. ``_skey`` namespaces the keys
per view; these tests pin that derivation and the per-view default.

Constructing the view for real needs a NiceGUI slot context, so the tests build
it with ``__new__`` and set only the attributes under test.
"""

import ast
import inspect
from pathlib import Path

from theme.tables.match import MatchTableView
from theme.tables.match_filters import DEFAULT_STATE_FILTER

REPO = Path(__file__).resolve().parents[2]

CONSTRUCTION_SITES = {
    'pages/admin_tabs/admin_schedule.py': 'admin_schedule',
    'pages/volunteer_tabs/proctor_station.py': 'proctor',
    'pages/home_tabs/schedule.py': 'home_schedule',
    'pages/home_tabs/player.py': 'player_dashboard',
}


def _view(storage_key='match', default_state_filter=None, default_day_scope='All dates'):
    view = MatchTableView.__new__(MatchTableView)
    view.storage_key = storage_key
    view.default_state_filter = default_state_filter
    view.default_day_scope = default_day_scope
    view.tournament_filter = None
    view.stage_filter = None
    view.state_filter = None
    view.day_filter = None
    return view


def test_two_views_do_not_share_a_filter_key():
    a = _view('admin_schedule')
    b = _view('proctor')
    assert a._skey('state_filter') != b._skey('state_filter')


def test_every_filter_key_is_namespaced_by_the_view():
    view = _view('proctor')
    for name in ('state_filter', 'tournament_filter', 'stage_filter', 'day_filter'):
        assert view._skey(name).startswith('proctor:')
        assert view._skey(name) != name


def test_skey_keeps_the_filter_names_distinct_within_a_view():
    view = _view('admin_schedule')
    keys = {view._skey(n) for n in
            ('state_filter', 'tournament_filter', 'stage_filter', 'day_filter')}
    assert len(keys) == 4


def test_no_bare_session_key_survives_in_the_view():
    """The whole bug was un-namespaced ``tenant_session_*`` calls."""
    src = inspect.getsource(MatchTableView)
    for name in ('state_filter', 'tournament_filter', 'stage_filter', 'day_filter'):
        for call in ('tenant_session_get', 'tenant_session_set'):
            assert f"{call}('{name}'" not in src, (
                f"{call}('{name}') is not namespaced — every board would share it; "
                f"use self._skey('{name}')"
            )


def _storage_key_arg(path: str):
    """The literal ``storage_key=`` passed to MatchTableView(...) in ``path``."""
    tree = ast.parse((REPO / path).read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == 'MatchTableView'):
            continue
        for kw in node.keywords:
            if kw.arg == 'storage_key':
                return ast.literal_eval(kw.value)
        return None
    raise AssertionError(f'no MatchTableView(...) construction found in {path}')


def test_each_board_passes_its_own_storage_key():
    for path, expected in CONSTRUCTION_SITES.items():
        assert _storage_key_arg(path) == expected, (
            f'{path} must pass storage_key={expected!r}'
        )


def test_the_four_storage_keys_are_distinct():
    keys = list(CONSTRUCTION_SITES.values())
    assert len(set(keys)) == len(keys)


def test_default_state_filter_is_used_when_nothing_is_stored(monkeypatch):
    stored = {}
    monkeypatch.setattr('theme.tables.match_filters.tenant_session_get',
                        lambda key, default=None: stored.get(key, default))
    view = _view('admin_schedule', ['Scheduled', 'Finished'])
    assert view._stored_or_default_states() == ['Scheduled', 'Finished']


def test_shared_default_applies_when_the_view_declares_none(monkeypatch):
    monkeypatch.setattr('theme.tables.match_filters.tenant_session_get',
                        lambda key, default=None: default)
    view = _view('proctor', None)
    assert view._stored_or_default_states() == list(DEFAULT_STATE_FILTER)


def test_a_stored_value_beats_the_default(monkeypatch):
    stored = {'admin_schedule:state_filter': ['Confirmed']}
    monkeypatch.setattr('theme.tables.match_filters.tenant_session_get',
                        lambda key, default=None: stored.get(key, default))
    view = _view('admin_schedule', ['Scheduled', 'Finished'])
    assert view._stored_or_default_states() == ['Confirmed']


def test_another_boards_stored_value_does_not_leak(monkeypatch):
    """The regression that matters: the proctor board keeps its own default
    even after the admin board stored something else."""
    stored = {'admin_schedule:state_filter': ['Confirmed']}
    monkeypatch.setattr('theme.tables.match_filters.tenant_session_get',
                        lambda key, default=None: stored.get(key, default))
    assert _view('proctor')._stored_or_default_states() == list(DEFAULT_STATE_FILTER)


class _FakeSelect:
    def __init__(self, value):
        self.value = value


def test_badge_does_not_count_a_boards_own_default_as_a_custom_filter():
    view = _view('admin_schedule', ['Scheduled', 'Checked In', 'Started', 'Finished'])
    view.tournament_filter = None
    view.stage_filter = None
    view.state_filter = _FakeSelect(['Finished', 'Started', 'Checked In', 'Scheduled'])
    assert view._active_filter_count() == 0


def test_badge_counts_a_state_filter_moved_off_the_boards_default():
    view = _view('admin_schedule', ['Scheduled', 'Checked In', 'Started', 'Finished'])
    view.tournament_filter = None
    view.stage_filter = None
    view.state_filter = _FakeSelect(['Finished'])
    assert view._active_filter_count() == 1


def test_all_dates_is_the_default_and_narrows_nothing():
    """Nothing may be hidden until the operator asks for it."""
    from theme.tables.match_filters import ALL_DAYS, DAY_SCOPES, day_scope_window

    assert DAY_SCOPES[0] == ALL_DAYS
    assert day_scope_window(ALL_DAYS) is None
    assert day_scope_window('nonsense') is None


def test_today_is_one_local_day_wide():
    from theme.tables.match_filters import day_scope_window

    start, end = day_scope_window('Today')
    assert (end - start) == 24 * 3600


def test_tomorrow_starts_where_today_ends():
    """Half-open and adjacent, so no match falls between the two scopes."""
    from theme.tables.match_filters import day_scope_window

    _today_start, today_end = day_scope_window('Today')
    tomorrow_start, _tomorrow_end = day_scope_window('Tomorrow')
    assert today_end == tomorrow_start


def test_the_week_scope_starts_today_and_runs_seven_days():
    from datetime import timedelta

    from application.utils.timezone import local_day_bounds, today_local
    from theme.tables.match_filters import day_scope_window

    today_start, _today_end = day_scope_window('Today')
    week_start, week_end = day_scope_window('Next 7 days')
    assert week_start == today_start
    # Seven local days, not 7 * 24h: a week across a DST change is 167h or 169h.
    _start, seventh_end = local_day_bounds(today_local() + timedelta(days=6), today_local() + timedelta(days=6))
    assert week_end == seventh_end.timestamp()


def test_the_day_filter_counts_toward_the_mobile_badge():
    """Otherwise a narrowed board looks unfiltered on the surface the filter
    was added for."""
    from types import SimpleNamespace

    from theme.tables.match_filters import ALL_DAYS

    view = _view('admin_schedule', DEFAULT_STATE_FILTER)
    view.state_filter = SimpleNamespace(value=list(DEFAULT_STATE_FILTER))
    view.day_filter = SimpleNamespace(value=ALL_DAYS)
    assert view._active_filter_count() == 0

    view.day_filter = SimpleNamespace(value='Today')
    assert view._active_filter_count() == 1


def test_the_admin_board_opens_on_today_and_today_is_not_a_custom_filter():
    """Thomas's pick: All dates sent all ~1,300 rows on every open. Opening on
    Today must not light the badge, but switching to All dates must."""
    from types import SimpleNamespace

    src = (REPO / 'pages/admin_tabs/admin_schedule.py').read_text()
    assert "default_day_scope='Today'" in src

    view = _view('admin_schedule', DEFAULT_STATE_FILTER, default_day_scope='Today')
    view.state_filter = SimpleNamespace(value=list(DEFAULT_STATE_FILTER))
    view.day_filter = SimpleNamespace(value='Today')
    assert view._active_filter_count() == 0
    view.day_filter = SimpleNamespace(value='All dates')
    assert view._active_filter_count() == 1


def test_a_deep_link_suspends_the_day_filter():
    """Same reason it suspends the State filter: a link to one match must not
    land on an empty board."""
    src = inspect.getsource(MatchTableView.refresh)
    assert 'None if self.match_ids else day_scope_window' in src


def _scoped_view(scope, filter_value=None):
    from types import SimpleNamespace

    view = _view('admin_schedule', DEFAULT_STATE_FILTER)
    view.scope_tournament_ids = scope
    view.tournament_filter = SimpleNamespace(value=filter_value) if filter_value else None
    return view


def _resolved_tournament_ids(view):
    """The `tournament_ids` `refresh()` would hand the display service."""
    tournament_ids = None
    if view.tournament_filter and view.tournament_filter.value:
        tournament_ids = view.tournament_filter.value
    if view.scope_tournament_ids is not None:
        tournament_ids = (
            [t for t in tournament_ids if t in view.scope_tournament_ids]
            if tournament_ids else list(view.scope_tournament_ids)
        )
        if not tournament_ids:
            from theme.tables.match import _MATCHES_NOTHING
            tournament_ids = _MATCHES_NOTHING
    return tournament_ids


def test_an_unscoped_board_is_unfiltered():
    assert _resolved_tournament_ids(_scoped_view(None)) is None


def test_a_scoped_board_narrows_to_its_own_tournaments():
    """The bug this pins: the scope was applied to `get_query` (which only the
    row handlers use) while the board's rows came from MatchDisplayService — so
    the "showing the N tournaments you run" chip claimed a scope nothing
    enforced."""
    assert _resolved_tournament_ids(_scoped_view([4, 7])) == [4, 7]


def test_the_filter_narrows_within_the_scope():
    assert _resolved_tournament_ids(_scoped_view([4, 7], filter_value=[7])) == [7]


def test_the_filter_cannot_widen_past_the_scope():
    assert _resolved_tournament_ids(_scoped_view([4], filter_value=[4, 9])) == [4]


def test_a_filter_wholly_outside_the_scope_shows_nothing():
    """`None` would mean *every* tournament, so an empty narrowing needs its own
    spelling — otherwise a scoped board falls open to the whole community."""
    from theme.tables.match import _MATCHES_NOTHING

    assert _resolved_tournament_ids(_scoped_view([4], filter_value=[9])) == _MATCHES_NOTHING
    assert _MATCHES_NOTHING != []


def test_the_resolution_matches_the_one_refresh_uses():
    """This file re-implements it; if `refresh` changes shape, say so loudly."""
    src = inspect.getsource(MatchTableView.refresh)
    assert 'self.scope_tournament_ids is not None' in src
    assert '_MATCHES_NOTHING' in src


def _filtered_view(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr('theme.tables.match_filters.PageTimer', lambda *a, **k: None)
    view = _view('admin_schedule', DEFAULT_STATE_FILTER, default_day_scope='Today')
    view.day_filter = SimpleNamespace(value='Tomorrow')
    view.tournament_filter = SimpleNamespace(value=[3, 4])
    view.stage_filter = SimpleNamespace(value=None)
    view.state_filter = SimpleNamespace(value=['Finished'])
    view.filter_badge = None
    view.filter_reset_desk = None
    view._reset_undo = None
    view.filters_card = _NullContext()
    view.refreshes = 0

    async def refresh():
        view.refreshes += 1
    view.refresh = refresh
    view._bg = lambda coro: coro.close()
    return view


class _NullContext:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_reset_puts_every_filter_back_to_the_boards_own_defaults(monkeypatch):
    view = _filtered_view(monkeypatch)
    assert view._active_filter_count() == 3

    view._on_reset_chip()

    assert view._active_filter_count() == 0
    assert view.day_filter.value == 'Today'
    assert view.tournament_filter.value is None
    assert view.state_filter.value == list(DEFAULT_STATE_FILTER)
    assert view._initializing is False


def test_a_second_tap_undoes_the_reset(monkeypatch):
    view = _filtered_view(monkeypatch)
    view._on_reset_chip()
    view._on_reset_chip()

    assert view.day_filter.value == 'Tomorrow'
    assert view.tournament_filter.value == [3, 4]
    assert view.state_filter.value == ['Finished']
    assert view._reset_undo is None
