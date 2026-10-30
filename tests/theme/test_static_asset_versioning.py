"""Every static asset the app links must carry a version stamp.

``brackets.css`` shipped a fix for group cards overflowing the page and readers
kept seeing the old layout for days: the URL never changed, so a browser holding
the previous deploy's copy had no reason to ask for another. `_asset` existed
but only two of the nine links used it.

The source scan is the part that matters — an `ui.add_head_html` with a bare
``/static/...`` URL is exactly the regression, and it is invisible at runtime
until a deploy goes out.
"""

import os
import re
from pathlib import Path

from theme.assets import (
    CACHE_IMMUTABLE,
    CACHE_NO_STORE,
    CACHE_REVALIDATE,
    VERSION_PARAM,
    asset_url,
    cache_control,
)

ROOT = Path(__file__).resolve().parents[2]

VERSIONED_SUFFIXES = ('.css', '.js')

STATIC_URL = re.compile(r"""['"]/static/([\w./-]+)['"]""")


def _linked_assets():
    for path in sorted((ROOT / 'theme').rglob('*.py')) + sorted((ROOT / 'pages').rglob('*.py')):
        if path == ROOT / 'theme' / 'assets.py':
            continue
        for match in STATIC_URL.finditer(path.read_text()):
            yield path.relative_to(ROOT), match.group(1)


def test_css_and_js_links_go_through_asset_url():
    bare = [
        f'{path}: /static/{asset}'
        for path, asset in _linked_assets()
        if asset.endswith(VERSIONED_SUFFIXES)
    ]
    assert not bare, (
        'link these through theme.assets.asset_url so a deploy busts the cache: '
        + ', '.join(bare)
    )


def test_asset_url_stamps_an_existing_file():
    url = asset_url('css/brackets.css')
    prefix, _, stamp = url.partition(f'?{VERSION_PARAM}=')
    assert prefix == '/static/css/brackets.css'
    assert stamp.isdigit()


def test_asset_url_stamp_follows_the_file(tmp_path, monkeypatch):
    target = tmp_path / 'css' / 'thing.css'
    target.parent.mkdir()
    target.write_text('a{}')
    monkeypatch.setattr('theme.assets.STATIC_ROOT', tmp_path)

    before = asset_url('css/thing.css')
    target.write_text('b{}')
    stat = target.stat()
    os.utime(target, (stat.st_atime + 60, stat.st_mtime + 60))

    assert asset_url('css/thing.css') != before


def test_asset_url_falls_back_when_the_file_is_missing():
    assert asset_url('css/nope.css') == '/static/css/nope.css'


def test_dev_never_stores():
    assert cache_control(b'v=123', is_dev=True) == CACHE_NO_STORE


def test_production_stamped_url_is_immutable():
    assert cache_control(b'v=1754000000', is_dev=False) == CACHE_IMMUTABLE


def test_production_unstamped_url_must_revalidate():
    assert cache_control(b'', is_dev=False) == CACHE_REVALIDATE
