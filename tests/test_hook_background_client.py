"""Guardrail: ``check_slot_context.py`` catches a background task fetching its own client.

The hook's second check, added after a UX audit found the shape live. A
coroutine handed to ``background_tasks.create(...)`` runs with an empty slot
stack, so ``context.client`` **raises** rather than returning ``None`` — a
handler that opens with ``client = context.client`` dies on its first statement,
before doing anything, with nothing on screen to say so. My Crew's ``acknowledge``
and ``withdraw`` were both written that way, so the volunteer's *Confirm I can
cover this* and *Withdraw* had never worked from the page built to give them one.

The hook's first check (an unguarded ``ui.*`` call) does not see it: the
offending line looks like a perfectly ordinary capture, and it is often followed
by a correct ``with client:`` block that satisfies check 1 completely.

Assertions run the hook's real ``check()`` against source fragments, so they
survive a refactor of its internals.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / ".claude" / "scripts" / "check_slot_context.py"


@pytest.fixture(scope='module')
def hook():
    """The hook module, loaded by path (``.claude/scripts`` is not a package)."""
    sys.path.insert(0, str(HOOK.parent))
    try:
        spec = importlib.util.spec_from_file_location('check_slot_context', HOOK)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(HOOK.parent))


# The `with client:` makes check 1 pass, which is why check 2 must catch this separately.
BUG = '''
from nicegui import background_tasks, context, ui

async def acknowledge(row):
    client = context.client
    with client:
        ui.notify('done')

def build(row):
    ui.button('go', on_click=lambda: background_tasks.create(acknowledge(row)))
'''

FIXED = '''
from nicegui import background_tasks, context, ui

async def acknowledge(row, client):
    with client:
        ui.notify('done')

def build(row):
    ui.button('go', on_click=lambda: background_tasks.create(acknowledge(row, context.client)))
'''

# context.client at the call site is the fix: a hook flagging it would make the correct pattern unwritable.
CALL_SITE_ONLY = '''
from nicegui import background_tasks, context, ui

async def handle(row, client):
    with client:
        ui.notify('ok')

def build(row):
    ui.button('go', on_click=lambda: background_tasks.create(handle(row, context.client)))
'''

NOT_A_BACKGROUND_TASK = '''
from nicegui import context, ui

async def handle(row):
    client = context.client
    ui.notify('ok')

def build(row):
    ui.button('go', on_click=lambda _e: handle(row))
'''


def _client_violations(hook, source: str) -> list[str]:
    return [v for v in hook.check(source, 'pages/x.py') if 'reads context.client' in v]


class TestTheShapeItCatches:
    def test_flags_reading_context_client_inside_the_task(self, hook):
        violations = _client_violations(hook, BUG)
        assert len(violations) == 1
        assert 'line 5' in violations[0]

    def test_a_with_client_block_does_not_excuse_it(self, hook):
        """There is no guarded position for this read — the value isn't there.

        BUG has a correct ``with client:`` and still must be flagged, which is
        the whole reason this is a separate check from the ui.* one.
        """
        assert 'with client' in BUG
        assert _client_violations(hook, BUG)

    def test_the_message_names_the_fix(self, hook):
        message = _client_violations(hook, BUG)[0]
        assert 'context.client' in message
        assert 'pass it in' in message
        assert 'silently' in message


class TestWhatItLeavesAlone:
    def test_the_fixed_shape_is_clean(self, hook):
        assert hook.check(FIXED, 'pages/x.py') == []

    def test_context_client_at_the_call_site_is_the_fix_not_the_bug(self, hook):
        assert hook.check(CALL_SITE_ONLY, 'pages/x.py') == []

    def test_a_handler_that_is_not_a_background_task_is_ignored(self, hook):
        assert _client_violations(hook, NOT_A_BACKGROUND_TASK) == []


class TestAgainstTheRepo:
    def test_no_presentation_file_currently_trips_it(self, hook):
        """The repo is clean today; this is what keeps it that way in the suite.

        ``scripts/guardrails.py`` runs the same hook per changed file on every
        commit — this is the whole-tree pass, so a file nobody touched cannot
        drift back.
        """
        offenders = []
        for root in (REPO / 'pages', REPO / 'theme'):
            for path in root.rglob('*.py'):
                if '__pycache__' in str(path):
                    continue
                if _client_violations(hook, path.read_text()):
                    offenders.append(str(path.relative_to(REPO)))
        assert not offenders, offenders
