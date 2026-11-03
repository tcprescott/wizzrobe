"""The tab and home-screen icons pages declare (theme/favicon.py)."""

from theme.brackets.static_view.document import StaticIndexView, render_index_document
from theme.favicon import WIZZROBE_ICON, WIZZROBE_TOUCH_ICON, icon_links_html

_SHA = 'a' * 64
_SVG_THEME = {
    'primary': '#123456',
    'logo': f'/brand-logo/7/{_SHA}.svg',
    'logo_icon': f'/brand-logo/7/{_SHA}-32.png',
    'logo_touch': f'/brand-logo/7/{_SHA}-180.png',
}


def test_no_community_logo_gets_wizzrobes_icons():
    for theme in (None, {}, {'primary': '#123456'}):
        html = icon_links_html(theme)
        assert f'href="{WIZZROBE_ICON}"' in html
        assert f'<link rel="apple-touch-icon" href="{WIZZROBE_TOUCH_ICON}">' in html


def test_svg_logo_declares_png_then_svg_and_its_own_touch_icon():
    html = icon_links_html(_SVG_THEME)
    png = html.index(f'sizes="32x32" href="{_SVG_THEME["logo_icon"]}"')
    svg = html.index(f'type="image/svg+xml" sizes="any" href="{_SVG_THEME["logo"]}"')
    assert png < svg
    assert f'<link rel="apple-touch-icon" href="{_SVG_THEME["logo_touch"]}">' in html
    assert WIZZROBE_ICON not in html and WIZZROBE_TOUCH_ICON not in html


def test_bitmap_logo_declares_only_the_png():
    theme = dict(_SVG_THEME, logo=f'/brand-logo/7/{_SHA}.webp')
    html = icon_links_html(theme)
    assert 'image/svg+xml' not in html and '.webp' not in html
    assert _SVG_THEME['logo_icon'] in html


def test_static_pages_carry_the_community_icons():
    html = render_index_document(StaticIndexView(
        tournament_id=3, tournament_name='Spring Cup', brackets=[], icons=_SVG_THEME,
    ))
    assert icon_links_html(_SVG_THEME) in html
    plain = render_index_document(StaticIndexView(
        tournament_id=3, tournament_name='Spring Cup', brackets=[],
    ))
    assert icon_links_html() in plain
