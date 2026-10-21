"""Discord Message Templates: community events

The DMs :mod:`application.services.community_event_service` and its reminder
worker send. Each one is also the web-push text, so it reads on its own; the
embed beside it carries the time as native ``<t:…>`` markup so every recipient
sees their own clock.

Titles, host names and locations are typed by members, so each passes through
:func:`literal` first: Discord renders ``[label](url)`` as a masked link inside
an embed, which would let a member put a disguised link in front of every
reviewer.
"""

import re

_LINK_BRACKETS = re.compile(r'([\[\]])')


def literal(text: str | None) -> str:
    """``text`` with its square brackets escaped, so it cannot form a masked link."""
    return _LINK_BRACKETS.sub(r'\\\1', text or '')


def submitted_to_staff_dm(community_name: str, host_name: str, title: str) -> str:
    """DM to staff when a member submits an event, or resubmits a rejected one."""
    return (
        f'**{literal(host_name)}** submitted **{literal(title)}** to the community events listing for '
        f'**{community_name}**. Open it to approve or reject it.'
    )


def approved_dm(community_name: str, title: str, rsvp_enabled: bool = False) -> str:
    """DM to the host when staff approve their event."""
    how = (
        "Members can RSVP to it, which also gets them a reminder before it starts."
        if rsvp_enabled else
        'Members can subscribe to it for a reminder before it starts.'
    )
    return (
        f'**{literal(title)}** is approved and now listed in **{community_name}**\'s community '
        f'events. {how}'
    )


def handed_over_dm(community_name: str, staff_name: str, title: str, *, new_event: bool,
                   listed: bool) -> str:
    """DM to a member staff made the host of an event, new or existing."""
    what = 'posted' if new_event else 'handed you'
    status = (
        "It's listed in the community events." if listed
        else "It isn't listed yet."
    )
    return (
        f'**{literal(staff_name)}** {what} **{literal(title)}** in **{community_name}** with you as '
        f'the host. {status} You can edit or cancel it from the event page.'
    )


def rejected_dm(community_name: str, title: str, reason: str, was_listed: bool) -> str:
    """DM to the host when staff reject their event or take a listed one down."""
    what = 'was taken off' if was_listed else "wasn't approved for"
    return (
        f'**{literal(title)}** {what} **{community_name}**\'s community events listing.\n'
        f'Reason from staff: {reason}\n'
        'You can edit it and submit it again.'
    )


def reminder_dm(title: str, starts_display: str, location: str | None) -> str:
    """DM to a subscriber shortly before an event starts."""
    where = f' at {literal(location)}' if location else ''
    return f'**{literal(title)}** starts {starts_display}{where}.'


def time_changed_dm(title: str, starts_display: str) -> str:
    """DM to subscribers when the host or staff move an approved event."""
    return f'**{literal(title)}** has moved. It now starts {starts_display}.'


def cancelled_dm(title: str) -> str:
    """DM to subscribers when an approved event is called off or taken down."""
    return f'**{literal(title)}** has been cancelled, so you won\'t get a reminder for it.'
