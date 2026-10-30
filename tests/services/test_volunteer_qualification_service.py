"""Tests for volunteer qualification logic.

There is no standalone VolunteerQualificationService; qualifications are
managed directly via the VolunteerQualification model and read through
VolunteerQualificationRepository by VolunteerAutoscheduleService. These tests cover that query path using the
in-memory DB fixture, and the qualification-based filtering inside _pick.
"""


from application.repositories import VolunteerQualificationRepository
from application.services.volunteer.volunteer_autoschedule_service import (
    DraftPolicy,
    VolunteerAutoscheduleService,
)


class TestQualificationsQuery:
    """Integration tests that hit the in-memory SQLite DB."""

    async def test_returns_empty_when_no_qualifications(self, db):
        result = await VolunteerQualificationRepository.position_ids_by_user([1, 2])
        assert result == {}

    async def test_returns_position_ids_grouped_by_user(self, db):
        import itertools

        from models import User, VolunteerPosition, VolunteerQualification

        counter = itertools.count(9000)
        user = await User.create(
            discord_id=next(counter), username='tester', display_name='Tester',
        )
        pos1 = await VolunteerPosition.create(name='QualPos1')
        pos2 = await VolunteerPosition.create(name='QualPos2')
        await VolunteerQualification.create(user=user, position=pos1)
        await VolunteerQualification.create(user=user, position=pos2)

        result = await VolunteerQualificationRepository.position_ids_by_user([user.id])
        assert user.id in result
        assert pos1.id in result[user.id]
        assert pos2.id in result[user.id]

    async def test_only_fetches_specified_users(self, db):
        import itertools

        from models import User, VolunteerPosition, VolunteerQualification

        counter = itertools.count(9100)
        u1 = await User.create(discord_id=next(counter), username='u1', display_name='u1')
        u2 = await User.create(discord_id=next(counter), username='u2', display_name='u2')
        pos = await VolunteerPosition.create(name='FilterPos')
        await VolunteerQualification.create(user=u2, position=pos)

        result = await VolunteerQualificationRepository.position_ids_by_user([u1.id])
        assert u2.id not in result


class TestPickQualificationFiltering:
    """Unit tests; no DB needed."""

    def _make_svc(self):
        return object.__new__(VolunteerAutoscheduleService)

    POLICY = DraftPolicy(fill_outside_availability=True, max_hours=0)

    def _make_shift(self, position_id=5):
        from datetime import datetime, timezone
        from types import SimpleNamespace

        UTC = timezone.utc
        return SimpleNamespace(
            id=1,
            position_id=position_id,
            starts_at=datetime(2026, 10, 4, 8, tzinfo=UTC),
            ends_at=datetime(2026, 10, 4, 12, tzinfo=UTC),
        )

    def _make_user(self, uid, name='Alice'):
        from types import SimpleNamespace
        return SimpleNamespace(id=uid, preferred_name=name)

    def test_user_with_no_quals_is_eligible_as_generalist(self):
        svc = self._make_svc()
        user = self._make_user(1)
        shift = self._make_shift(position_id=5)
        result = svc._pick(
            shift, [user], [1], {}, {},
            {1: []}, {1: 0.0}, {1: set()}, policy=self.POLICY,
        )
        assert result is user

    def test_user_qualified_for_correct_position_is_eligible(self):
        svc = self._make_svc()
        user = self._make_user(1)
        shift = self._make_shift(position_id=5)
        quals = {1: {5}}
        result = svc._pick(
            shift, [user], [1], quals, {},
            {1: []}, {1: 0.0}, {1: set()}, policy=self.POLICY,
        )
        assert result is user

    def test_user_qualified_for_wrong_position_is_skipped(self):
        svc = self._make_svc()
        user = self._make_user(1)
        shift = self._make_shift(position_id=5)
        quals = {1: {99}}
        result = svc._pick(
            shift, [user], [1], quals, {},
            {1: []}, {1: 0.0}, {1: set()}, policy=self.POLICY,
        )
        assert result is None

    def test_qualified_user_preferred_over_generalist(self):
        svc = self._make_svc()
        generalist = self._make_user(1, 'Zoe')
        specialist = self._make_user(2, 'Bob')
        shift = self._make_shift(position_id=5)
        quals = {2: {5}}
        result = svc._pick(
            shift, [generalist, specialist], [1, 2], quals, {},
            {1: [], 2: []}, {1: 0.0, 2: 0.0}, {1: set(), 2: set()},
            policy=self.POLICY,
        )
        assert result is specialist
