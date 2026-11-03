"""The Needs you inbox must rebuild without an empty frame.

``.schedule-layout__inbox:empty`` hides the inbox, so an async refreshable that
clears and then awaits a query leaves it empty mid-refresh and the board beside
it jumps to full width and back. The query runs before the clear instead, which
needs the refreshable builder itself to be synchronous.
"""

import inspect

from theme.match_inbox import MatchInbox


def test_inbox_builder_is_synchronous():
    assert not inspect.iscoroutinefunction(MatchInbox._build.func)
