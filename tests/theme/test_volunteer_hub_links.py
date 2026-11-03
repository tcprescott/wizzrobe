"""Check-in Desk and My Shifts left the menu and the Volunteer hub.

The desk is a Volunteer tab and the shifts live on My Schedule, so the menu
offers Volunteer to a desk worker and the links in DMs point at the new homes.
"""

from application.utils.app_links import (
    volunteer_check_in_url,
    volunteer_my_shifts_url,
    volunteer_release_url,
)
from theme.base import BaseLayout


def _menu(*, volunteer: bool, check_in: bool, proctor: bool = False) -> list[str]:
    layout = BaseLayout(show_volunteer=volunteer)
    layout._show_check_in = check_in
    layout._show_proctor = proctor
    layout._build_top_menu()
    return [item['label'] for item in layout.top_menu]


def test_desk_worker_gets_volunteer_not_a_desk_item():
    labels = _menu(volunteer=False, check_in=True)
    assert 'Volunteer' in labels
    assert 'Check-in Desk' not in labels


def test_volunteer_appears_once():
    assert _menu(volunteer=True, check_in=True).count('Volunteer') == 1


def test_proctor_gets_volunteer_with_volunteers_off():
    assert 'Volunteer' in _menu(volunteer=False, check_in=False, proctor=True)


def test_nothing_to_do_hides_volunteer():
    assert 'Volunteer' not in _menu(volunteer=False, check_in=False)


def test_shift_links_land_on_my_schedule():
    assert volunteer_my_shifts_url() == '/home/my-schedule?shifts=1'
    assert volunteer_release_url(42) == '/home/my-schedule?release=42'


def test_desk_link_is_the_volunteer_tab():
    assert volunteer_check_in_url() == '/volunteer/check-in-desk'
    assert volunteer_check_in_url(3) == '/volunteer/check-in-desk?event=3'
