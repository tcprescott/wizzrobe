"""An in-process stand-in for the DK64 Randomizer API (api.dk64rando.com).

DK64R is the one backend whose roll is not a request but a **task queue**:
submit, then poll ``task-status`` until it flips to ``finished``, which upstream
takes two to three minutes of real wall clock. That duration is the whole
user-experience problem — the Generate button holds a spinner for the length of
it — and it cannot be exercised by the plain ``MOCK_SEEDGEN`` short-circuit,
which returns a permalink instantly and so never puts the presentation layer in
the waiting state at all.

So this mock stands in one layer lower, at the HTTP session rather than at
:meth:`SeedGenerationService.generate_seed`. ``_generate_dk64r`` runs unchanged
against it: it really converts settings, really submits, and really polls on the
five-second cadence while the fake task walks ``queued`` → ``started`` →
``finished``. The poll loop and its failure branches are live code under the
mock, and the UI spends a configurable number of seconds in exactly the state a
real roll puts it in — without adding a seed to a randomizer whose global
counter is public.

Gated by ``MOCK_SEEDGEN`` (and therefore refused in production by
:func:`is_mock_seedgen`); no DK64 API key is needed while it is on. Three knobs:

``MOCK_DK64_SECONDS``
    Wall clock from submit to ``finished``. Default 20 — long enough to watch
    the waiting UI, short enough not to stall the browser-validation loop.
``MOCK_DK64_OUTCOME``
    ``finished`` (default), ``failed`` (a ``failed`` status inside a 200),
    ``http_error`` (a 500 from ``task-status``), or ``stuck`` (never leaves
    ``started``, to reach the generation-timeout branch — pair it with a
    patched ``DK64R_GENERATION_TIMEOUT``, which is ten minutes by default).
``MOCK_DK64_BROKEN_STAGE``
    ``convert`` or ``submit`` to fail that call instead, covering the two error
    paths that happen before there is any task to poll.
"""

import os
import random
import time
from typing import Any, Dict, List, Optional

from application.utils.environment import env_flag, is_production

MOCK_DK64_DEFAULT_SECONDS = 20.0

_QUEUED_FRACTION = 0.25

_OUTCOMES = frozenset({'finished', 'failed', 'http_error', 'stuck'})


def is_mock_dk64() -> bool:
    """Return True when the DK64 queue should be simulated in-process.

    Rides on ``MOCK_SEEDGEN`` rather than adding a switch of its own, so there
    is one answer to "is seed rolling faked here?" and one production refusal.
    """
    from application.utils.mocks.mock_seedgen import is_mock_seedgen

    return is_mock_seedgen()


def mock_dk64_seconds() -> float:
    """How long a simulated roll takes, end to end."""
    raw = os.environ.get('MOCK_DK64_SECONDS')
    if raw is None:
        return MOCK_DK64_DEFAULT_SECONDS
    try:
        value = float(raw.strip())
    except ValueError:
        return MOCK_DK64_DEFAULT_SECONDS
    return max(0.0, value)


def mock_dk64_outcome() -> str:
    """Which ending the simulated task reaches."""
    value = (os.environ.get('MOCK_DK64_OUTCOME') or 'finished').strip().lower()
    return value if value in _OUTCOMES else 'finished'


def mock_dk64_broken_stage() -> Optional[str]:
    """Which pre-poll call, if any, is made to fail."""
    value = (os.environ.get('MOCK_DK64_BROKEN_STAGE') or '').strip().lower()
    return value if value in {'convert', 'submit'} else None


def mock_dk64_presets(branch: str) -> List[Dict[str, str]]:
    """A believable ``get_presets`` payload, shaped like the real one.

    Same four keys the live API returns, so a preset picker fed from upstream
    renders offline. The settings strings are placeholders: nothing in the mock
    path decodes them, and a real one would go stale against the live presets.

    Deliberately *long*. The live site publishes dozens of presets, and a picker
    that only ever saw four would hide every problem a real catalogue creates —
    a list that scrolls off the dialog, a name you cannot find without reading
    all of them.
    """
    names = [
        ('Season 5 Race Settings', 'Medium length settings with a uniquely shuffled seasonal world.'),
        ('Season 4 Race Settings', 'The previous season, kept for rematches and vods.'),
        ('Season 3 Race Settings', 'Archived season settings.'),
        ('Beginner Settings', 'A gentler ruleset for players new to the randomizer.'),
        ('Beginner Plus', 'Beginner with item rando switched on.'),
        ('Balanced LZR', 'Level order rando with balanced progression requirements.'),
        ('Hard LZR', 'Level order rando without the training wheels.'),
        ('Hell Mode', 'Long, punishing, and not remotely balanced.'),
        ('Item Rando Standard', 'Every shop and collectible shuffled into one pool.'),
        ('Item Rando Lite', 'Item rando limited to major items.'),
        ('Move Rando', 'Moves shuffled across the vendors.'),
        ('Kong Rando', 'Kongs freed in a shuffled order.'),
        ('Barrel Rando', 'Bonus barrel minigames shuffled.'),
        ('Boss Rando', 'Bosses and their key rewards shuffled.'),
        ('Key Rando', 'Boss keys placed anywhere in the pool.'),
        ('Coin Rando', 'Golden bananas replaced by coin checks.'),
        ('Blocker Rando', 'Randomized B. Locker requirements.'),
        ('Fast Start', 'Training barrels and tutorial skipped.'),
        ('Fast Start Plus', 'Fast start with the first three kongs unlocked.'),
        ('No Logic Chaos', 'Placement ignores logic entirely. Not for races.'),
        ('Glitchless Standard', 'The default glitchless ruleset.'),
        ('Glitchless Long', 'Glitchless with a full 201-check pool.'),
        ('Glitch Any Percent', 'Anything goes, including out of bounds.'),
        ('Bingo Prep', 'Settings the bingo board is generated against.'),
        ('Co-op Duo', 'Tuned for two players sharing a file.'),
        ('Weekly Community Race', 'The rotating weekly community ruleset.'),
        ('Monthly Marathon', 'Very long settings for the monthly marathon.'),
        ('Sprint 30', 'A thirty-minute sprint ruleset.'),
        ('Sprint 60', 'An hour-long sprint ruleset.'),
        ('Blind Draft', 'Settings drawn blind for draft brackets.'),
        ('Tournament Qualifier', 'The async qualifier ruleset.'),
        ('Tournament Finals', 'The finals ruleset.'),
        ('Practice Isles', 'DK Isles only, for route practice.'),
        ('Practice Bosses', 'Boss rush for practice.'),
    ]
    if branch == 'dev':
        names.append(('Season 6 Candidate', 'Under evaluation for the next season.'))
        names.append(('Experimental Shuffle', 'Dev-only placement experiment.'))
    return [
        {
            'branch': branch,
            'name': name,
            'description': description,
            'settings_string': f'MOCK{branch.upper()}{index:04d}',
        }
        for index, (name, description) in enumerate(names)
    ]


class _MockResponse:
    """The slice of ``aiohttp.ClientResponse`` the DK64 generator touches."""

    def __init__(self, status: int, payload: Any) -> None:
        self.status = status
        self._payload = payload

    async def __aenter__(self) -> '_MockResponse':
        return self

    async def __aexit__(self, *exc_info) -> bool:
        return False

    async def json(self) -> Any:
        return self._payload

    async def text(self) -> str:
        return str(self._payload)


# Module level on purpose: a resumed poll arrives on another session, so per-instance state would 404 it.
_TASKS: Dict[str, float] = {}


def reset_mock_dk64() -> None:
    """Forget every simulated task. For tests that assert on a clean queue."""
    _TASKS.clear()


class MockDK64Session:
    """A fake ``aiohttp.ClientSession`` serving the DK64 task-queue endpoints."""

    def __init__(self) -> None:
        self._duration = mock_dk64_seconds()
        self._outcome = mock_dk64_outcome()
        self._broken = mock_dk64_broken_stage()

    async def __aenter__(self) -> 'MockDK64Session':
        return self

    async def __aexit__(self, *exc_info) -> bool:
        return False

    def post(self, url: str, *, json: Optional[dict] = None, params: Optional[dict] = None):
        if url.endswith('/convert_settings'):
            return self._convert_settings(json or {})
        if url.endswith('/submit-task'):
            return self._submit_task(json or {})
        return _MockResponse(404, {'error': f'No mock route for POST {url}'})

    def get(self, url: str, *, params: Optional[dict] = None):
        if '/task-status/' in url:
            return self._task_status(url.rsplit('/', 1)[-1])
        if url.endswith('/get_presets'):
            branch = (params or {}).get('branch', 'stable')
            return _MockResponse(200, mock_dk64_presets(branch))
        return _MockResponse(404, {'error': f'No mock route for GET {url}'})

    def _convert_settings(self, body: dict) -> _MockResponse:
        if self._broken == 'convert':
            return _MockResponse(400, {'error': 'Invalid settings string'})
        settings_string = body.get('settings')
        if not settings_string:
            return _MockResponse(400, {'error': 'Missing settings'})
        return _MockResponse(200, {
            'mock_settings_string': settings_string,
            'generate_spoilerlog': True,
            'has_password': False,
            'random_starting_move_set': 0,
            'shuffle_items': True,
        })

    def _submit_task(self, body: dict) -> _MockResponse:
        if self._broken == 'submit':
            return _MockResponse(500, {'error': 'Queue is unavailable'})
        if not body.get('settings_data'):
            return _MockResponse(400, {'error': 'Missing settings_data'})
        task_id = f"mock-{random.getrandbits(48):012x}"
        _TASKS[task_id] = time.monotonic()
        return _MockResponse(200, {'task_id': task_id, 'status': 'queued', 'priority': 'High'})

    def _task_status(self, task_id: str) -> _MockResponse:
        started_at = _TASKS.get(task_id)
        if started_at is None:
            return _MockResponse(404, {'error': 'Unknown task'})

        elapsed = time.monotonic() - started_at
        if elapsed < self._duration * _QUEUED_FRACTION:
            return _MockResponse(200, {'status': 'queued'})
        if elapsed < self._duration or self._outcome == 'stuck':
            return _MockResponse(200, {'status': 'started'})

        if self._outcome == 'failed':
            return _MockResponse(200, {'status': 'failed'})
        if self._outcome == 'http_error':
            return _MockResponse(500, {'error': 'Generation crashed'})

        return _MockResponse(200, {
            'status': 'finished',
            'result': {
                # The real ~9 MB `patch` field is omitted on purpose, so the mock doesn't normalise keeping it.
                'seed_number': random.randint(100000, 999999),
                'hash': [random.randint(0, 9) for _ in range(5)],
            },
        })


def guard_not_production() -> None:
    """Refuse to simulate DK64 rolls in production.

    ``is_mock_dk64`` already inherits :func:`is_mock_seedgen`'s refusal; this is
    the direct check for callers that construct a session without asking first.
    """
    if env_flag('MOCK_SEEDGEN') and is_production():
        raise RuntimeError(
            'MOCK_SEEDGEN must not be enabled in production: DK64 rolls would be '
            'simulated instead of generating a real seed.'
        )
