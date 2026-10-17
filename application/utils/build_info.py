"""Which build of Wizzrobe is running.

The image bakes ``GIT_SHA`` (the commit) and ``GIT_COMMIT_DATE`` (its ISO commit
time) in at build time; ``APP_VERSION`` overrides the version outright, which a
tag build uses. Without a tag the version is the commit's UTC date as
``YYYY.MM.DD``, so two people comparing builds can tell which is newer without
looking up a hash. A local checkout has none of these and reports ``dev``.
"""

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional

__all__ = ['BuildInfo', 'build_info']

_REPOSITORY_URL = 'https://github.com/tcprescott/wizzrobe'


@dataclass(frozen=True)
class BuildInfo:
    version: str
    commit: Optional[str]
    commit_date: Optional[datetime]

    @property
    def commit_short(self) -> Optional[str]:
        return self.commit[:7] if self.commit else None

    @property
    def source_url(self) -> str:
        return f'{_REPOSITORY_URL}/tree/{self.commit}' if self.commit else _REPOSITORY_URL


def _parse_date(raw: str) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@lru_cache(maxsize=1)
def build_info() -> BuildInfo:
    commit = (os.environ.get('GIT_SHA') or '').strip() or None
    commit_date = _parse_date((os.environ.get('GIT_COMMIT_DATE') or '').strip())
    version = (os.environ.get('APP_VERSION') or '').strip().removeprefix('v')
    if not version:
        version = commit_date.strftime('%Y.%m.%d') if commit_date else 'dev'
    return BuildInfo(version=version, commit=commit, commit_date=commit_date)
