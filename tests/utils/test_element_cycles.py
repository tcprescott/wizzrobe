"""A deleted element's Classes/Style/Props are freed with it, not by a full pass."""

import gc
import weakref
from contextlib import contextmanager

from nicegui.classes import Classes
from nicegui.props import Props
from nicegui.style import Style

from application.utils import element_cycles


class _Element:
    def __init__(self):
        self._classes = Classes([], element=self)
        self._style = Style({}, element=self)
        self._props = Props({}, element=self)


@contextmanager
def _gc_off():
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if was_enabled:
            gc.enable()
        gc.collect()


def _refs(element) -> list:
    return [weakref.ref(getattr(element, name)) for name in element_cycles._COLLECTIONS]


def test_nicegui_collections_are_self_cycles():
    # If NiceGUI stops registering its own _update this fix is no longer needed.
    with _gc_off():
        element = _Element()
        refs = _refs(element)
        del element
        assert all(ref() is not None for ref in refs)


def test_released_collections_are_freed_by_refcount():
    with _gc_off():
        element = _Element()
        refs = _refs(element)
        element_cycles.release(element)
        del element
        assert all(ref() is None for ref in refs)


def test_release_tolerates_elements_without_collections():
    element_cycles.release(object())


def test_remove_elements_releases_after_removing(monkeypatch):
    removed = []
    monkeypatch.setattr(element_cycles, '_original', lambda client, elements: removed.extend(elements))
    with _gc_off():
        elements = [_Element(), _Element()]
        refs = [ref for e in elements for ref in _refs(e)]
        element_cycles._remove_elements(None, iter(elements))
        assert removed == elements
        del elements, removed[:]
        assert all(ref() is None for ref in refs)


def test_install_patches_client_and_uninstall_restores():
    from nicegui.client import Client

    original = Client.remove_elements
    element_cycles.install()
    try:
        assert Client.remove_elements is element_cycles._remove_elements
    finally:
        element_cycles.uninstall()
    assert Client.remove_elements is original
