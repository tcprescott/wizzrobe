"""The drawer's section switcher: Home, Volunteer and Admin swap the page list in place.

Volunteer and Admin list their pages through providers their own page modules
register, so a section with no provider would show an empty list.
"""

import pages.admin  # noqa: F401  (registers the Admin provider)
import pages.volunteer  # noqa: F401  (registers the Volunteer provider)
from theme.base import WATCH_URL, BaseLayout, _section_page_path
from theme.section_menu import registered_section_urls


def test_every_switchable_section_but_home_has_a_page_list():
    layout = BaseLayout(user=None, show_admin=True, show_volunteer=True)
    layout._tenant_id = 1
    layout._build_top_menu()
    switchable = {item['url'] for item in layout.top_menu} - {WATCH_URL, '/'}
    assert switchable
    assert switchable <= registered_section_urls()


def test_another_sections_rows_link_to_its_own_pages():
    assert _section_page_path('/', {'label': 'My Schedule'}) == '/home/my-schedule'
    assert _section_page_path('/admin', {'label': 'Match Schedule'}) == '/admin/match-schedule'
    assert _section_page_path('/volunteer', {'label': 'Check-in Desk', 'slug': 'check-in'}) == '/volunteer/check-in'
