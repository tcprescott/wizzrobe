"""The third-party software Wizzrobe ships, with each one's licence text.

Built from what is actually deployed, so it can't drift from the lockfiles:

* **Python packages**: every ``main``-group package in ``poetry.lock`` that is
  installed here, with its licence read from its own ``.dist-info``. A package
  locked only for another platform (``pywin32``) isn't installed, so it isn't
  shipped and isn't listed.
* **The editor bundle**: ``theme/markdown_editor/dist/THIRD_PARTY_LICENSES.txt``,
  which the bundle's build writes from every package esbuild pulled in.
* **What NiceGUI serves to the browser** (Vue, Quasar and friends), whose
  versions come from the banners of the files NiceGUI ships; and the fonts and
  icons under ``static/fonts``. Neither has a lockfile, so they are listed here.

The full set is read once per process; nothing here touches the database.
"""

from __future__ import annotations

import importlib.metadata as metadata
import logging
import re
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from itertools import pairwise
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

__all__ = ['GROUPS', 'Notice', 'NoticeGroup', 'all_notices', 'find_notice']

_ROOT = Path(__file__).resolve().parents[2]
_LICENSES = Path(__file__).parent / 'licenses'
_POETRY_LOCK = _ROOT / 'poetry.lock'
_EDITOR_NOTICES = _ROOT / 'theme' / 'markdown_editor' / 'dist' / 'THIRD_PARTY_LICENSES.txt'

WEB = 'web'
FONTS = 'fonts'
PYTHON = 'python'

GROUPS = (
    (WEB, 'Web libraries', 'Code that runs in your browser.'),
    (FONTS, 'Fonts and icons', 'Typefaces and icon sets the pages are drawn with.'),
    (PYTHON, 'Python packages', 'Code that runs on the server.'),
)


@dataclass(frozen=True)
class Notice:
    name: str
    version: str
    license: str
    url: Optional[str]
    text: str
    group: str

    @property
    def key(self) -> str:
        return f'{self.group}:{self.name}'

    @property
    def reading_text(self) -> str:
        """``text`` with each paragraph's hard line breaks joined.

        Licence files are wrapped at 72-100 columns for a terminal; on a phone
        each of those lines wraps again and the text reads as a ragged column.
        Indented lines, list items and short lines keep their own line.
        """
        return reflow(self.text)


@dataclass(frozen=True)
class NoticeGroup:
    key: str
    heading: str
    blurb: str
    notices: tuple[Notice, ...]


_KEEP_LINE_RE = re.compile(r'^([-*•]\s|\(?\w{1,3}[.)]\s|=|-{3,})')


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def reflow(text: str) -> str:
    out: list[str] = []
    for paragraph in re.split(r'\n\s*\n', text.strip('\n')):
        lines = [line.rstrip() for line in paragraph.split('\n')]
        margin = min(_indent(line) for line in lines if line.strip())
        lines = [line[margin:] for line in lines]
        joined = [lines[0]]
        for previous, line in pairwise(lines):
            keep = (
                _KEEP_LINE_RE.match(line.lstrip())
                or len(previous.strip()) < 40
                or _indent(line) != _indent(previous)
            )
            if keep:
                joined.append(line)
            else:
                joined[-1] = f'{joined[-1]} {line.strip()}'
        out.append('\n'.join(joined))
    return '\n\n'.join(out)


def _template(spdx: str, copyright: str) -> str:
    body = (_LICENSES / f'{spdx}.txt').read_text(encoding='utf-8').strip()
    return f'{copyright}\n\n{body}' if copyright else body


def _nicegui_dir() -> Optional[Path]:
    try:
        import nicegui
    except ImportError:
        return None
    return Path(nicegui.__file__).parent


def _banner_version(path: Optional[Path], pattern: str) -> str:
    if path is None or not path.is_file():
        return ''
    with path.open(encoding='utf-8', errors='replace') as handle:
        head = handle.read(600)
    match = re.search(pattern, head)
    return match.group(1) if match else ''


def _lock_version(path: Optional[Path], package: str) -> str:
    if path is None or not path.is_file():
        return ''
    import json

    packages = json.loads(path.read_text(encoding='utf-8')).get('packages', {})
    return packages.get(f'node_modules/{package}', {}).get('version', '')


def _vendored_version(name: str) -> str:
    path = _ROOT / 'static' / 'vendor' / name / 'VERSION'
    return path.read_text(encoding='utf-8').strip() if path.is_file() else ''


def _web_libraries() -> list[Notice]:
    base = _nicegui_dir()
    static = base / 'static' if base else None
    echart_lock = base / 'elements' / 'echart' / 'package-lock.json' if base else None
    rows = [
        ('Vue', _banner_version(static and static / 'vue.esm-browser.prod.js', r'vue v([\d.]+)'),
         'MIT', 'Copyright (c) 2018-present, Yuxi (Evan) You and Vue contributors', 'https://vuejs.org'),
        ('Quasar', _banner_version(static and static / 'quasar.umd.prod.js', r'Quasar Framework v([\d.]+)'),
         'MIT', 'Copyright (c) 2015-present Razvan Stoenescu', 'https://quasar.dev'),
        ('Socket.IO client', _banner_version(static and static / 'socket.io.min.js', r'Socket\.IO v([\d.]+)'),
         'MIT', 'Copyright (c) 2014-present Guillermo Rauch and Socket.IO contributors', 'https://socket.io'),
        ('DOMPurify', _banner_version(static and static / 'dompurify.mjs', r'DOMPurify ([\d.]+)'),
         'Apache-2.0 OR MPL-2.0', 'Copyright 2025 Dr.-Ing. Mario Heiderich, Cure53', 'https://github.com/cure53/DOMPurify'),
        ('Tailwind CSS', _banner_version(static and static / 'tailwindcss.min.js', r'="([\d.]+)"'),
         'MIT', 'Copyright (c) Tailwind Labs, Inc.', 'https://tailwindcss.com'),
        ('Apache ECharts', _lock_version(echart_lock, 'echarts'),
         'Apache-2.0', 'Copyright 2017-2025 The Apache Software Foundation', 'https://echarts.apache.org'),
        ('ZRender', _lock_version(echart_lock, 'zrender'),
         'BSD-3-Clause', 'Copyright (c) 2013, Baidu Inc.', 'https://github.com/ecomfe/zrender'),
        ('tslib', _lock_version(echart_lock, 'tslib'),
         '0BSD', 'Copyright (c) Microsoft Corporation.', 'https://github.com/microsoft/tslib'),
        ('PDF.js', _vendored_version('pdfjs'),
         'Apache-2.0', 'Copyright 2024 Mozilla Foundation', 'https://mozilla.github.io/pdf.js/'),
    ]
    template_for = {'Apache-2.0 OR MPL-2.0': 'Apache-2.0'}
    return [
        Notice(name=name, version=version, license=spdx, url=url, group=WEB,
               text=_template(template_for.get(spdx, spdx), copyright))
        for name, version, spdx, copyright, url in rows
    ]


_FONTS = (
    ('Atkinson Hyperlegible', 'OFL-1.1',
     'Copyright 2020 Braille Institute of America, Inc.', 'https://www.brailleinstitute.org/freefont/'),
    ('IBM Plex Mono', 'OFL-1.1',
     'Copyright © 2017 IBM Corp. with Reserved Font Name "Plex"', 'https://github.com/IBM/plex'),
    ('Material Symbols', 'Apache-2.0', 'Copyright 2022 Google LLC',
     'https://github.com/google/material-design-icons'),
    ('Material Icons', 'Apache-2.0', 'Copyright 2015 Google LLC',
     'https://github.com/google/material-design-icons'),
    ('Roboto', 'OFL-1.1', 'Copyright 2011 The Roboto Project Authors', 'https://github.com/googlefonts/roboto-3-classic'),
)


def _fonts() -> list[Notice]:
    return [
        Notice(name=name, version='', license=spdx, url=url, group=FONTS, text=_template(spdx, copyright))
        for name, spdx, copyright, url in _FONTS
    ]


_EDITOR_HEADER_RE = re.compile(r'^(?P<name>(?:@[^/\s]+/)?[^@\s]+)@(?P<version>\S+) \((?P<license>[^)]*)\)$')


def _editor_bundle(path: Path = _EDITOR_NOTICES) -> list[Notice]:
    if not path.is_file():
        logger.warning('Editor bundle notices missing at %s.', path)
        return []
    body = path.read_text(encoding='utf-8').split('\n\n', 1)[-1]
    notices = []
    for chunk in re.split(r'\n-{72}\n', body):
        header, _, text = chunk.strip().partition('\n\n')
        match = _EDITOR_HEADER_RE.match(header.strip())
        if not match:
            continue
        name = match['name']
        notices.append(Notice(
            name=name, version=match['version'], license=match['license'],
            url=f'https://www.npmjs.com/package/{name}', text=text.strip(), group=WEB,
        ))
    return notices


_CLASSIFIER_SPDX = {
    'MIT License': 'MIT',
    'BSD License': 'BSD',
    'Apache Software License': 'Apache-2.0',
    'Mozilla Public License 2.0 (MPL 2.0)': 'MPL-2.0',
    'Python Software Foundation License': 'PSF-2.0',
    'ISC License (ISCL)': 'ISC',
    'GNU Lesser General Public License v3 (LGPLv3)': 'LGPL-3.0',
    'GNU General Public License (GPL)': 'GPL',
}

_LICENSE_FILE_RE = re.compile(r'(licen[cs]e|copying|notice|authors)', re.IGNORECASE)


def _license_name(meta) -> str:
    expression = (meta.get('License-Expression') or '').strip()
    if expression:
        return expression
    field = (meta.get('License') or '').strip()
    if field and '\n' not in field and len(field) <= 40 and field.upper() != 'UNKNOWN':
        return field
    names = [
        _CLASSIFIER_SPDX.get(c.rsplit(' :: ', 1)[-1], c.rsplit(' :: ', 1)[-1])
        for c in meta.get_all('Classifier') or []
        if c.startswith('License :: ') and c != 'License :: OSI Approved'
    ]
    return ' OR '.join(names)


def _project_url(meta) -> Optional[str]:
    urls = {}
    for entry in meta.get_all('Project-URL') or []:
        label, _, url = entry.partition(',')
        urls[label.strip().lower()] = url.strip()
    for label in ('homepage', 'home', 'source', 'repository', 'source code', 'documentation'):
        if urls.get(label):
            return urls[label]
    return (meta.get('Home-page') or '').strip() or next(iter(urls.values()), None)


def _sniff_license(text: str) -> str:
    """Name a licence from its text, for a package whose metadata names none."""
    if 'Permission is hereby granted, free of charge' in text:
        return 'MIT'
    if 'Apache License' in text and 'Version 2.0' in text:
        return 'Apache-2.0'
    if 'Redistribution and use in source and binary forms' in text:
        return 'BSD'
    return 'See licence text'


def _license_text(dist) -> str:
    files = [
        f for f in dist.files or []
        if '.dist-info' in str(f) and _LICENSE_FILE_RE.search(f.name)
    ]
    texts = []
    for file in sorted(files, key=str):
        try:
            texts.append(file.read_text(encoding='utf-8').strip())
        except (OSError, UnicodeDecodeError):
            continue
    if texts:
        return '\n\n'.join(t for t in texts if t)
    field = (dist.metadata.get('License') or '').strip()
    return field if len(field) > 40 else f'Licensed under {_license_name(dist.metadata) or "an unnamed licence"}.'


def _main_group_packages(lock_path: Path = _POETRY_LOCK) -> list[tuple[str, str]]:
    with lock_path.open('rb') as handle:
        lock = tomllib.load(handle)
    return [
        (package['name'], package['version'])
        for package in lock.get('package', [])
        if 'main' in package.get('groups', ['main'])
    ]


def _python_packages() -> list[Notice]:
    notices = []
    for name, locked_version in _main_group_packages():
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        meta = dist.metadata
        text = _license_text(dist)
        notices.append(Notice(
            name=meta.get('Name') or name, version=dist.version or locked_version,
            license=_license_name(meta) or _sniff_license(text), url=_project_url(meta),
            text=text, group=PYTHON,
        ))
    return notices


@lru_cache(maxsize=1)
def all_notices() -> tuple[NoticeGroup, ...]:
    found = {
        WEB: _web_libraries() + _editor_bundle(),
        FONTS: _fonts(),
        PYTHON: _python_packages(),
    }
    return tuple(
        NoticeGroup(
            key=key, heading=heading, blurb=blurb,
            notices=tuple(sorted(found[key], key=lambda n: n.name.lower())),
        )
        for key, heading, blurb in GROUPS
    )


def find_notice(key: str) -> Optional[Notice]:
    for group in all_notices():
        for notice in group.notices:
            if notice.key == key:
                return notice
    return None
