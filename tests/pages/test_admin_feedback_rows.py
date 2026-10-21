"""Admin → Feedback draws the sender's page as a link only when it stays on this site.

``page_url`` is whatever the sender's browser reported, so it is player input:
an ``href`` of ``javascript:`` runs in the staff reader's session, and ``//evil``
leaves the site.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from application.timezone_context import tz_scope
from models import FeedbackCategory, FeedbackStatus
from pages.admin_tabs.admin_feedback import _row, _status_options


def _feedback(page_url: str, status: FeedbackStatus = FeedbackStatus.NEW):
    return SimpleNamespace(
        id=1, created_at=datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc),
        user=SimpleNamespace(preferred_name='Synack'), category=FeedbackCategory.BUG,
        message='It broke', page_url=page_url, status=status,
    )


@pytest.mark.parametrize('page_url', ['/t/sgl26/volunteer/my-shifts', '/?tab=Equipment'])
def test_a_local_path_is_a_link(page_url):
    with tz_scope('UTC'):
        row = _row(_feedback(page_url))
    assert row['page_href'] == page_url
    assert row['page_url'] == page_url


@pytest.mark.parametrize('page_url', ['javascript:alert(1)', '//evil.example/x', '/\\evil.example',
                                      'https://evil.example', ''])
def test_anything_else_is_text_only(page_url):
    with tz_scope('UTC'):
        row = _row(_feedback(page_url))
    assert row['page_href'] == ''
    assert row['page_url'] == page_url


def test_the_new_count_leaves_reviewed_out():
    with tz_scope('UTC'):
        rows = [_row(_feedback('/', FeedbackStatus.NEW)), _row(_feedback('/', FeedbackStatus.REVIEWED))]
    assert _status_options(rows)['new'] == 'New · 1'
