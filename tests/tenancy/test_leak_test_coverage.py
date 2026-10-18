"""Every tenant-scoped model must appear in a tenant-isolation (leak) test.

The multitenancy contract (CLAUDE.md) requires a leak test when adding a
tenant-scoped model; this ratchet makes that mechanical. A model counts as
covered when its class name appears (word-bounded) in any ``tests/**/*isolation*``
file. Models that predate the rule sit in BACKLOG with a reason — and the
companion test guarantees the backlog only ever shrinks: a model that gains a
leak test (or is deleted) must leave it.
"""

import re
from pathlib import Path

from tests.conftest import _scoped_models

# Search the whole test tree, not just this package: a leak test is allowed to
# live next to the feature it covers as long as its filename says "isolation".
TESTS_DIR = Path(__file__).resolve().parents[1]

# Model name -> why it has no leak test yet. Shrink this list; never grow it.
_DEBT = 'pre-ratchet debt (2026-07): needs a two-tenant read-isolation test'
BACKLOG: dict[str, str] = {
    # Leak coverage exists but lives outside the tests/*isolation* glob:
    'TenantFeatureFlag': 'covered by test_feature_flags.py::test_flags_do_not_leak_across_tenants',
    # Models that predate the leak-test rule:
    'AsyncQualifierReviewNote': _DEBT,
    'ChallongeApiUsage': _DEBT,
    'ChallongeConnection': _DEBT,
    'ChallongeMatch': _DEBT,
    'ChallongeParticipant': _DEBT,
    'Commentator': _DEBT,
    'DiscordRoleMapping': _DEBT,
    'EquipmentLoan': _DEBT,
    'GeneratedSeeds': _DEBT,
    'MatchAcknowledgment': _DEBT,
    'TournamentNotificationPreference': _DEBT,
    'Tracker': _DEBT,
    'TriforceText': _DEBT,
    'WebhookDelivery': _DEBT,
}


def _isolation_text() -> str:
    return "\n".join(
        p.read_text() for p in sorted(TESTS_DIR.rglob("*isolation*.py"))
        if p.name != Path(__file__).name
    )


def _covered(name: str, text: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text) is not None


def test_every_tenant_fk_model_has_a_leak_test():
    text = _isolation_text()
    missing = [
        m.__name__
        for m in _scoped_models()
        if m.__name__ not in BACKLOG and not _covered(m.__name__, text)
    ]
    assert not missing, (
        f"tenant-FK models with no tenant-isolation test: {missing}. Add a leak "
        f"test (two tenants, write under each via tenant_scope, assert no "
        f"cross-tenant reads — see tests/tenancy/test_tenant_isolation.py), or, only "
        f"for pre-existing debt, record a reason in BACKLOG above."
    )


def test_backlog_is_current():
    text = _isolation_text()
    names = {m.__name__ for m in _scoped_models()}
    stale = [
        n for n in BACKLOG
        if n not in names or _covered(n, text)
    ]
    assert not stale, (
        f"BACKLOG entries that are no longer needed (model covered or deleted): "
        f"{stale}. Remove them so the backlog only shrinks."
    )
