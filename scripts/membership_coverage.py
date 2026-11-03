"""Which ``(user, tenant)`` pairs show participation but carry no membership.

Shared by ``audit_membership_coverage.py`` (report) and
``backfill_memberships.py`` (grant), so the two can never disagree about what
counts as belonging to a community.

Import-pure on purpose — no ``load_dotenv()`` at module scope; see
``tests/test_script_import_purity.py``.
"""

from typing import Dict, List, Set, Tuple

from models import (
    AuditLog,
    Commentator,
    MatchPlayers,
    Role,
    TenantMembership,
    TournamentPlayers,
    Tracker,
    UserRole,
    VolunteerAssignment,
)

Pair = Tuple[int, int]


async def _pairs(model, user_field: str = 'user_id') -> Set[Pair]:
    """Every ``(user, tenant)`` this model records. Deliberately cross-tenant."""
    rows = await model.all().values(user_field, 'tenant_id')
    return {
        (r[user_field], r['tenant_id'])
        for r in rows
        if r[user_field] is not None and r['tenant_id'] is not None
    }


async def _role_pairs() -> Set[Pair]:
    """Role holders, minus SUPER_ADMIN — its row is deliberately tenant-less.

    Cross-tenant by nature: the question is which tenants each user acts in.
    """
    rows = await UserRole.exclude(role=Role.SUPER_ADMIN).values('user_id', 'tenant_id')
    return {
        (r['user_id'], r['tenant_id'])
        for r in rows
        if r['user_id'] is not None and r['tenant_id'] is not None
    }


# Ordered strongest first.
GRANTING_SIGNALS = {
    'enrolled in a tournament': lambda: _pairs(TournamentPlayers),
    'played in a match': lambda: _pairs(MatchPlayers),
    'signed up as a commentator': lambda: _pairs(Commentator),
    'signed up as a tracker': lambda: _pairs(Tracker),
    'took a volunteer shift': lambda: _pairs(VolunteerAssignment),
    'holds a role': _role_pairs,
}

# Reported, never granted on: an audit row can mean someone acted on you (a revoke), not that you belong.
REPORT_ONLY_SIGNALS = {
    'appears in the audit log': lambda: _pairs(AuditLog),
}


async def existing_memberships() -> Set[Pair]:
    """Deliberately cross-tenant: the whole point is to compare every tenant."""
    rows = await TenantMembership.all().values('user_id', 'tenant_id')
    return {(r['user_id'], r['tenant_id']) for r in rows}


async def coverage_gaps(include_report_only: bool = False) -> Dict[str, Set[Pair]]:
    """``{signal label: uncovered (user, tenant) pairs}``.

    A pair is uncovered when the signal says the user participates in that
    community and no ``TenantMembership`` row says they belong to it.
    """
    members = await existing_memberships()
    signals = dict(GRANTING_SIGNALS)
    if include_report_only:
        signals.update(REPORT_ONLY_SIGNALS)
    out: Dict[str, Set[Pair]] = {}
    for label, loader in signals.items():
        out[label] = await loader() - members
    return out


async def grantable_gaps() -> List[Pair]:
    """Every pair the backfill should write, sorted for a stable report."""
    gaps = await coverage_gaps(include_report_only=False)
    return sorted(set().union(*gaps.values()) if gaps else set())
