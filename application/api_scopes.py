"""The scope vocabulary third-party apps ask for and the REST API enforces.

A personal access token is unscoped: it acts with everything its owner may do.
An app token (``ApiToken.origin == 'app'``) carries the scopes its user approved
on the consent page, space-separated in ``ApiToken.scope``, and the REST API
refuses any route outside them (``api/scopes.py`` maps every route to one).

Scopes only ever narrow. Holding ``staff:write`` gives a player nothing, because
each service still checks the user's roles in the community on every call.

Pure metadata, import-safe from every layer: the authorization server (consent,
registration, issuer metadata) and the REST layer read the same names.
"""

from enum import Enum
from typing import Dict, FrozenSet, Iterable, Optional


class ApiScope(str, Enum):
    PROFILE = 'profile'
    PROFILE_WRITE = 'profile:write'
    SCHEDULE_READ = 'schedule:read'
    PLAY = 'play'
    CREW = 'crew'
    VOLUNTEER = 'volunteer'
    STAFF_READ = 'staff:read'
    STAFF_WRITE = 'staff:write'


ALL_SCOPES: FrozenSet[ApiScope] = frozenset(ApiScope)

WRITE_SCOPES: FrozenSet[ApiScope] = frozenset({
    ApiScope.PROFILE_WRITE, ApiScope.PLAY, ApiScope.CREW, ApiScope.VOLUNTEER, ApiScope.STAFF_WRITE,
})

READ_SCOPES: FrozenSet[ApiScope] = ALL_SCOPES - WRITE_SCOPES

STAFF_SCOPES: FrozenSet[ApiScope] = frozenset({ApiScope.STAFF_READ, ApiScope.STAFF_WRITE})

CONSENT_WORDING: Dict[ApiScope, str] = {
    ApiScope.PROFILE: 'See your Wizzrobe profile and availability',
    ApiScope.PROFILE_WRITE: 'Update your profile, availability and notification settings',
    ApiScope.SCHEDULE_READ: 'See tournaments, matches, brackets and the schedule',
    ApiScope.PLAY: 'Act as you as a player',
    ApiScope.CREW: 'Sign you up or withdraw you as crew',
    ApiScope.VOLUNTEER: 'See and manage your volunteer shifts',
    ApiScope.STAFF_READ: 'See staff-only information in this community',
    ApiScope.STAFF_WRITE: 'Make staff changes in this community',
}


def parse_scope(value: Optional[str]) -> FrozenSet[ApiScope]:
    """The scopes in a space-separated string. Unknown names are dropped, so a
    grant stored before a scope was retired loses it rather than failing."""
    known = {scope.value: scope for scope in ApiScope}
    return frozenset(known[name] for name in (value or '').split() if name in known)


def format_scope(scopes: Iterable[ApiScope]) -> str:
    """The space-separated form stored on a grant, in declaration order."""
    wanted = set(scopes)
    return ' '.join(scope.value for scope in ApiScope if scope in wanted)


__all__ = [
    'ALL_SCOPES',
    'CONSENT_WORDING',
    'READ_SCOPES',
    'STAFF_SCOPES',
    'WRITE_SCOPES',
    'ApiScope',
    'format_scope',
    'parse_scope',
]
