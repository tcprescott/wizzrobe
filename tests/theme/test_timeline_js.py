"""static/js/timeline.js turns a pointer position back into a time, so its
hour scale must match the grid's."""

import re
from pathlib import Path

from theme.volunteer_grid import HOUR_PX

ROOT = Path(__file__).resolve().parents[2]


def test_the_script_and_the_grid_share_one_hour_scale():
    script = (ROOT / 'static/js/timeline.js').read_text()
    match = re.search(r'var HOUR_PX = (\d+);', script)
    assert match and int(match.group(1)) == HOUR_PX
    css = (ROOT / 'static/css/styles.css').read_text()
    assert f'--wiz-vgrid-hour: {HOUR_PX}px;' in css


def test_every_page_loads_the_script():
    assert 'js/timeline.js' in (ROOT / 'theme/base.py').read_text()
