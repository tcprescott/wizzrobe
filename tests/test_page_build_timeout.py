"""A restart sends every open tab back at once; a slow build must finish, not fail."""

import inspect

from nicegui import ui

from application.utils import page_build_timeout


def test_page_build_timeout_is_raised_for_every_page():
    page_build_timeout.install()

    assert ui.page('/_restart-load-default').response_timeout == page_build_timeout.PAGE_BUILD_TIMEOUT_SECONDS
    assert ui.page('/_restart-load-own', response_timeout=5).response_timeout == 5


def test_page_build_timeout_installs_before_pages_register():
    import frontend

    source = inspect.getsource(frontend.init)
    assert 'page_build_timeout.install()' in source
    assert source.index('page_build_timeout.install()') < source.index('auth.create()')
