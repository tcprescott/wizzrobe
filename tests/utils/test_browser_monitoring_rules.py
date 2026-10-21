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
