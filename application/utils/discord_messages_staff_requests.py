"""Discord Message Templates: a staff request someone has already handled

Every staff member is DM'd about a community event to review, an ADA request,
a join request, a reschedule request or a crew signup. Once one of them decides it, each copy
of that DM is edited to one of these, so the rest can see it's done without
opening the request. The reader may be the person who decided it, so each
builder takes ``you`` and says "You" instead of their name.

Each returns ``(title, description)`` for the edited card. Names and titles are
typed by members, so they pass through :func:`literal` like the originals.
"""

from datetime import datetime
from typing import Optional, Tuple

from application.utils.discord_messages_community_events import literal

Card = Tuple[str, str]


def _who(you: bool, name: str) -> str:
    return 'You' if you else literal(name)


def _subject(you: bool, name: str) -> str:
    return 'You' if you else f'**{literal(name)}**'


def _at(when: datetime) -> str:
    return f'<t:{int(when.timestamp())}:f>'


def event_approved(you: bool, staff_name: str, title: str, when: datetime) -> Card:
    return (
        f'✅ Approved by {_who(you, staff_name)}',
        f"{_subject(you, staff_name)} approved **{literal(title)}** {_at(when)}. "
        "It's listed now, so there's nothing left for you to do.",
    )


def event_rejected(you: bool, staff_name: str, title: str, when: datetime,
                   reason: str, was_listed: bool) -> Card:
    if was_listed:
        heading = f'🚫 Taken down by {_who(you, staff_name)}'
        what = f'took **{literal(title)}** off the listing'
    else:
        heading = f'🚫 Rejected by {_who(you, staff_name)}'
        what = f"didn't approve **{literal(title)}**"
    return (
        heading,
        f'{_subject(you, staff_name)} {what} {_at(when)}.\nReason given to the host: {reason}',
    )


def event_cancelled(you: bool, name: str, title: str, when: datetime) -> Card:
    return (
        f'↩️ Cancelled by {_who(you, name)}',
        f"{_subject(you, name)} cancelled **{literal(title)}** {_at(when)}, "
        "so there's nothing to review.",
    )


def ada_status(you: bool, staff_name: str, member_name: str, status_label: str,
               when: datetime) -> Card:
    return (
        f'✅ {status_label} by {_who(you, staff_name)}',
        f"{_subject(you, staff_name)} marked **{literal(member_name)}**'s ADA request "
        f'{status_label} {_at(when)}. Open it if you want to see where it stands.',
    )


def ada_reviewed(you: bool, staff_name: str, member_name: str, when: datetime) -> Card:
    return (
        f'✅ Change reviewed by {_who(you, staff_name)}',
        f"{_subject(you, staff_name)} reviewed the change to **{literal(member_name)}**'s "
        f'ADA request {_at(when)}. It\'s still Arranged.',
    )


def ada_withdrawn(member_name: str, when: datetime) -> Card:
    return (
        '↩️ Request withdrawn',
        f"**{literal(member_name)}**'s ADA request was withdrawn {_at(when)}, "
        "so there's nothing left to do.",
    )


def join_approved(you: bool, staff_name: Optional[str], requester_name: str,
                  community_name: str, when: datetime) -> Card:
    if staff_name is None:
        return (
            '✅ Already a member',
            f'**{literal(requester_name)}** joined **{community_name}** another way {_at(when)}, '
            "so there's nothing left to do.",
        )
    return (
        f'✅ Approved by {_who(you, staff_name)}',
        f'{_subject(you, staff_name)} let **{literal(requester_name)}** into '
        f"**{community_name}** {_at(when)}. There's nothing left to do.",
    )


def join_declined(you: bool, staff_name: str, requester_name: str, when: datetime) -> Card:
    return (
        f'🚫 Declined by {_who(you, staff_name)}',
        f"{_subject(you, staff_name)} declined **{literal(requester_name)}**'s request "
        f'to join {_at(when)}.',
    )


def _ask(requester_name: str, tournament_name: str, kind_cancel: bool) -> str:
    verb = 'call off' if kind_cancel else 'move'
    return (
        f"**{literal(requester_name)}**'s request to {verb} their "
        f'**{literal(tournament_name)}** match'
    )


def reschedule_approved(you: bool, staff_name: str, requester_name: str,
                        tournament_name: str, kind_cancel: bool, when: datetime) -> Card:
    outcome = 'The match is called off.' if kind_cancel else 'The match has moved.'
    return (
        f'✅ Approved by {_who(you, staff_name)}',
        f'{_subject(you, staff_name)} approved {_ask(requester_name, tournament_name, kind_cancel)} '
        f'{_at(when)}. {outcome}',
    )


def reschedule_declined(you: bool, staff_name: str, requester_name: str,
                        tournament_name: str, kind_cancel: bool, when: datetime) -> Card:
    return (
        f'🚫 Declined by {_who(you, staff_name)}',
        f'{_subject(you, staff_name)} declined {_ask(requester_name, tournament_name, kind_cancel)} '
        f'{_at(when)}. The match stays as it was.',
    )


def reschedule_withdrawn(requester_name: str, tournament_name: str, kind_cancel: bool,
                         when: datetime) -> Card:
    return (
        '↩️ Request withdrawn',
        f'{_ask(requester_name, tournament_name, kind_cancel)} was withdrawn {_at(when)}, '
        "so there's nothing to decide.",
    )


def reschedule_superseded(you: bool, staff_name: str, requester_name: str,
                          tournament_name: str, kind_cancel: bool, when: datetime) -> Card:
    return (
        '✅ Settled by another request',
        f'{_subject(you, staff_name)} approved a different request for this match {_at(when)}, '
        f"which closed {_ask(requester_name, tournament_name, kind_cancel)}. "
        "There's nothing to decide.",
    )


def crew_signup_pending(volunteer_name: str, crew_type: str) -> Card:
    """The original ask, restored when a signup is waiting again."""
    return (
        f'🎙️ New {crew_type} signup',
        f'**{literal(volunteer_name)}** wants to cover this as {crew_type}. It needs your approval.',
    )


def crew_signup_withdrawn(volunteer_name: str, crew_type: str, when: datetime) -> Card:
    return (
        f'↩️ Withdrawn by {literal(volunteer_name)}',
        f'**{literal(volunteer_name)}** withdrew their {crew_type} signup {_at(when)}, '
        "so there's nothing to approve.",
    )


def crew_signup_approved(you: bool, staff_name: str, volunteer_name: str, crew_type: str,
                         when: datetime) -> Card:
    return (
        f'✅ Approved by {_who(you, staff_name)}',
        f'{_subject(you, staff_name)} approved **{literal(volunteer_name)}** as {crew_type} '
        f'{_at(when)}. Nothing left for you to do.',
    )


def crew_signup_removed(you: bool, staff_name: str, volunteer_name: str, crew_type: str,
                        when: datetime) -> Card:
    return (
        f'🚫 Removed by {_who(you, staff_name)}',
        f"{_subject(you, staff_name)} removed **{literal(volunteer_name)}**'s {crew_type} signup "
        f"{_at(when)}, so there's nothing to approve.",
    )
