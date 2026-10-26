"""Per-tab state lets go of its page when NiceGUI deletes the client (SGLMAN-29).

A ``WeakKeyDictionary[Client, X]`` only drops an entry when the client dies,
and a value that points back at its client keeps it alive for ever: every
closed tab's whole element tree stayed in memory and every full collection
walked it again.
"""

import gc
import weakref

import theme.nav_badges as nav_badges
import theme.realtime as realtime
import theme.url_state as url_state_mod


class _Client:
    def __init__(self):
        self.delete_handlers = []

    def on_delete(self, handler):
        self.delete_handlers.append(handler)

    def delete(self):
        for handler in self.delete_handlers:
            handler()


class _HoldsClient:
    def __init__(self, client):
        self.client = client


def test_url_state_is_released_on_delete(monkeypatch):
    monkeypatch.setattr(url_state_mod, 'UrlState', _HoldsClient)
    client = _Client()
    state = url_state_mod.url_state(client)
    assert url_state_mod.url_state(client) is state
    gone = weakref.ref(client)

    client.delete()
    del client, state
    gc.collect()
    assert gone() is None


def test_nav_badges_are_released_on_delete(monkeypatch):
    client = _Client()

    class _Badge:
        def __init__(self, *_, **__):
            pass

        def classes(self, *_):
            return self

        def set_visibility(self, _):
            pass

    monkeypatch.setattr(nav_badges, 'context', type('_Ctx', (), {'client': client}))
    monkeypatch.setattr(nav_badges.ui, 'badge', _Badge)
    nav_badges.nav_count_badge('Matches', 2)
    nav_badges.nav_count_badge('Shifts', 1)
    assert len(client.delete_handlers) == 1
    gone = weakref.ref(client)

    client.delete()
    monkeypatch.undo()
    del client
    gc.collect()
    assert gone() is None


def test_live_subscriptions_end_on_delete_as_well_as_disconnect(monkeypatch):
    registered = {}
    monkeypatch.setattr(realtime, '_disconnect_installed', False)
    monkeypatch.setattr(realtime.app, 'on_disconnect', lambda h: registered.setdefault('disconnect', h))
    monkeypatch.setattr(realtime.app, 'on_delete', lambda h: registered.setdefault('delete', h))
    realtime._install_disconnect_cleanup()
    assert registered == {'disconnect': realtime._on_disconnect, 'delete': realtime._on_disconnect}
