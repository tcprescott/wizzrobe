"""Parsing ``changelog.md`` and the per-PR ``changes/`` files into dated entries.

Each entry opens with a level-two heading carrying an ISO 8601 date interval and
a title — ``## 2026-10-05/2026-10-11 Event check-in`` — and runs to the next one.
The interval is calendar dates, not instants, so there is no timezone to apply.
A heading that does not match is logged and its section dropped rather than
raised: a typo in the file must not take the page down. ``test_changelog``
fails the build on the same typo, so it never reaches a reader.

A PR adds its line as a file of its own under ``changes/``, named
``YYYY-MM-DD-<topic>.md`` and holding only bullets, rather than editing the
current week's entry: every PR merged in the same week used to append to the
same few lines of ``changelog.md`` and conflict. Each file joins the entry that
covers its date, or, when ``changelog.md`` has none, a Monday-to-Sunday entry
made for that week. Giving that week a headline is an optional one-line heading
in ``changelog.md``, which the files' bullets then fill.

Inside an entry, and inside every change file, bullets sit under a category
heading (``### New``, ``### Improved``, ``### Fixed``, ``### Security`` or
``### Behind the scenes``) and the page shows each week's lines grouped in that
order. A bullet with no heading, or under a heading that isn't one of those, is
logged and shown last under "More changes" rather than dropped: a change file
written before categories existed still reaches the page, and
``test_changelog`` catches it before it ships.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from application.content import Block, parse_blocks

logger = logging.getLogger(__name__)

__all__ = [
    'CATEGORIES',
    'UNSORTED',
    'ChangelogEntry',
    'ChangelogSection',
    'all_entries',
    'apply_changes',
    'change_file_date',
    'parse_changelog',
    'reload_entries',
    'split_categories',
]

CHANGELOG_PATH = Path(__file__).parent / 'changelog.md'
CHANGES_DIR = Path(__file__).parent / 'changes'

CHANGE_FILE_RE = re.compile(r'^(?P<date>\d{4}-\d{2}-\d{2})-[a-z0-9]+(?:-[a-z0-9]+)*\.md$')

UNTITLED_WEEK = 'Updates'

CATEGORIES = ('New', 'Improved', 'Fixed', 'Security', 'Behind the scenes')

UNSORTED = 'More changes'

CATEGORY_HEADING_RE = re.compile(r'^### (?P<name>\S.*?)\s*$')

ENTRY_HEADING_RE = re.compile(
    r'^## (?P<start>\d{4}-\d{2}-\d{2})/(?P<end>\d{4}-\d{2}-\d{2}) (?P<title>\S.*?)\s*$'
)


@dataclass(frozen=True)
class ChangelogSection:
    category: str
    blocks: tuple[Block, ...]

    @property
    def count(self) -> int:
        """How many lines the section lists."""
        return sum(len(b.items) for b in self.blocks if b.kind == 'bullets')


@dataclass(frozen=True)
class ChangelogEntry:
    start: date
    end: date
    title: str
    sections: tuple[ChangelogSection, ...]

    @property
    def blocks(self) -> tuple[Block, ...]:
        """Every block in the entry, section after section."""
        return tuple(b for s in self.sections for b in s.blocks)

    @property
    def anchor(self) -> str:
        return self.start.isoformat()

    @property
    def period(self) -> str:
        """``Oct 5 – 11, 2026``, widening only as far as the interval needs."""
        start, end = self.start, self.end
        if start == end:
            return f'{start:%b} {start.day}, {start.year}'
        if start.year != end.year:
            return f'{start:%b} {start.day}, {start.year} – {end:%b} {end.day}, {end.year}'
        if start.month != end.month:
            return f'{start:%b} {start.day} – {end:%b} {end.day}, {end.year}'
        return f'{start:%b} {start.day} – {end.day}, {end.year}'


def split_categories(text: str, source: str) -> list[tuple[str, list[Block]]]:
    """``text`` cut at its ``### Category`` headings, each run parsed into blocks.

    ``source`` names the entry or file in the log line a stray bullet produces.
    """
    runs: list[tuple[str, list[str]]] = [(UNSORTED, [])]
    for line in text.splitlines():
        match = CATEGORY_HEADING_RE.match(line)
        if match is None:
            runs[-1][1].append(line)
            continue
        name = match['name']
        if name not in CATEGORIES:
            logger.warning('changelog: unknown category %r in %s; shown as %s', name, source, UNSORTED)
            name = UNSORTED
        runs.append((name, []))
    if any(line.strip() for line in runs[0][1]):
        logger.warning('changelog: lines before any category heading in %s; shown as %s', source, UNSORTED)
    return [(name, blocks) for name, lines in runs if (blocks := parse_blocks('\n'.join(lines)))]


def _merge_bullets(blocks: list[Block]) -> tuple[Block, ...]:
    """Adjacent bullet lists from separate files read as one list."""
    merged: list[Block] = []
    for block in blocks:
        if merged and block.kind == 'bullets' and merged[-1].kind == 'bullets':
            merged[-1] = replace(merged[-1], items=merged[-1].items + block.items)
        else:
            merged.append(block)
    return tuple(merged)


def _sections(runs: list[tuple[str, list[Block]]]) -> tuple[ChangelogSection, ...]:
    """One section per category in page order, each category's runs merged."""
    by_category: dict[str, list[Block]] = {}
    for name, blocks in runs:
        by_category.setdefault(name, []).extend(blocks)
    return tuple(
        ChangelogSection(name, _merge_bullets(by_category[name]))
        for name in (*CATEGORIES, UNSORTED)
        if name in by_category
    )


def _entry(heading: str, body: list[str]) -> ChangelogEntry | None:
    match = ENTRY_HEADING_RE.match(heading)
    if match is None:
        logger.warning('changelog: unrecognised entry heading %r; section skipped', heading)
        return None
    try:
        start = date.fromisoformat(match['start'])
        end = date.fromisoformat(match['end'])
    except ValueError:
        logger.warning('changelog: invalid date in heading %r; section skipped', heading)
        return None
    if end < start:
        logger.warning('changelog: heading %r ends before it starts; section skipped', heading)
        return None
    return ChangelogEntry(
        start=start,
        end=end,
        title=match['title'],
        sections=_sections(split_categories('\n'.join(body), heading)),
    )


def parse_changelog(text: str) -> list[ChangelogEntry]:
    """Every well-formed entry in ``text``, newest first."""
    entries: list[ChangelogEntry] = []
    heading: str | None = None
    body: list[str] = []
    for line in text.splitlines():
        if line.startswith('## '):
            if heading is not None and (entry := _entry(heading, body)):
                entries.append(entry)
            heading, body = line.rstrip(), []
        elif heading is not None:
            body.append(line)
    if heading is not None and (entry := _entry(heading, body)):
        entries.append(entry)
    return sorted(entries, key=lambda e: e.start, reverse=True)


def change_file_date(name: str) -> date | None:
    """The date a ``changes/`` file is filed under, or ``None`` for a malformed name."""
    match = CHANGE_FILE_RE.match(name)
    if match is None:
        return None
    try:
        return date.fromisoformat(match['date'])
    except ValueError:
        return None


def apply_changes(
    entries: list[ChangelogEntry], changes: list[tuple[date, str]]
) -> list[ChangelogEntry]:
    """Fold ``(date, text)`` change files into ``entries``, newest first.

    A change joins the entry whose interval covers its date; otherwise it joins
    (or opens) an untitled entry for its Monday-to-Sunday week. Within each
    category the files keep the order given, which the loader makes
    date-then-name.
    """
    extra: dict[date, list[tuple[str, list[Block]]]] = {}
    bodies = {e.start: [(s.category, list(s.blocks)) for s in e.sections] for e in entries}
    for day, text in changes:
        runs = split_categories(text, f'a change file dated {day.isoformat()}')
        owner = next((e for e in entries if e.start <= day <= e.end), None)
        if owner is not None:
            bodies[owner.start].extend(runs)
        else:
            extra.setdefault(day - timedelta(days=day.weekday()), []).extend(runs)
    result = [
        ChangelogEntry(e.start, e.end, e.title, _sections(bodies[e.start])) for e in entries
    ]
    result.extend(
        ChangelogEntry(monday, monday + timedelta(days=6), UNTITLED_WEEK, _sections(runs))
        for monday, runs in extra.items()
    )
    return sorted(result, key=lambda e: e.start, reverse=True)


def _read_changes() -> list[tuple[date, str]]:
    changes: list[tuple[date, str]] = []
    if not CHANGES_DIR.is_dir():
        return changes
    for path in sorted(CHANGES_DIR.glob('*.md')):
        day = change_file_date(path.name)
        if day is None:
            logger.warning('changelog: unrecognised change file name %r; skipped', path.name)
            continue
        changes.append((day, path.read_text(encoding='utf-8')))
    return changes


@lru_cache(maxsize=1)
def _load() -> tuple[ChangelogEntry, ...]:
    entries = parse_changelog(CHANGELOG_PATH.read_text(encoding='utf-8'))
    return tuple(apply_changes(entries, _read_changes()))


def all_entries() -> list[ChangelogEntry]:
    """Every shipped entry, newest first. Parsed once per process."""
    return list(_load())


def reload_entries() -> None:
    """Drop the parse cache. Used by tests and by a dev reload."""
    _load.cache_clear()
