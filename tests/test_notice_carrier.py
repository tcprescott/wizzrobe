"""The post-redirect notice carrier, and the shape it exists to prevent.

``ui.notify`` followed by ``ui.navigate.to`` writes a message and then unloads
the document that would have shown it. That single shape accounted for every
unreachable failure string in the OAuth link and login flows, so the carrier
(``theme/notice.py``) gets unit coverage and the shape itself gets a source-text
guard.
"""

import re
from pathlib import Path

import pytest
from nicegui import app

from theme.notice import _KEY, drain_notice, stash_notice


class _FakeStorage(dict):
    """``app.storage.user`` outside a request: a plain dict is enough here."""


@pytest.fixture
def storage(monkeypatch):
    store = _FakeStorage()
    monkeypatch.setattr(type(app.storage), 'user', property(lambda self: store))
    return store


@pytest.fixture
def notified(monkeypatch):
    seen = []
    monkeypatch.setattr('theme.notice.ui.notify', lambda msg, **kw: seen.append((msg, kw)))
    return seen


def test_stash_then_drain_emits_one_notification(storage, notified):
    stash_notice('Link session expired or already used. Please try again.')
    assert storage[_KEY]['message']
    drain_notice()
    assert notified == [
        ('Link session expired or already used. Please try again.', {'color': 'warning'}),
    ]


def test_drain_is_idempotent(storage, notified):
    stash_notice('once', color='positive')
    drain_notice()
    drain_notice()
    assert notified == [('once', {'color': 'positive'})]
    assert _KEY not in storage


def test_a_sticky_notice_survives_a_reload_until_dismissed(storage, monkeypatch):
    shown = []

    class _FakeNotification:
        def __init__(self, message, **kw):
            shown.append((message, kw))

    monkeypatch.setattr('theme.notice.ui.notification', _FakeNotification)
    stash_notice('Welcome in.', color='positive', sticky=True)

    drain_notice()  # the first-visit timezone reload throws this page away
    assert _KEY in storage
    shown[-1][1]['on_dismiss']()  # Got it on the page that stayed
    drain_notice()
    assert [m for m, _ in shown] == ['Welcome in.']
    assert shown[0][1]['timeout'] is None
    assert _KEY not in storage


def test_a_sticky_notice_stops_after_its_show_limit(storage, monkeypatch):
    shown = []
    monkeypatch.setattr('theme.notice.ui.notification', lambda m, **kw: shown.append(m))
    stash_notice('Welcome in.', sticky=True)
    for _ in range(5):
        drain_notice()
    assert len(shown) == 2
    assert _KEY not in storage


def test_a_tenant_notice_waits_for_its_own_community(storage, monkeypatch):
    shown = []
    monkeypatch.setattr('theme.notice.ui.notification', lambda m, **kw: shown.append(m))
    monkeypatch.setattr('theme.notice.get_current_tenant_id', lambda: 2)
    stash_notice('Welcome to One.', sticky=True, tenant_id=1)

    drain_notice()  # a page in another community
    assert shown == []
    assert storage[_KEY]['message'] == 'Welcome to One.'

    monkeypatch.setattr('theme.notice.get_current_tenant_id', lambda: 1)
    drain_notice()
    assert shown == ['Welcome to One.']


def test_drain_with_nothing_stashed_is_a_noop(storage, notified):
    drain_notice()
    assert notified == []


def test_stash_is_single_slot_last_write_wins(storage, notified):
    # A queue would let an aborted redirect chain leave a stale notice behind to
    # surface on some later, unrelated page.
    stash_notice('first')
    stash_notice('second')
    drain_notice()
    assert notified == [('second', {'color': 'warning'})]


def test_stash_ignores_an_empty_message(storage, notified):
    stash_notice('')
    assert _KEY not in storage
    drain_notice()
    assert notified == []


def test_drain_tolerates_junk_in_the_slot(storage, notified):
    storage[_KEY] = 'not a dict'
    drain_notice()
    assert notified == []


def test_drain_survives_having_no_user_storage(monkeypatch, notified):
    # Bare routes can run before a session exists; a drain point must never be
    # the thing that raises out of a page build.
    def boom(self):
        raise RuntimeError('app.storage.user needs a request context')
    monkeypatch.setattr(type(app.storage), 'user', property(boom))
    drain_notice()
    assert notified == []


_SCANNED = ('pages/_oauth_link.py', 'pages/challonge_oauth.py', 'pages/auth.py')
_LEAVES = re.compile(r'^\s*(ui\.navigate\.to\(|return\s+RedirectResponse\(|RedirectResponse\()')


def test_no_link_page_notifies_immediately_before_navigating():
    """No ``ui.notify`` may be followed by a navigation that discards it.

    A source-text test, which is coarse: it cannot see a notify and a navigate
    that reach each other through a call, and it would flag a notify whose
    following navigation is on a branch that cannot run. It is here anyway
    because it is the only thing that stops the shape reappearing — nine written
    strings survived review in this exact form. Use ``stash_notice`` from
    ``theme/notice.py`` instead; where the page keeps rendering (a ``.refresh()``
    handler), ``ui.notify`` is correct and this test will not see it.
    """
    root = Path(__file__).resolve().parent.parent
    offenders = []
    for rel in _SCANNED:
        lines = (root / rel).read_text().splitlines()
        for i, line in enumerate(lines):
            if 'ui.notify(' not in line:
                continue
            # Skip past the notify's own (possibly multi-line) argument list and
            # any blank/comment lines to find the next statement.
            depth = line.count('(') - line.count(')')
            j = i + 1
            while j < len(lines) and depth > 0:
                depth += lines[j].count('(') - lines[j].count(')')
                j += 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and _LEAVES.match(lines[j]):
                offenders.append(f'{rel}:{i + 1}: {line.strip()}  ->  {lines[j].strip()}')
    assert not offenders, (
        'ui.notify immediately before a navigation loses the message; '
        'use theme.notice.stash_notice:\n' + '\n'.join(offenders)
    )
