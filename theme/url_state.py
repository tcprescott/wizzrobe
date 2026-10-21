"""Every view a page can show has an address, a title, and a place in Back.

A page here is one websocket-driven document: switching a hub tab, My Schedule's
List / Timeline, the Event board's Schedule / Brackets / Community or a timeline
day redraws part of the page without loading a new one. Left alone, the address
bar and the browser tab's title stay on whatever was first loaded, so Back
leaves the page instead of undoing the switch, a reload lands somewhere else,
and a copied link opens the wrong view.

A switcher declares the query parameter it owns with :func:`url_param` and
calls :meth:`UrlParam.set` when the viewer changes it; the page's
:class:`UrlState` keeps the address bar, ``document.title`` and the history
entry in step. Back and Forward come back as a ``popstate`` that the state
replays: it moves the hub tab, then hands each parameter whose value differs
its ``on_restore``. A cold load reads the same parameters from the request, so
a reload or a shared link builds the view it names.

Two kinds of history entry:

* ``'push'`` for a real change of view (a tab, a sub-view, a day): Back undoes it.
* ``'replace'`` for a filter (a status chip, a search): the address keeps it,
  Back skips it.

A parameter belongs to the hub tab its control was built in, so switching tabs
drops the old tab's parameters from the address, and to its control's lifetime:
once the control is deleted (its view was switched away) it stops appearing.
"""

import json
import logging
from typing import Any, Awaitable, Callable, Literal, Optional, Union
from urllib.parse import parse_qsl, urlencode
from weakref import WeakKeyDictionary

from nicegui import Client, context, ui

__all__ = ['TITLE_SEPARATOR', 'UrlParam', 'UrlState', 'url_param', 'url_state']

logger = logging.getLogger(__name__)

History = Literal['push', 'replace']
Restore = Callable[[str], Union[Awaitable[None], None]]
Label = Callable[[str], Optional[str]]

TITLE_SEPARATOR = ' — '
POP_EVENT = 'wiz_url_pop'

_states: 'WeakKeyDictionary[Client, UrlState]' = WeakKeyDictionary()

# Installed once per document. ``popstate`` reports the address the browser moved
# to; the server works out what changed. Inline rather than a static file,
# because a lazily built tab can be the first thing on a page to need it.
_LISTENER_JS = (
    'if(!window.__wizUrlState){window.__wizUrlState=true;'
    "window.addEventListener('popstate',function(){"
    f"emitEvent('{POP_EVENT}',{{path:location.pathname,search:location.search}});"
    '});}'
)


class UrlParam:
    """One query parameter a control owns. Build with :func:`url_param`."""

    def __init__(
        self, state: 'UrlState', key: str, default: str, element: ui.element,
        label: Optional[Label], on_restore: Optional[Restore], history: History,
    ) -> None:
        self._state = state
        self.key = key
        self.default = default
        self.element = element
        self.label = label
        self.on_restore = on_restore
        self.history: History = history
        self.value = default

    @property
    def is_live(self) -> bool:
        return not self.element.is_deleted

    def set(self, value: Any) -> None:
        """The viewer changed the view: record it in the address, title and history.

        Call it after the switch has redrawn, so a parameter belonging to a view
        that was just switched away has already dropped out.
        """
        value = '' if value is None else str(value)
        if value == self.value:
            return
        self.value = value
        if self._state.restoring:
            return
        self._state.sync(history=self.history)


class UrlState:
    """The address, title and history of one page (one client)."""

    def __init__(self, client: Client) -> None:
        self.client = client
        try:
            request = client.request
            self.path = request.scope.get('root_path', '') + request.url.path
            self.query: dict[str, str] = dict(parse_qsl(request.url.query, keep_blank_values=False))
        except Exception:
            self.path, self.query = '/', {}
        # What the page called itself before any view was named in front of it.
        self.base_title: str = client.resolve_title() or 'Wizzrobe'
        self._params: list[UrlParam] = []
        #: The hub tab on show, and the container each tab builds into.
        self.tab: Optional[str] = None
        self._tab_containers: dict[str, ui.element] = {}
        self._on_path: Optional[Callable[[str], Awaitable[None]]] = None
        self.restoring = False
        client.layout.on(POP_EVENT, self._on_pop)
        with client:
            ui.run_javascript(_LISTENER_JS)

    # --- wiring for BaseLayout ------------------------------------------------

    def bind_tabs(
        self, containers: dict[str, ui.element], on_path: Callable[[str], Awaitable[None]],
    ) -> None:
        """A hub's tabs: where each builds, and how to show the tab a path names."""
        self._tab_containers = containers
        self._on_path = on_path

    def show_tab(self, label: str, path: str, *, history: Optional[History]) -> None:
        """A tab was switched to. ``history=None`` when Back/Forward did it."""
        self.tab = label
        if history is None:
            return
        self.path = path
        self.query = {}
        self.sync(history=history)

    # --- parameters -----------------------------------------------------------

    def _owner(self, element: ui.element) -> Optional[str]:
        ancestors = {id(a) for a in element.ancestors(include_self=True)}
        return next((label for label, c in self._tab_containers.items() if id(c) in ancestors), None)

    def _current(self, param: UrlParam) -> bool:
        if not param.is_live:
            return False
        owner = self._owner(param.element)
        return owner is None or owner == self.tab

    def register(
        self, key: str, default: str, element: ui.element, *,
        label: Optional[Label], on_restore: Optional[Restore], history: History,
    ) -> UrlParam:
        self._params = [p for p in self._params if p.is_live and not (p.key == key and self._owner(p.element) == self._owner(element))]
        param = UrlParam(self, key, default, element, label, on_restore, history)
        self._params.append(param)
        return param

    def requested(self, key: str) -> Optional[str]:
        """The value the address names for ``key``, if any."""
        return self.query.get(key)

    # --- writing the address --------------------------------------------------

    def title(self) -> str:
        parts = [p.label(p.value) for p in reversed(self._params) if self._current(p) and p.label]
        if self.tab:
            parts.append(self.tab)
        parts.append(self.base_title)
        return TITLE_SEPARATOR.join(p for p in parts if p)

    def url(self, *, keep_foreign: bool = False) -> str:
        """The address of what is on show.

        ``keep_foreign`` keeps parameters no control owns: the ones a DM link
        arrived with (``?schedule=``, ``?match=``) open something once, on
        arrival, so the address keeps them until the viewer moves on and then
        lets them go, rather than reopening the dialog on every reload after.
        """
        current = [p for p in self._params if self._current(p)]
        owned = {p.key for p in self._params}
        query = {k: v for k, v in self.query.items() if keep_foreign and k not in owned}
        for p in current:
            if p.value not in (p.default, ''):
                query[p.key] = p.value
        self.query = query
        return self.path + (f'?{urlencode(query)}' if query else '')

    def sync(self, *, history: History = 'replace', keep_foreign: bool = False) -> None:
        """Write the current view into the address bar, title and history."""
        url, title = self.url(keep_foreign=keep_foreign), self.title()
        self.client.title = title
        method = 'pushState' if history == 'push' else 'replaceState'
        with self.client:
            ui.run_javascript(
                f'history.{method}(history.state,"",{json.dumps(url)}+location.hash);'
                f'document.title={json.dumps(title)};'
            )

    def settle(self) -> None:
        """A late-built control registered: bring the address up to date in place."""
        if not self.restoring:
            self.sync(history='replace', keep_foreign=True)

    # --- Back and Forward -----------------------------------------------------

    async def _on_pop(self, e) -> None:
        args = e.args if isinstance(e.args, dict) else {}
        path = str(args.get('path') or self.path)
        query = dict(parse_qsl(str(args.get('search') or '').lstrip('?')))
        self.restoring = True
        try:
            self.path, self.query = path, query
            if self._on_path is not None:
                await self._on_path(path)
            # Restoring one parameter can rebuild a view that registers others;
            # those read the address as they build, so a fresh pass picks up
            # only what is still out of step.
            for _ in range(4):
                stale = [
                    p for p in self._params
                    if self._current(p) and p.value != query.get(p.key, p.default)
                ]
                if not stale:
                    break
                for p in stale:
                    if not self._current(p):
                        continue
                    p.value = query.get(p.key, p.default)
                    if p.on_restore is not None:
                        result = p.on_restore(p.value)
                        if isinstance(result, Awaitable):
                            await result
        except Exception:
            logger.exception('Restoring the view from history failed')
        finally:
            self.restoring = False
        title = self.title()
        self.client.title = title
        with self.client:
            ui.run_javascript(f'document.title={json.dumps(title)};')


def url_state(client: Optional[Client] = None) -> UrlState:
    """This page's address keeper, made on first use."""
    client = client or context.client
    state = _states.get(client)
    if state is None:
        state = _states[client] = UrlState(client)
    return state


def url_param(
    key: str,
    element: ui.element,
    *,
    default: Any,
    value: Any = None,
    label: Optional[Label] = None,
    on_restore: Optional[Restore] = None,
    history: History = 'push',
) -> UrlParam:
    """Bind the view ``element`` switches to the ``?key=`` query parameter.

    ``value`` is what the control shows now (``None`` means ``default``);
    read what the address asked for with ``url_state().requested(key)`` before
    building the control. ``label`` names a value for the browser tab's title
    (``None`` leaves it out). ``on_restore`` puts the control and its view back
    when Back or Forward lands on another value; it must not call
    :meth:`UrlParam.set` itself, but if it does, nothing is recorded.
    """
    state = url_state()
    default_s = '' if default is None else str(default)
    param = state.register(
        key, default_s, element, label=label, on_restore=on_restore, history=history,
    )
    param.value = default_s if value is None else str(value)
    if state.requested(key) != (None if param.value == default_s else param.value):
        state.settle()
    return param
