import pytest

from application.services.bracket_sources import site_for_link


@pytest.mark.parametrize('value, site', [
    ('https://speedgaming.challonge.com/sgl26alttpr', 'challonge'),
    ('challonge.com/abc', 'challonge'),
    ('https://www.start.gg/tournament/sgl/event/ootr', 'startgg'),
    ('start.gg/tournament/sgl/event/ootr', 'startgg'),
    ('abc123', None),
    ('', None),
    ('https://evil.example/challonge.com', None),
    ('https://notchallonge.com/x', None),
])
def test_site_for_link_reads_the_host(value, site):
    assert site_for_link(value) == site
