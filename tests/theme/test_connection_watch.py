"""The connection card's copy, its body-class hook, and its four event bindings.

No unit test can prove what a browser does with a dead socket — that is a
re-measurement pass. What *can* be locked here is the contract the rest of the
offline story hangs off: the exact copy an operator reads, the single body class
the CSS and the click guard both key on, and the fact that all four detection
signals are wired. Losing any one of the four silently halves the detection: the
window events miss "server unreachable, link up", and the socket events miss the
first seconds of a dead link.
"""

import json
from pathlib import Path

from theme.connection import (
    BLOCKED_ACTION_TEXT,
    CARD_SHOWN_BODY_CLASS,
    OFFLINE_BODY_CLASS,
    OFFLINE_BODY_TEXT,
    OFFLINE_TITLE,
    ONLINE_TITLE,
    RECONNECTING_TEXT,
    REQUIRES_SOCKET_CLASS,
    connection_watch_script,
)

_COPY = (OFFLINE_TITLE, OFFLINE_BODY_TEXT, RECONNECTING_TEXT, ONLINE_TITLE, BLOCKED_ACTION_TEXT)
_STYLES = Path(__file__).resolve().parents[2] / 'static' / 'css' / 'styles.css'


class TestCopy:
    def test_offline_card_says_what_it_means_for_the_operator(self):
        assert OFFLINE_TITLE == 'Connection lost'
        assert OFFLINE_BODY_TEXT == "Anything you change now won't be saved."
        assert RECONNECTING_TEXT == 'Trying to reconnect…'

    def test_recovery_text_is_short(self):
        assert ONLINE_TITLE == 'Back online'

    def test_blocked_action_text_says_it_was_not_sent(self):
        assert BLOCKED_ACTION_TEXT == "No connection, so that wasn't sent. Try again once you're back online."

    def test_no_copy_mentions_the_socket(self):
        for text in _COPY:
            assert 'socket' not in text.lower()

    def test_every_string_reaches_the_script(self):
        script = connection_watch_script()
        for text in _COPY:
            # json.dumps escapes the non-ASCII copy, so compare on the escaped form.
            assert json.dumps(text) in script


class TestDetection:
    def test_listens_to_both_window_events(self):
        script = connection_watch_script()
        assert "window.addEventListener('offline'" in script
        assert "window.addEventListener('online'" in script

    def test_listens_to_both_socket_events(self):
        script = connection_watch_script()
        assert "window.socket.on('disconnect'" in script
        assert "window.socket.on('connect'" in script

    def test_tolerates_a_socket_that_does_not_exist_yet(self):
        script = connection_watch_script()
        assert 'if (window.socket && window.socket.on)' in script
        assert 'setTimeout(bindSocket' in script

    def test_installs_only_once_per_document(self):
        script = connection_watch_script()
        assert 'window.__wizConnectionWatch' in script


class TestHooks:
    def test_toggles_the_single_body_class(self):
        script = connection_watch_script()
        assert f'"{OFFLINE_BODY_CLASS}"' in script
        assert 'document.body.classList.add(OFFLINE_CLASS)' in script
        assert 'document.body.classList.remove(OFFLINE_CLASS)' in script

    def test_refuses_marked_controls_in_the_capture_phase(self):
        script = connection_watch_script()
        assert f'"{REQUIRES_SOCKET_CLASS}"' in script
        assert 'e.stopImmediatePropagation()' in script
        assert '}, true);' in script

    def test_explains_the_refusal_without_the_socket(self):
        script = connection_watch_script()
        assert 'Quasar.Notify.create' in script

    def test_marks_the_body_while_the_card_shows(self):
        script = connection_watch_script()
        assert f'"{CARD_SHOWN_BODY_CLASS}"' in script
        assert '--wiz-connection-card-h' in script


class TestStyles:
    def test_framework_popup_is_hidden_in_favour_of_the_card(self):
        css = _STYLES.read_text()
        assert '#popup.nicegui-error-popup {\n    display: none !important;' in css

    def test_no_bottom_bar_remains(self):
        css = _STYLES.read_text()
        assert 'wiz-connection-banner' not in css

    def test_toasts_open_below_the_card(self):
        css = _STYLES.read_text()
        assert f'body.{CARD_SHOWN_BODY_CLASS} .q-notifications__list--top' in css
