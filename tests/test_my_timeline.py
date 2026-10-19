"""The Timeline view's pure rules: lanes for clashes, the 06:00 event day,
which day opens, and the short-name fallback."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from application.services.my_timeline_service import (
    COMMUNITY,
    CREW,
    GOING,
    MATCH,
    NO_STAGE,
    REMINDER,
    SHIFT,
    UNAVAILABLE,
    MyTimelineService,
    TimelineItem,
    event_day,
    opening_day,
    place_in_lanes,
    split_by_event_day,
)
from models import Tournament, VolunteerAvailabilityStatus

UTC = timezone.utc
T0 = datetime(2026, 10, 23, 15, 0, tzinfo=UTC)


def _item(start_h, hours, kind=MATCH):
    start = T0 + timedelta(hours=start_h)
    return TimelineItem(kind=kind, starts_at=start, ends_at=start + timedelta(hours=hours),
                        title='x', detail='')


def test_separate_items_each_take_the_full_width():
    placed = place_in_lanes([_item(0, 1), _item(2, 1)])
    assert [(p.lane, p.lanes) for p in placed] == [(0, 1), (0, 1)]


def test_a_clash_sits_side_by_side():
    shift = _item(0, 4, SHIFT)
    crew = _item(2.5, 1.5)
    placed = {p.item: (p.lane, p.lanes) for p in place_in_lanes([crew, shift])}
    assert placed[shift] == (0, 2)
    assert placed[crew] == (1, 2)


def test_back_to_back_items_do_not_clash():
    placed = place_in_lanes([_item(0, 1), _item(1, 1)])
    assert all(p.lanes == 1 for p in placed)


def test_a_late_night_match_belongs_to_the_evening_before():
    one_am = datetime(2026, 10, 24, 1, 0, tzinfo=UTC)
    assert event_day(one_am, 'UTC') == date(2026, 10, 23)
    assert event_day(one_am + timedelta(hours=6), 'UTC') == date(2026, 10, 24)


def test_opening_day_prefers_today_then_the_next_day_on():
    days = [date(2026, 10, 22), date(2026, 10, 24)]
    assert opening_day(days, date(2026, 10, 22)) == date(2026, 10, 22)
    assert opening_day(days, date(2026, 10, 23)) == date(2026, 10, 24)
    assert opening_day(days, date(2026, 10, 30)) == date(2026, 10, 24)
    assert opening_day([], date(2026, 10, 23)) is None


def _match(players, tournament):
    return SimpleNamespace(
        id=7, title='', scheduled_at=T0, tournament=tournament, stage_id=None,
        players=[SimpleNamespace(user_id=u.id, user=u) for u in players],
    )


def test_a_played_match_names_the_opponent_and_the_short_name():
    me = SimpleNamespace(id=1, preferred_name='Me')
    them = SimpleNamespace(id=2, preferred_name='Them')
    tournament = Tournament(name='A Link to the Past Main', short_name='ALTTPR', average_match_duration=60)
    item = MyTimelineService._match_item(_match([me, them], tournament), me, None)
    assert (item.kind, item.title, item.detail) == (MATCH, 'vs Them', 'ALTTPR')
    assert item.ends_at - item.starts_at == timedelta(minutes=60)


def test_a_crewed_match_says_the_role_and_who_plays():
    me = SimpleNamespace(id=1, preferred_name='Me')
    a = SimpleNamespace(id=2, preferred_name='A')
    b = SimpleNamespace(id=3, preferred_name='B')
    tournament = Tournament(name='Super Metroid', short_name=None)
    item = MyTimelineService._match_item(_match([a, b], tournament), me, 'commentator')
    assert (item.title, item.detail) == ('Commentary', 'A vs B · Super Metroid')
    assert item.ends_at - item.starts_at == timedelta(minutes=90)
    assert (item.badge, item.optional) == ('', False)


def test_an_approved_crew_slot_with_no_stage_is_optional():
    me = SimpleNamespace(id=1, preferred_name='Me')
    a = SimpleNamespace(id=2, preferred_name='A')
    tournament = Tournament(name='Super Metroid', short_name=None)
    off_stage = _match([a], tournament)
    on_stage = _match([a], tournament)
    on_stage.stage_id = 4

    item = MyTimelineService._match_item(off_stage, me, 'tracker', approved=True)
    assert (item.kind, item.badge, item.optional) == (CREW, NO_STAGE, True)
    item = MyTimelineService._match_item(on_stage, me, 'tracker', approved=True)
    assert (item.badge, item.optional) == ('', False)
    item = MyTimelineService._match_item(off_stage, me, 'tracker', approved=False)
    assert (item.badge, item.optional) == ('', False)


def test_short_label_falls_back_to_the_name():
    assert Tournament(name='Full', short_name='  ').short_label == 'Full'
    assert Tournament(name='Full', short_name='F').short_label == 'F'


def test_a_window_past_six_in_the_morning_splits_into_two_days():
    start = datetime(2026, 10, 23, 20, 0, tzinfo=UTC)
    end = datetime(2026, 10, 24, 10, 0, tzinfo=UTC)
    six = datetime(2026, 10, 24, 6, 0, tzinfo=UTC)
    assert split_by_event_day(start, end, 'UTC') == [(start, six), (six, end)]


def test_a_window_inside_one_day_stays_whole():
    start = datetime(2026, 10, 23, 8, 0, tzinfo=UTC)
    end = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
    assert split_by_event_day(start, end, 'UTC') == [(start, end)]


async def test_unavailable_keeps_only_cant_play_windows_inside_the_event(monkeypatch):
    lo = datetime(2026, 10, 22, 6, 0, tzinfo=UTC)
    hi = datetime(2026, 10, 26, 6, 0, tzinfo=UTC)
    service = MyTimelineService()

    async def window():
        return lo, hi

    def row(start, end, status, note=None):
        return SimpleNamespace(starts_at=start, ends_at=end, status=status, note=note)

    async def availability_for(_user):
        return [
            row(lo - timedelta(hours=4), lo + timedelta(hours=2), VolunteerAvailabilityStatus.UNAVAILABLE, 'Flight'),
            row(T0, T0 + timedelta(hours=2), VolunteerAvailabilityStatus.PREFERRED),
            row(hi + timedelta(days=1), hi + timedelta(days=2), VolunteerAvailabilityStatus.UNAVAILABLE),
        ]

    monkeypatch.setattr(service, 'window', window)
    monkeypatch.setattr(service.availability_service, 'availability_for', availability_for)
    items = await service.unavailable(SimpleNamespace(id=1))
    assert [(i.kind, i.starts_at, i.ends_at, i.detail) for i in items] == [
        (UNAVAILABLE, lo, lo + timedelta(hours=2), 'Flight'),
    ]


async def test_community_events_join_the_timeline_one_piece_per_day(monkeypatch):
    lo = datetime(2026, 10, 22, 6, 0, tzinfo=UTC)
    hi = datetime(2026, 10, 26, 6, 0, tzinfo=UTC)
    service = MyTimelineService()

    async def window():
        return lo, hi

    async def none(*_args, **_kwargs):
        return []

    def event(id_, start, minutes, location=None):
        return SimpleNamespace(id=id_, title=f'Event {id_}', location=location,
                               starts_at=start, duration_minutes=minutes)

    overnight = event(1, datetime(2026, 10, 23, 22, 0, tzinfo=UTC), 600, 'Lobby')
    early = event(2, lo - timedelta(hours=1), 120)

    async def list_attending(_user, start, end):
        assert (start, end) == (lo, hi)
        return [(overnight, GOING), (early, REMINDER)]

    monkeypatch.setattr(service, 'window', window)
    monkeypatch.setattr(service.crew_service, 'list_my_commitments', none)
    monkeypatch.setattr(service.match_repository, 'commitments_for_user', none)
    monkeypatch.setattr(service.community_service, 'list_attending', list_attending)
    monkeypatch.setattr('application.services.my_timeline_service.split_by_event_day',
                        lambda a, b: split_by_event_day(a, b, 'UTC'))

    items = await service.items(SimpleNamespace(id=1), include_shifts=False, include_community=True)

    assert [(i.kind, i.event_id, i.badge, i.optional, i.starts_at, i.ends_at, i.detail) for i in items] == [
        (COMMUNITY, 2, REMINDER, True, lo, lo + timedelta(hours=1), ''),
        (COMMUNITY, 1, GOING, False, overnight.starts_at, datetime(2026, 10, 24, 6, 0, tzinfo=UTC), 'Lobby'),
        (COMMUNITY, 1, GOING, False, datetime(2026, 10, 24, 6, 0, tzinfo=UTC),
         datetime(2026, 10, 24, 8, 0, tzinfo=UTC), 'Lobby'),
    ]
    assert await service.items(SimpleNamespace(id=1), include_shifts=False) == []
