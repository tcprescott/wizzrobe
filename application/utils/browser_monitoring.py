"""What the browser-side Sentry SDK may send, as the running process knows it.

Every page build reads this to decide whether to load the SDK and what to hand
it, and a page build is synchronous, so the settings live in memory:
:class:`~application.services.BrowserMonitoringService` loads them at startup
and replaces the snapshot on every change. One worker, one snapshot.

The built-in rules are the browser noise every site sees and nobody can fix:
extensions, venue Wi-Fi dropping a request, a browser's own quirks. They ship
in code so the first deploy is already quiet; super-admins add the rest on
``/platform`` → Monitoring.
"""

from dataclasses import dataclass
from typing import Any, Dict, Tuple

MATCH_MESSAGE = 'message'
MATCH_URL = 'url'
MATCH_KINDS = (MATCH_MESSAGE, MATCH_URL)

#: Shares of browser errors a super-admin can choose to send.
SAMPLE_RATES = (1.0, 0.5, 0.25, 0.1)


@dataclass(frozen=True)
class IgnoreRule:
    """A case-insensitive substring of an error's message or a stack frame's URL."""

    pattern: str
    match: str


BUILTIN_RULES: Tuple[IgnoreRule, ...] = (
    # Extensions and in-app browsers inject their own scripts into the page.
    IgnoreRule('chrome-extension://', MATCH_URL),
    IgnoreRule('moz-extension://', MATCH_URL),
    IgnoreRule('safari-extension://', MATCH_URL),
    IgnoreRule('safari-web-extension://', MATCH_URL),
    IgnoreRule('webkit-masked-url://', MATCH_URL),
    # Browser quirks with no actionable cause.
    IgnoreRule('ResizeObserver loop', MATCH_MESSAGE),
    IgnoreRule('Script error.', MATCH_MESSAGE),
    IgnoreRule('Non-Error promise rejection captured', MATCH_MESSAGE),
    # The network went away: venue Wi-Fi, a phone locking, a deploy restarting.
    IgnoreRule('Load failed', MATCH_MESSAGE),
    IgnoreRule('Failed to fetch', MATCH_MESSAGE),
    IgnoreRule('NetworkError when attempting to fetch resource', MATCH_MESSAGE),
    IgnoreRule('The network connection was lost', MATCH_MESSAGE),
    IgnoreRule('AbortError', MATCH_MESSAGE),
    IgnoreRule('xhr poll error', MATCH_MESSAGE),
    IgnoreRule('websocket error', MATCH_MESSAGE),
    # Vue's or Quasar's own script never arrived, so Quasar has no Vue to read
    # (SGLMAN-1E) or its language pack has no Quasar to attach to (SGLMAN-1J).
    # The page reloads itself (install_framework_load_guard) and reports a
    # second failure as a message. Firefox says "window.Vue is undefined",
    # Chrome "Cannot destructure property '…' of 'window.Vue'".
    IgnoreRule('window.Vue', MATCH_MESSAGE),
    IgnoreRule('/static/lang/', MATCH_URL),
)


@dataclass(frozen=True)
class Snapshot:
    enabled: bool = True
    replay_on_error: bool = True
    sample_rate: float = 1.0
    rules: Tuple[IgnoreRule, ...] = ()


_current = Snapshot()


def current() -> Snapshot:
    return _current


def replace(snapshot: Snapshot) -> None:
    global _current
    _current = snapshot


def live_config(snapshot: Snapshot) -> Dict[str, Any]:
    """The part of the browser config a super-admin can change under open tabs."""
    return {
        'enabled': snapshot.enabled,
        'replay': snapshot.replay_on_error,
        'sampleRate': snapshot.sample_rate,
        'rules': [{'pattern': r.pattern, 'match': r.match} for r in snapshot.rules],
    }
