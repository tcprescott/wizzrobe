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

import inspect
import json
import logging
from contextlib import asynccontextmanager
from datetime import date
from typing import Any, AsyncIterator, Awaitable, Callable, Iterable, Literal, Optional
from urllib.parse import parse_qsl, urlencode
from weakref import WeakKeyDictionary

from nicegui import Client, context, ui
from nicegui.elements.mixins.value_element import ValueElement

__all__ = ['PUSH_ATTR', 'TAIL', 'TITLE_SEPARATOR', 'UrlParam', 'UrlState', 'bind_url', 'iso_day', 'url_param', 'url_state']

logger = logging.getLogger(__name__)

History = Literal['push', 'replace']
Restore = Callable[[str], Any]
Label = Callable[[str], Optional[str]]

TITLE_SEPARATOR = ' — '
POP_EVENT = 'wiz_url_pop'
#: The key of a parameter carried as path segments under the hub tab's path
#: rather than in the query string (``/admin/settings/<pane>``).
TAIL = '/'

_states: 'WeakKeyDictionary[Client, UrlState]' = WeakKeyDictionary()

#: An element carrying this attribute (a hub tab's drawer row or bottom-bar tab)
#: pushes its address in the click itself. Safari, and Chrome after five
#: seconds, skip a Back entry that was pushed without a click to hand: a push
#: that waits on a slow server round trip left Back with nowhere to go.
PUSH_ATTR = 'data-wiz-push'

# Installed once per document. ``popstate`` reports the address the browser moved
# to; the server works out what changed. Inline rather than a static file,
# because a lazily built tab can be the first thing on a page to need it.
_LISTENER_JS = (
    'if(!window.__wizUrlState){window.__wizUrlState=true;'
    "window.addEventListener('popstate',function(){"
    f"emitEvent('{POP_EVENT}',{{path:location.pathname,search:location.search}});"
    '});'
    "document.addEventListener('click',function(e){"
    f"var el=e.target.closest&&e.target.closest('[{PUSH_ATTR}]');"
    f"var to=el&&el.getAttribute('{PUSH_ATTR}');"
    'if(to&&location.pathname!==to)history.pushState(history.state,"",to);'
    '},true);}'
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
    def state(self) -> 'UrlState':
        return self._state

    @asynccontextmanager
    async def changing(self, value: Any) -> AsyncIterator[None]:
        """Switch to ``value`` around a redraw: ``async with p.changing(v): await body.refresh()``.

        For a switch whose redraw builds or drops controls with parameters of
        their own (My Schedule's Timeline brings its day). Those settle quietly
        while the redraw runs, and would otherwise rewrite the entry being left;
        here they wait, and the one new entry is written once the redraw is done,
        with exactly the parameters the new view has.
        """
        self._state.busy += 1
        try:
            yield
        finally:
            self._state.busy -= 1
        self.set(value)

    @property
    def is_live(self) -> bool:
        return not self.element.is_deleted

    def set(self, value: Any, *, history: Optional[History] = None) -> None:
        """The viewer changed the view: record it in the address, title and history.

        Call it after the switch has redrawn, so a parameter belonging to a view
        that was just switched away has already dropped out. ``history='replace'``
        is for a value the page settled on by itself (the opening day once the
        days are known), which is no step to go Back over.
        """
        value = '' if value is None else str(value)
        if value == self.value:
            return
        self.value = value
        if self._state.restoring:
            return
        if history == 'replace':
            self._state.settle()
        else:
            self._state.sync(history=history or self.history)


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
        # Path segments under the hub tab's own (``/admin/settings/<pane>``),
        # owned by the :data:`TAIL` parameter.
        self.tail = ''
        # What the page called itself before any view was named in front of it.
        self.base_title: str = client.resolve_title() or 'Wizzrobe'
        self._params: list[UrlParam] = []
        #: The hub tab on show, and the container each tab builds into.
        self.tab: Optional[str] = None
        self._tab_containers: dict[str, ui.element] = {}
        self._on_slug: Optional[Callable[[str], Awaitable[None]]] = None
        self._hub_base = ''
        #: The pathname the browser was last told to show (or arrived at by
        #: Back/Forward). A replace is only applied while the browser is still
        #: there: one computed before a Back that the server hadn't heard of
        #: yet would otherwise rewrite the entry Back just landed on.
        self._at = self.path
        self.restoring = False
        #: Switches mid-redraw (:meth:`UrlParam.changing`): late controls wait.
        self.busy = 0
        client.layout.on(POP_EVENT, self._on_pop)
        with client:
            ui.run_javascript(_LISTENER_JS)

    # --- wiring for BaseLayout ------------------------------------------------

    def bind_hub(
        self, base_path: Optional[str], section: Optional[str], tab: str, slug: str,
        containers: dict[str, ui.element], on_slug: Callable[[str], Awaitable[None]],
    ) -> None:
        """A hub's tabs (``/admin/<slug>``): where each builds, the one on show,
        and how to show the tab a slug names when Back/Forward land on it.

        A bare hub path keeps its address until the first switch (Home's ``/``
        is the community's front door); a named section normalizes to the tab's
        current slug, keeping anything under it (``/admin/settings/<pane>``).
        """
        self._tab_containers = containers
        self._on_slug = on_slug
        self._hub_base = (base_path or '').rstrip('/')
        self.tab = tab
        if base_path is not None and section:
            self.set_tab_path(f'{self._hub_base}/{section}')
            self.path = f'{self._hub_base}/{slug}'

    def show_tab(self, label: str, path: str, *, history: Optional[History]) -> None:
        """A tab was switched to. ``history=None`` when Back/Forward did it."""
        self.tab = label
        if history is None:
            return
        self.path = path
        self.tail = ''
        self.query = {}
        self.sync(history=history)

    def set_tab_path(self, path: str) -> None:
        """The address's hub-tab part is ``path``; anything under it is the tail."""
        full = self.path + self.tail
        self.path = path
        self.tail = full[len(path):] if full.startswith(path + '/') else ''

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
        if key == TAIL:
            return self.tail.strip('/') or None
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
        tail = self.tail if keep_foreign and TAIL not in owned else ''
        for p in current:
            if p.value in (p.default, ''):
                continue
            if p.key == TAIL:
                tail = f'/{p.value}'
            else:
                query[p.key] = p.value
        self.query, self.tail = query, tail
        return self.path + tail + (f'?{urlencode(query)}' if query else '')

    def sync(self, *, history: History = 'replace', keep_foreign: bool = False) -> None:
        """Write the current view into the address bar, title and history."""
        url, title = self.url(keep_foreign=keep_foreign), self.title()
        self.client.title = title
        target, label = json.dumps(url), json.dumps(title)
        if history == 'push':
            # A push the click already made (``PUSH_ATTR``) only needs the
            # title and query brought up to date, not a second Back step.
            script = (f'history[location.pathname+location.search==={target}?"replaceState":"pushState"]'
                      f'(history.state,"",{target}+location.hash);document.title={label};')
        else:
            script = (f'if(location.pathname==={json.dumps(self._at)}){{'
                      f'history.replaceState(history.state,"",{target}+location.hash);document.title={label};}}')
        self._at = url.split('?', 1)[0]
        with self.client:
            ui.run_javascript(script)

    def settle(self) -> None:
        """A late-built control registered: bring the address up to date in place."""
        if not self.restoring and not self.busy:
            self.sync(history='replace', keep_foreign=True)

    # --- Back and Forward -----------------------------------------------------

    async def _on_pop(self, e) -> None:
        args = e.args if isinstance(e.args, dict) else {}
        path = str(args.get('path') or self.path)
        query = dict(parse_qsl(str(args.get('search') or '').lstrip('?')))
        self.restoring = True
        self._at = path
        try:
            self.path, self.tail, self.query = path, '', query
            if self._on_slug is not None:
                base = self._hub_base
                slug = path[len(base):].strip('/').split('/', 1)[0] if base and path.startswith(base + '/') else ''
                if slug:
                    self.set_tab_path(f'{base}/{slug}')
                await self._on_slug(slug)
            # Restoring one parameter can rebuild a view that registers others;
            # those read the address as they build, so a fresh pass picks up
            # only what is still out of step.
            for _ in range(4):
                stale = [
                    p for p in self._params
                    if self._current(p) and p.value != (self.requested(p.key) or p.default)
                ]
                if not stale:
                    break
                for p in stale:
                    if not self._current(p):
                        continue
                    p.value = self.requested(p.key) or p.default
                    if p.on_restore is not None:
                        result = p.on_restore(p.value)
                        if inspect.isawaitable(result):
                            await result
        except Exception:
            logger.exception('Restoring the view from history failed')
        finally:
            self.restoring = False
        title = self.title()
        self.client.title = title
        with self.client:
            ui.run_javascript(f'document.title={json.dumps(title)};')


def iso_day(value: Optional[str]) -> Optional[date]:
    """``?day=2026-10-23`` as a date, or ``None`` when absent or malformed."""
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def url_state(client: Optional[Client] = None) -> UrlState:
    """This page's address keeper, made on first use."""
    client = client or context.client
    state = _states.get(client)
    if state is None:
        state = _states[client] = UrlState(client)
        # The state holds its client, so the weak key alone never lets go and
        # every closed tab's whole page stayed in memory (SGLMAN-29).
        client.on_delete(lambda: _states.pop(client, None))
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


def _plain(value: Any) -> str:
    """A tab's name (``ui.tabs`` values can be the ``ui.tab`` itself), else ``str``."""
    if isinstance(value, ui.tab):
        return str(value.props.get('name'))
    return '' if value is None else str(value)


def bind_url(
    key: str,
    element: ValueElement,
    *,
    default: Any = None,
    choices: Optional[Iterable[Any]] = None,
    label: Optional[Label] = None,
    history: History = 'push',
) -> UrlParam:
    """Tie a switch whose value *is* the view (``ui.tabs``, ``ui.toggle``,
    ``ui.select``) to ``?key=``, in both directions.

    Call it after the element's own change handler is attached: a value the
    address asks for is applied through ``set_value``, so that handler shows the
    view, and Back/Forward go the same way. ``choices`` are the values the
    address may name (anything else opens ``default``); it defaults to the
    element's options where it has them. ``default`` defaults to the element's
    current value.
    """
    default_s = _plain(element.value if default is None else default)
    options = getattr(element, 'options', None)
    if choices is None and isinstance(options, (list, dict)):
        choices = options
    allowed = None if choices is None else {_plain(c) for c in choices}

    def valid(value: Optional[str]) -> Optional[str]:
        return value if value is not None and (allowed is None or value in allowed) else None

    def restore(value: str) -> None:
        element.set_value(valid(value) or default_s)

    asked = valid(url_state().requested(key))
    if asked is not None and asked != _plain(element.value):
        element.set_value(asked)
    param = url_param(
        key, element, default=default_s, value=_plain(element.value),
        label=label, on_restore=restore, history=history,
    )
    element.on_value_change(lambda e: param.set(_plain(e.value)))
    return param
