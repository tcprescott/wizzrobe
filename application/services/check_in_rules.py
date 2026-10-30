"""Pure rules for event check-in: roster filters, counts, and identifier matching.

No database access and no I/O, so the desk and the service share one
definition of which filter chip a row belongs to and which identifier links a
registrant to an account. :class:`~application.services.CheckInService` does
the reads and writes around these.
"""

import difflib
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Protocol, Sequence, Set, Tuple

from models import CheckInEntrant, CheckInEntrantSource, CheckInLinkMethod, CheckInPass, CheckInTierLanyard, User


@dataclass
class SyncResult:
    added: int = 0
    updated: int = 0
    withdrawn: int = 0
    rejoined: int = 0
    auto_linked: int = 0
    total: int = 0
    badges: int = 0
    comped: int = 0

    def as_dict(self) -> Dict[str, int]:
        return {
            'added': self.added, 'updated': self.updated, 'withdrawn': self.withdrawn,
            'rejoined': self.rejoined, 'auto_linked': self.auto_linked, 'total': self.total,
            'badges': self.badges, 'comped': self.comped,
        }


@dataclass
class RosterCounts:
    """Headline numbers for the desk. Withdrawn rows count only as withdrawn."""

    total: int = 0
    checked_in: int = 0
    not_yet: int = 0
    unlinked: int = 0
    withdrawn: int = 0
    walk_ups: int = 0
    comped: int = 0


@dataclass
class CheckInOutcome:
    entrant: CheckInEntrant
    already: bool = False


@dataclass
class IdentityLookups:
    """Candidate accounts for auto-linking, indexed by each identifier."""

    by_matcherino_id: Dict[str, User] = field(default_factory=dict)
    by_discord_id: Dict[str, User] = field(default_factory=dict)
    by_twitch_id: Dict[str, User] = field(default_factory=dict)
    by_handle_id: Dict[str, User] = field(default_factory=dict)


_HANDLE_ID = re.compile(r'#\s*(\d+)\s*$')
_NAME_NOISE = re.compile(r'[^a-z0-9]+')


def handle_id(handle: Optional[str]) -> Optional[str]:
    """The numeric id at the end of a ``name#id`` Matcherino handle, if any."""
    if not handle:
        return None
    match = _HANDLE_ID.search(handle)
    return match.group(1) if match else None


def norm_name(name: Optional[str]) -> str:
    return _NAME_NOISE.sub('', (name or '').lower())


ROSTER_FILTERS = ('all', 'not_yet', 'checked_in', 'comped', 'unlinked', 'walk_up', 'withdrawn')
DESK_FILTERS = ('all', 'not_yet', 'checked_in', 'comped', 'walk_up')

VOLUNTEER_COMP = 'volunteer'


def comp_label(reason: str) -> str:
    """``'staff'`` → ``'Staff'``, ``'check_in_desk'`` → ``'Check In Desk'``."""
    return reason.replace('_', ' ').title()


# Order matters, highest first: someone who qualifies for several lanyards gets the first.
LANYARDS = ('staff', 'volunteer', CheckInTierLanyard.VIP.value, CheckInTierLanyard.BASE.value,
            CheckInTierLanyard.DAY_PASS.value)
LANYARD_LABELS = {
    'staff': 'Staff', 'volunteer': 'Volunteer', 'vip': 'VIP', 'base': 'Base', 'day_pass': 'Day Pass',
}


def is_active_badge(badge: CheckInPass) -> bool:
    return badge.refunded_at is None and badge.removed_at is None


def active_badges(badges: Iterable[CheckInPass]) -> List[CheckInPass]:
    """The badges someone still holds, most expensive first.

    ``badge.tier`` must be loaded. The first one is the tier the desk shows
    them under.
    """
    return sorted(
        (b for b in badges if is_active_badge(b)),
        key=lambda b: (-b.tier.amount_cents, b.tier.title, b.matcherino_purchase_id),
    )


def lanyard_for(
    entrant: CheckInEntrant, badges: Iterable[CheckInPass] = (), volunteer: bool = False,
) -> Optional[str]:
    """The one lanyard this person gets, or None (a walk-up with no badge).

    ``staff`` for anyone comped by a role (the event's comp roles), then
    ``volunteer`` for anyone with a published volunteer assignment
    (``volunteer``) or a volunteer comp, then the best lanyard among the
    badge types they still hold (``badges`` with tiers loaded).
    """
    reasons = entrant.comp_reasons or []
    if any(reason != VOLUNTEER_COMP for reason in reasons):
        return 'staff'
    if volunteer or VOLUNTEER_COMP in reasons:
        return 'volunteer'
    held = {badge.tier.lanyard.value for badge in badges if is_active_badge(badge)}
    return next((lanyard for lanyard in LANYARDS if lanyard in held), None)


def entrant_filters(entrant: CheckInEntrant) -> Set[str]:
    """The roster states a row belongs to.

    Someone whose badges were all refunded, and who hasn't checked in, counts
    only as withdrawn; once checked in they count as present whatever Matcherino
    says.
    """
    checked_in = entrant.checked_in_at is not None
    if entrant.withdrawn_at is not None and not checked_in:
        return {'withdrawn'}
    filters = {'all', 'checked_in' if checked_in else 'not_yet'}
    if entrant.user_id is None:
        filters.add('unlinked')
    if entrant.source == CheckInEntrantSource.WALK_UP:
        filters.add('walk_up')
    if entrant.comp_reasons:
        filters.add('comped')
    return filters


def summarize(entrants: Iterable[CheckInEntrant]) -> RosterCounts:
    counts = RosterCounts()
    for entrant in entrants:
        filters = entrant_filters(entrant)
        counts.total += 'all' in filters
        counts.checked_in += 'checked_in' in filters
        counts.not_yet += 'not_yet' in filters
        counts.unlinked += 'unlinked' in filters
        counts.withdrawn += 'withdrawn' in filters
        counts.walk_ups += 'walk_up' in filters
        counts.comped += 'comped' in filters
    return counts


def resolve_link(row: CheckInEntrant, lookups: IdentityLookups) -> Optional[Tuple[User, CheckInLinkMethod]]:
    candidates = (
        (lookups.by_matcherino_id.get(row.matcherino_user_id or ''), CheckInLinkMethod.MATCHERINO_ID),
        (lookups.by_discord_id.get(row.auth_id or '') if row.auth_provider == 'discord' else None,
         CheckInLinkMethod.DISCORD_ID),
        (lookups.by_twitch_id.get(row.auth_id or '') if row.auth_provider == 'twitch' else None,
         CheckInLinkMethod.TWITCH_ID),
        (lookups.by_handle_id.get(row.matcherino_user_id or ''), CheckInLinkMethod.MATCHERINO_HANDLE),
    )
    for user, method in candidates:
        if user is not None:
            return user, method
    return None


def entrant_details(entrant: CheckInEntrant) -> Dict[str, object]:
    """The audit and event payload for a roster row."""
    return {
        'event_id': entrant.event_id, 'entrant_id': entrant.id,
        'user_id': entrant.user_id, 'display_name': entrant.display_name,
        'matcherino_user_id': entrant.matcherino_user_id,
    }


def refuse_comp_row(entrant: CheckInEntrant) -> None:
    """A comp row belongs to the account it was given to; the desk can't relink it."""
    if entrant.source == CheckInEntrantSource.COMP:
        raise ValueError(
            "A comp is tied to the account it was given to. Change the event's comp rules instead."
        )


SUGGESTION_THRESHOLD = 0.6


class NamedPerson(Protocol):
    """A ``User``, or just its name columns (``PersonSummary``): all the scoring reads."""

    @property
    def id(self) -> int: ...
    @property
    def username(self) -> str: ...
    @property
    def display_name(self) -> Optional[str]: ...
    @property
    def twitch_username(self) -> Optional[str]: ...
    @property
    def matcherino_username(self) -> Optional[str]: ...


def suggestion_candidates(
    people: Sequence[NamedPerson], taken: set[int],
) -> List[Tuple[NamedPerson, frozenset[str]]]:
    candidates = []
    for user in people:
        if user.id in taken:
            continue
        names = frozenset({
            norm_name(user.username), norm_name(user.display_name), norm_name(user.twitch_username),
            norm_name((user.matcherino_username or '').split('#')[0]),
        } - {''})
        if names:
            candidates.append((user, names))
    return candidates


def suggested_users(
    target: str, candidates: Sequence[Tuple[NamedPerson, frozenset[str]]], limit: int,
) -> List[NamedPerson]:
    """The best-scoring candidates at or above the threshold, as ``difflib.get_close_matches`` prunes.

    The two cheap upper bounds skip the full ratio for names that can't reach
    the threshold, which roughly triples the speed of scoring a whole roster.
    """
    matcher = difflib.SequenceMatcher()
    matcher.set_seq1(target)
    scored: List[Tuple[float, NamedPerson]] = []
    for user, names in candidates:
        best = 0.0
        for name in names:
            matcher.set_seq2(name)
            if (matcher.real_quick_ratio() >= SUGGESTION_THRESHOLD
                    and matcher.quick_ratio() >= SUGGESTION_THRESHOLD):
                best = max(best, matcher.ratio())
        if best >= SUGGESTION_THRESHOLD:
            scored.append((best, user))
    scored.sort(key=lambda pair: (-pair[0], pair[1].username.lower()))
    return [user for _, user in scored[:limit]]


def count_suggestions(
    entrants: Sequence[CheckInEntrant], candidates: Sequence[Tuple[NamedPerson, frozenset[str]]], limit: int,
) -> Dict[int, int]:
    """How many of ``candidates`` each unlinked row would be offered. Comps can't be linked, so they're skipped."""
    return {
        e.id: len(suggested_users(target, candidates, limit))
        for e in entrants
        if e.user_id is None and e.source != CheckInEntrantSource.COMP and (target := norm_name(e.display_name))
    }
