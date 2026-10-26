import pytest

from application.utils.browser_monitoring import BUILTIN_RULES, MATCH_MESSAGE


def _ignored_message(message: str) -> bool:
    """The message half of static/js/sentry-browser.js's ``ignored``."""
    return any(
        rule.pattern.lower() in message.lower()
        for rule in BUILTIN_RULES
        if rule.match == MATCH_MESSAGE
    )


@pytest.mark.parametrize('message', [
    'TypeError: window.Vue is undefined',
    "TypeError: Cannot destructure property 'withDirectives' of 'window.Vue' as it is undefined.",
])
def test_missing_vue_is_ignored(message):
    assert _ignored_message(message)


def test_an_ordinary_type_error_is_not_ignored():
    assert not _ignored_message("TypeError: Cannot read properties of undefined (reading 'value')")


def test_exceptions_are_dropped_only_while_the_framework_is_missing():
    """Safari's missing-Vue wording names nothing of ours (SGLMAN-1R), and a
    missing Quasar surfaces as the page script's ReferenceError plus NiceGUI's
    stylesheet SecurityError (SGLMAN-27/28), so static/js/sentry-browser.js
    drops them by cause, not by text. The guard's own report is a message, so it
    still goes out."""
    from pathlib import Path

    js = (Path(__file__).resolve().parents[2] / 'static/js/sentry-browser.js').read_text()
    assert 'if (window.Vue && window.Quasar) return false;' in js
    assert "var GUARD_REPORT = 'framework scripts failed to load';" in js
    guard = (Path(__file__).resolve().parents[2] / 'theme/chrome.py').read_text()
    assert "Sentry.captureMessage('Framework scripts failed to load after a reload'" in guard
    assert 'if (missingFramework(event)) return null;' in js
