"""What "a day" means is a display-clock decision, and it lives in the service.

``MatchService.get_matches_for_date`` turns a picked date into the half-open
instant window the repository queries. Getting that wrong is invisible: the page
renders, the table fills, and only the matches near either midnight are missing
or wrong.

Regression: the bounds were once built with a bare ``datetime.combine``, whose
naive result the ORM reads as UTC — so "March 10" selected 00:00–23:59 *UTC*. In
Eastern that dropped the evening (19:00 ET onward is already the 11th in UTC) and
wrongly pulled in the previous evening.
"""

from datetime import date

from application.services.match.match_service import MatchService
from application.timezone_context import tz_scope
from models import Match, Stage, Tournament
from tests.factories import utc


class TestGetMatchesForDate:
    async def test_uses_the_display_clock_not_utc(self, db):
        t = await Tournament.create(name="T")
        sr = await Stage.create(name="Stage 1")
        # 21:00 ET on the 10th — 02:00 UTC on the *11th*. On the viewer's clock
        # this is squarely inside the requested day.
        evening = await Match.create(
            tournament=t, scheduled_at=utc(2025, 3, 11, 2), stage=sr,
        )
        # 20:00 ET on the *9th* — 01:00 UTC on the 10th. Inside the UTC day, but
        # not the day that was asked for.
        await Match.create(
            tournament=t, scheduled_at=utc(2025, 3, 10, 1), stage=sr,
        )
        with tz_scope('America/New_York'):
            result = await MatchService().get_matches_for_date(date(2025, 3, 10))
        assert [m.id for m in result] == [evening.id]

    async def test_window_follows_the_zone(self, db):
        """The same instant belongs to different days in different zones."""
        t = await Tournament.create(name="T")
        sr = await Stage.create(name="Stage 1")
        m = await Match.create(
            tournament=t, scheduled_at=utc(2025, 3, 11, 2), stage=sr,
        )
        service = MatchService()
        with tz_scope('America/New_York'):
            eastern = await service.get_matches_for_date(date(2025, 3, 10))
        with tz_scope('Asia/Tokyo'):
            tokyo = await service.get_matches_for_date(date(2025, 3, 11))
        assert [x.id for x in eastern] == [m.id]
        assert [x.id for x in tokyo] == [m.id]

    async def test_midnight_boundary_is_half_open(self, db):
        """Local midnight belongs to the day it starts, not the one it ends."""
        t = await Tournament.create(name="T")
        sr = await Stage.create(name="Stage 1")
        # Exactly 00:00 ET on the 11th = 05:00 UTC.
        midnight = await Match.create(
            tournament=t, scheduled_at=utc(2025, 3, 11, 5), stage=sr,
        )
        service = MatchService()
        with tz_scope('America/New_York'):
            tenth = await service.get_matches_for_date(date(2025, 3, 10))
            eleventh = await service.get_matches_for_date(date(2025, 3, 11))
        assert [x.id for x in tenth] == []
        assert [x.id for x in eleventh] == [midnight.id]
