"""The About section: its pages' wiring, the privacy policy, and the notices.

The notices are the part that can quietly rot: they're meant to list what ships,
so the tests hold them to the lockfiles rather than to a snapshot.
"""

import inspect
import json
import re
import tomllib
from importlib import metadata
from pathlib import Path

import pages.about as about_page
import pages.help as help_page
import theme.base as base
from application.about import all_notices, find_notice, privacy_blocks
from application.about.notices import FONTS, PYTHON, WEB, _editor_bundle
from application.content import plain_text
from middleware.auth import protected_routes

ROOT = Path(__file__).resolve().parents[1]
ROUTES = ('/about', '/about/privacy', '/about/notices')


def _group(key):
    return next(group for group in all_notices() if group.key == key)


class TestAboutPages:
    def test_registers_every_route_as_public(self):
        source = inspect.getsource(about_page.create)
        for route in ROUTES:
            assert f"@public_page('{route}')" in source

    def test_routes_never_join_protected_routes(self):
        about_page.create()
        for route in ROUTES:
            assert route not in protected_routes

    def test_pages_tolerate_an_anonymous_visitor(self):
        source = inspect.getsource(about_page)
        assert not re.search(r'(?<!storage\.)\buser\.\w', source)

    def test_never_renders_through_markdown(self):
        assert 'ui.markdown' not in inspect.getsource(about_page)

    def test_frontend_registers_the_pages(self):
        assert 'about.create()' in (ROOT / 'frontend.py').read_text(encoding='utf-8')

    def test_help_index_links_about_and_the_drawer_does_not(self):
        assert "ui.navigate.to('/about')" in inspect.getsource(help_page)
        assert "'/about'" not in inspect.getsource(base)

    def test_says_claude_helped_build_it(self):
        assert 'written with Claude' in inspect.getsource(about_page)


class TestPrivacyPolicy:
    def test_parses_into_sections(self):
        headings = [plain_text([b]) for b in privacy_blocks() if b.kind == 'heading' and b.level == 2]
        assert 'What we collect' in headings
        assert "Where it's stored" in headings
        assert 'Your choices' in headings

    def test_states_us_data_residency(self):
        text = plain_text(list(privacy_blocks()))
        assert 'stored on servers in the United States' in text

    def test_names_every_cookie_the_app_sets(self):
        text = (ROOT / 'application' / 'about' / 'privacy.md').read_text(encoding='utf-8')
        for cookie in ('session', 'wiz_tz', 'wiz_dark'):
            assert f'`{cookie}`' in text


class TestNotices:
    def test_lists_every_installed_main_group_python_package(self):
        lock = tomllib.loads((ROOT / 'poetry.lock').read_text(encoding='utf-8'))
        expected = set()
        for package in lock['package']:
            if 'main' not in package.get('groups', ['main']):
                continue
            try:
                metadata.distribution(package['name'])
            except metadata.PackageNotFoundError:
                continue
            expected.add(metadata.distribution(package['name']).metadata['Name'].lower())
        listed = {n.name.lower() for n in _group(PYTHON).notices}
        assert listed == expected

    def test_dev_only_packages_are_left_out(self):
        listed = {n.name.lower() for n in _group(PYTHON).notices}
        assert 'pytest' not in listed
        assert 'ruff' not in listed

    def test_editor_bundle_matches_its_lockfile(self):
        lock = json.loads((ROOT / 'theme' / 'markdown_editor' / 'package-lock.json').read_text(encoding='utf-8'))
        runtime = {
            key.removeprefix('node_modules/'): entry['version']
            for key, entry in lock['packages'].items()
            if key and not entry.get('dev')
        }
        bundled = _editor_bundle()
        assert bundled, 'THIRD_PARTY_LICENSES.txt parsed to nothing'
        for notice in bundled:
            assert runtime.get(notice.name) == notice.version, notice.name
            assert 'Permission is hereby granted' in notice.text

    def test_browser_libraries_carry_a_version_and_copyright(self):
        for name in ('Vue', 'Quasar', 'Socket.IO client', 'DOMPurify', 'Apache ECharts'):
            notice = find_notice(f'{WEB}:{name}')
            assert notice is not None, name
            assert re.fullmatch(r'\d+(\.\d+)+', notice.version), (name, notice.version)
            assert notice.text.startswith('Copyright'), name

    def test_every_self_hosted_font_is_listed(self):
        listed = {n.name for n in _group(FONTS).notices}
        assert {'Atkinson Hyperlegible', 'IBM Plex Mono', 'Material Symbols'} <= listed

    def test_every_notice_has_a_licence_and_its_text(self):
        for group in all_notices():
            for notice in group.notices:
                assert notice.license, notice.key
                assert len(notice.text) > 15, notice.key


class TestReflow:
    def test_joins_a_paragraphs_hard_wraps(self):
        from application.about.notices import reflow

        text = (
            'Permission is hereby granted, free of charge, to any person obtaining\n'
            'a copy of this software.\n\n'
            '   1. Definitions.\n\n'
            '      "License" shall mean the terms and conditions for use, reproduction,\n'
            '      and distribution.\n'
            '      - one item that stays on its own line\n'
        )
        assert reflow(text) == (
            'Permission is hereby granted, free of charge, to any person obtaining a copy of this software.\n\n'
            '1. Definitions.\n\n'
            '"License" shall mean the terms and conditions for use, reproduction, and distribution.\n'
            '- one item that stays on its own line'
        )
