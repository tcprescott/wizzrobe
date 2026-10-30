"""When the give-back dialog warns that releasing a shift may leave nobody to cover it.

The copy decision is a pure function precisely so it can be asserted without a
slot context: the page it lives on cannot be rendered in a unit test.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from pages.volunteer_tabs.my_shifts import release_warning, shift_actions

UTC = timezone.utc


def _in(hours):
    return datetime.now(UTC) + timedelta(hours=hours)


def test_silent_for_a_shift_days_away():
    assert release_warning(_in(72), 60) is None


def test_speaks_a_few_hours_out():
    message = release_warning(_in(3), 60)
    assert message is not None
    assert '3 hour(s)' in message
    assert 'may not find cover' in message


def test_the_window_is_at_least_a_day():
    # A 60-minute reminder lead must not shrink the warning window to an hour.
    assert release_warning(_in(12), 60) is not None


def test_a_long_reminder_lead_widens_the_window():
    two_days = 48 * 60
    assert release_warning(_in(36), two_days) is not None
    assert release_warning(_in(36), 60) is None


def test_an_already_started_shift_says_so():
    assert 'already started' in release_warning(_in(-1), 60)


def _card(*, ends_in_hours: float, acknowledged: bool = False, checked_in: bool = False):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        shift=SimpleNamespace(ends_at=now + timedelta(hours=ends_in_hours)),
        acknowledged_at=now if acknowledged else None,
        checked_in_at=now if checked_in else None,
        called_off_at=None,
    )


def test_upcoming_unacknowledged_shift_offers_both_buttons():
    assert shift_actions(_card(ends_in_hours=5), datetime.now(timezone.utc)) == (True, True)


def test_checked_in_shift_cannot_be_given_back():
    card = _card(ends_in_hours=2, acknowledged=True, checked_in=True)
    assert shift_actions(card, datetime.now(timezone.utc)) == (False, False)


def test_finished_shift_offers_nothing():
    assert shift_actions(_card(ends_in_hours=-1), datetime.now(timezone.utc)) == (False, False)

