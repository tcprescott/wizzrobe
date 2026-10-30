"""The admin Schedule tab and the proctor board share one set of lifecycle callbacks.

Copying the handler bodies into a second page would guarantee drift, so both
surfaces build their ``MatchTableView`` from ``MatchLifecycleHandlers``. These
tests pin the contract — gating by *omission* rather than ``None``, one group per
``MatchBoardAccess`` capability — and the fact that neither page re-implements
it.

The capability split is the point: the board used to pass the five run callbacks
to *everyone* and gate only the other four, which is how a crew coordinator was
handed Check In, Start, Generate and Stations buttons that ``can_run_match`` then
refused one click later.
"""

import inspect
from contextlib import nullcontext
from types import SimpleNamespace

import pages.admin_tabs.admin_schedule as admin_schedule
import pages.volunteer_tabs.proctor_station as proctor_station
import theme.tables.match_lifecycle as match_lifecycle
from theme.tables.match_access import MatchBoardAccess
from theme.tables.match_lifecycle import MatchLifecycleHandlers

RUN = ['on_generate_seed', 'on_seat', 'on_start', 'on_finish', 'on_report_winner', 'on_assign_stations']
EDIT = ['on_edit']
CONFIRM = ['on_confirm', 'on_edit_result']
STREAM = ['on_set_stage']
ALL = RUN + EDIT + CONFIRM + STREAM


def _callbacks(access: MatchBoardAccess) -> set:
    return set(MatchLifecycleHandlers(None, access=access).callbacks())


def test_a_viewer_with_no_capability_is_offered_nothing():
    assert _callbacks(MatchBoardAccess()) == set()


def test_staff_gets_every_callback():
    cb = MatchLifecycleHandlers(None, access=MatchBoardAccess.for_roles(is_staff=True)).callbacks()
    assert set(cb) == set(ALL)
    assert all(callable(fn) for fn in cb.values())


def test_a_tournament_admin_gets_the_same_set_as_staff():
    """Their authority is per tournament, not narrower in kind — the *board* is
    what scopes them (``admin_schedule_page``'s ``tournament_ids``)."""
    assert _callbacks(MatchBoardAccess.for_roles(is_tournament_admin=True)) == set(ALL)


def test_the_proctor_runs_matches_and_amends_nothing():
    """``can_run_match`` admits them; ``can_confirm_match`` deliberately does not."""
    assert _callbacks(MatchBoardAccess.for_roles(is_proctor=True)) == set(RUN)


def test_the_crew_coordinator_is_offered_no_lifecycle_control():
    """The regression this split exists to prevent.

    ``can_approve_crew`` admits them and every other match gate refuses them, so
    a board that hands them Check In / Start / Generate / Stations is a board of
    37 controls that all error. Crew approval is not a callback — it is a slot
    flag — so their callback set is correctly empty.
    """
    assert _callbacks(MatchBoardAccess.for_roles(is_crew_coordinator=True)) == set()


def test_the_broadcast_tech_is_offered_the_stage_and_the_run_controls():
    """The stage is theirs, and so is running a match on one; the row-level
    stage check lives in the slot guards and ``can_run_match``."""
    assert _callbacks(MatchBoardAccess.for_roles(is_stream_manager=True)) == set(STREAM) | set(RUN)


def test_an_uncovered_callback_is_omitted_not_none():
    """MatchTableView keys slot registration and event wiring off *presence*."""
    cb = MatchLifecycleHandlers(None, access=MatchBoardAccess(run=True)).callbacks()
    for name in EDIT + CONFIRM + STREAM:
        assert name not in cb


def test_correcting_a_result_does_not_re_finish_the_match():
    """The match is already Finished when the pencil appears.

    Reusing ``on_finish``'s shape would re-run ``finish_match``, rewriting
    ``finished_at`` and re-notifying for a match nobody just played. Recording
    the corrected winner is the whole of the change.
    """
    src = inspect.getsource(MatchLifecycleHandlers.on_edit_result)
    assert "mode='edit'" in src
    # The call, not the word — the handler's docstring explains the omission.
    assert 'finish_match(' not in src
    assert 'confirm_finishing(' not in src


def test_both_surfaces_build_their_view_from_the_shared_handlers():
    for module, fn in ((admin_schedule, admin_schedule.admin_schedule_page),
                       (proctor_station, proctor_station.proctor_station_tab)):
        src = inspect.getsource(fn)
        assert 'MatchLifecycleHandlers(' in src, module.__name__
        assert '**handlers.callbacks()' in src, module.__name__
        assert 'handlers.table_view = table_view' in src, module.__name__


def test_the_proctor_board_declares_only_the_run_capability():
    src = inspect.getsource(proctor_station.proctor_station_tab)
    assert 'MatchBoardAccess(run=True)' in src
    assert 'submit_match_callback' not in src


class _Recorder:
    """Stands in for ``MatchService``, recording what the handler asked for."""

    def __init__(self):
        self.stages = []
        self.candidates = []

    async def assign_stage(self, match_id, stage_id, actor=None):
        self.stages.append((match_id, stage_id))

    async def set_stream_candidate(self, match_id, flag, actor=None):
        self.candidates.append((match_id, flag))


class _Rows:
    def __init__(self):
        self.refreshed = []

    async def update_row_by_id(self, match_id, flash=False):
        self.refreshed.append(match_id)


def _stage_handlers(monkeypatch, *, stage_id=None, is_stream_candidate=False):
    """Handlers whose one match is in the given stage/candidate state."""
    row = SimpleNamespace(
        id=7, stage_id=stage_id, is_stream_candidate=is_stream_candidate,
    )

    class _Model:
        @staticmethod
        async def get(**_kwargs):
            return row

    monkeypatch.setattr(match_lifecycle, 'Match', _Model)
    monkeypatch.setattr(match_lifecycle, 'require_tenant_id', lambda: 1)
    # ``notify_error`` reaches for a NiceGUI client, which no unit test has.
    notified = []
    monkeypatch.setattr(match_lifecycle, 'notify_error', notified.append)

    handlers = MatchLifecycleHandlers(
        nullcontext(), access=MatchBoardAccess.for_roles(is_staff=True),
    )
    handlers.match_service = _Recorder()
    handlers.table_view = _Rows()

    async def _actor():
        return None
    handlers._actor = _actor
    handlers.notified = notified
    return handlers


async def test_picking_a_stage_assigns_it_and_leaves_the_flag_alone(monkeypatch):
    """A staged match still counts as a candidate in the coverage reports.

    The cell stops *showing* candidate because the stage is the more specific
    answer; clearing the flag here would drop the match out of the one report
    that asks whether its crew is covered.
    """
    handlers = _stage_handlers(monkeypatch, is_stream_candidate=True)
    await handlers.on_set_stage(7, 3)
    assert handlers.match_service.stages == [(7, 3)]
    assert handlers.match_service.candidates == []


async def test_picking_candidate_flags_the_match_and_takes_it_off_its_stage(monkeypatch):
    """``Candidate`` is the answer *before* a stage is picked, so it cannot keep one."""
    handlers = _stage_handlers(monkeypatch, stage_id=3)
    await handlers.on_set_stage(7, 'candidate')
    assert handlers.match_service.stages == [(7, None)]
    assert handlers.match_service.candidates == [(7, True)]


async def test_clearing_the_select_clears_both_the_stage_and_the_flag(monkeypatch):
    handlers = _stage_handlers(monkeypatch, stage_id=3, is_stream_candidate=True)
    await handlers.on_set_stage(7, None)
    assert handlers.match_service.stages == [(7, None)]
    assert handlers.match_service.candidates == [(7, False)]


async def test_re_picking_what_is_already_set_writes_nothing(monkeypatch):
    """Every write audits, publishes an event and can DM — a no-op must not.

    A ``q-select`` re-emits on reselection, and the row refresh after a failed
    write reselects the stored value.
    """
    handlers = _stage_handlers(monkeypatch, stage_id=3)
    await handlers.on_set_stage(7, 3)
    assert handlers.match_service.stages == []
    assert handlers.match_service.candidates == []


async def test_a_refused_write_still_refreshes_the_row(monkeypatch):
    """The select is driven by the row, so the refusal has to snap it back."""
    handlers = _stage_handlers(monkeypatch)

    async def refuse(*_args, **_kwargs):
        raise PermissionError('nope')
    handlers.match_service.assign_stage = refuse

    await handlers.on_set_stage(7, 3)
    assert handlers.table_view.refreshed == [7]
    assert [str(e) for e in handlers.notified] == ['nope']


def _start_handlers(monkeypatch, *, stage_id=None, stations=(None, None)):
    """Handlers whose one match is checked in with the given stations."""
    players = [
        SimpleNamespace(assigned_station=s, user=SimpleNamespace(preferred_name=f'P{i}'))
        for i, s in enumerate(stations)
    ]
    match = SimpleNamespace(id=7, stage_id=stage_id, players=players)

    class _Query:
        def prefetch_related(self, *_args):
            return self

        def __await__(self):
            async def _m():
                return match
            return _m().__await__()

    class _Model:
        @staticmethod
        def get(**_kwargs):
            return _Query()

    opened = []

    class _Dialog:
        def __init__(self, *, match, purpose, **_kwargs):
            self.purpose = purpose

        async def open(self):
            opened.append(('stations', self.purpose))

    class _Confirm:
        def __init__(self, **_kwargs):
            pass

        def open(self):
            opened.append(('confirm', None))

    monkeypatch.setattr(match_lifecycle, 'Match', _Model)
    monkeypatch.setattr(match_lifecycle, 'require_tenant_id', lambda: 1)
    monkeypatch.setattr(match_lifecycle, 'StationAssignmentDialog', _Dialog)
    monkeypatch.setattr(match_lifecycle, 'ConfirmationDialog', _Confirm)
    handlers = MatchLifecycleHandlers(nullcontext(), access=MatchBoardAccess(run=True))
    return handlers, opened


async def test_starting_without_stations_asks_for_them(monkeypatch):
    handlers, opened = _start_handlers(monkeypatch, stations=('4', None))
    await handlers.on_start(7)
    assert opened == [('stations', 'start')]


async def test_starting_a_seated_match_just_confirms(monkeypatch):
    handlers, opened = _start_handlers(monkeypatch, stations=('4', '5'))
    await handlers.on_start(7)
    assert opened == [('confirm', None)]


async def test_a_stage_match_is_never_asked_for_stations(monkeypatch):
    handlers, opened = _start_handlers(monkeypatch, stage_id=2)
    await handlers.on_start(7)
    assert opened == [('confirm', None)]


def _clear_seed_handlers(monkeypatch, *, refuse: bool = False):
    calls = SimpleNamespace(confirms=[], cleared=[], refreshed=[], notified=[])

    class _Confirm:
        def __init__(self, *, on_confirm, **_kwargs):
            calls.confirms.append(on_confirm)

        def open(self):
            pass

    class _Service:
        async def clear_match_seed(self, match_id, *, actor):
            if refuse:
                raise ValueError("This match doesn't have a seed to clear.")
            calls.cleared.append((match_id, actor))

    class _View:
        async def update_row_by_id(self, match_id, flash=False):
            calls.refreshed.append(match_id)

    monkeypatch.setattr(match_lifecycle, 'ConfirmationDialog', _Confirm)
    monkeypatch.setattr(match_lifecycle, 'notify_error', calls.notified.append)
    monkeypatch.setattr(match_lifecycle.ui, 'notify', lambda *_a, **_k: None)
    handlers = MatchLifecycleHandlers(nullcontext(), access=MatchBoardAccess.for_roles(is_staff=True))
    handlers.match_service = _Service()
    handlers.table_view = _View()

    async def _actor():
        return 'staff'
    handlers._actor = _actor
    return handlers, calls


async def test_clearing_a_seed_from_the_sheet_asks_first(monkeypatch):
    handlers, calls = _clear_seed_handlers(monkeypatch)
    await handlers.on_clear_seed(7)
    assert calls.cleared == []
    await calls.confirms[0]()
    assert calls.cleared == [(7, 'staff')]
    assert calls.refreshed == [7]


async def test_a_refused_clear_says_why(monkeypatch):
    handlers, calls = _clear_seed_handlers(monkeypatch, refuse=True)
    await handlers.on_clear_seed(7)
    await calls.confirms[0]()
    assert len(calls.notified) == 1
    assert calls.refreshed == []


def test_the_sheet_offers_clear_seed_only_to_editors():
    from theme.dialog.match_sheet import MatchSheet
    src = inspect.getsource(MatchSheet._check_seed)
    assert 'can_edit_row(row)' in src and 'on_clear_seed' in src


async def test_checking_in_off_stage_seats_the_players(monkeypatch):
    handlers, opened = _start_handlers(monkeypatch)
    await handlers.on_seat(7)
    assert opened == [('stations', 'checkin')]


async def test_checking_in_a_stage_match_takes_one_tap(monkeypatch):
    handlers, opened = _start_handlers(monkeypatch, stage_id=2)
    seated = []

    async def _seat(match):
        seated.append(match.id)
    handlers.confirm_seating = _seat
    await handlers.on_seat(7)
    assert opened == [] and seated == [7]
