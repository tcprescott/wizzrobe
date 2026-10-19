"""Pure builders for in-app route paths.

Section slugs are path segments (``/admin/match-schedule``, ``/home/player``); anything
a surface needs to focus on — or a dialog it should open on arrival — travels as a
query param. Root-relative on purpose: NiceGUI derives the client prefix from the
page's ``root_path``, so a bare ``/admin/…`` resolves inside ``/t/<slug>``.

Lives in ``utils`` rather than ``pages/`` because the notification layer needs the
same paths: a DM's "Pick a time" button and the page's own link must address the
identical route, and a service may not import a presentation module.
``pages/admin_tabs/links.py`` re-exports the admin half for its existing callers.
"""

from datetime import date, datetime
from typing import Optional
from urllib.parse import urlencode

# Admin tab slugs (theme.base.tab_slug of each tab's label).
SCHEDULE = 'match-schedule'
REPORTS = 'reports'
VOL_SCHEDULE = 'volunteer-shifts'
SHIFT_BOARD = 'shift-board'
# The Challonge sign-in lives on Settings → Integrations.
INTEGRATIONS = 'settings/integrations'
USERS = 'members'
QUALIFIERS = 'qualifiers'
CHECK_IN = 'check-in-roster'
COMMUNITY_EVENTS = 'community-events'
NOTIFICATIONS = 'notifications'
TOURNAMENTS = 'tournaments'

# Home tab slugs.
HOME_PLAYER = 'player'
HOME_MY_SCHEDULE = 'my-schedule'
HOME_SCHEDULE = 'schedule'
HOME_MY_CREW = 'my-crew'
HOME_TOURNAMENTS = 'tournaments'

# Volunteer hub tab slugs. My Shifts is retired: the hub sends it to My Schedule.
VOLUNTEER_MY_SHIFTS = 'my-shifts'
VOLUNTEER_CHECK_IN = 'check-in-desk'


def _query(params: dict) -> str:
    """``?a=1&b=2``, dropping empty values; '' when nothing survives.

    ``None`` and ``''`` are omitted entirely (an absent filter is not the same as
    a blank one), and dates are normalised to ISO so a caller can pass the
    ``date`` it already has.
    """
    payload: dict = {}
    for key, value in params.items():
        if value is None or value == '':
            continue
        payload[key] = value.isoformat() if isinstance(value, (date, datetime)) else value
    return f'?{urlencode(payload)}' if payload else ''


def admin_url(section: Optional[str] = None, **params) -> str:
    """Build ``/admin[/<section>][?…]``, dropping empty params."""
    return f"{'/admin/' + section if section else '/admin'}{_query(params)}"


def admin_match_comments_url(match_id: int) -> str:
    """The admin Schedule board narrowed to one match, its sheet open on the comments."""
    return admin_url(SCHEDULE, comments=match_id)


def admin_qualifier_queue_url(qualifier_id: int) -> str:
    """The review queue of one qualifier, open on arrival.

    The queue is the control, not the page: landing a reviewer on
    ``/admin/qualifiers`` leaves them to find the qualifier among several, click
    Manage, and then find the tab — three steps between a "runs are waiting" DM
    and the runs.
    """
    return admin_url(QUALIFIERS, qualifier=qualifier_id, tab='queue')


def qualifier_url(qualifier_id: int) -> str:
    """A player's own page for one async qualifier: start, submit or forfeit a run."""
    return f'/qualifiers/{qualifier_id}'


def qualifier_runs_url(qualifier_id: int) -> str:
    """The same page scrolled to My runs, where a verdict or a forfeit shows up.

    The run-reviewed and run-expired DMs land here. Their button used to say
    "View the leaderboard", which is hidden from players until the qualifier
    closes, so the one thing the reader came to check was the thing they
    could not see.
    """
    return f'{qualifier_url(qualifier_id)}{_query({"focus": "runs"})}'


def home_url(section: Optional[str] = None, **params) -> str:
    """Build ``/home[/<section>][?…]``, dropping empty params."""
    return f"{'/home/' + section if section else '/home'}{_query(params)}"


def volunteer_my_shifts_url() -> str:
    """My Schedule scrolled to your shifts, where "I'm here" and give-back live."""
    return home_url(HOME_MY_SCHEDULE, shifts=1)


def volunteer_release_url(assignment_id: int) -> str:
    """My Schedule with one assignment's "Give this shift back?" dialog open."""
    return home_url(HOME_MY_SCHEDULE, release=assignment_id)


def volunteer_check_in_url(event_id: Optional[int | str] = None) -> str:
    """Volunteer → Check-in Desk, on one check-in event when given."""
    return f'/volunteer/{VOLUNTEER_CHECK_IN}{_query({"event": event_id})}'


def player_match_comments_url(match_id: int) -> str:
    """The Player tab narrowed to one match, with its details open on the comments."""
    return home_url(HOME_PLAYER, comments=match_id)


def player_schedule_url(bracket_match_id: int) -> str:
    """The Player tab with the schedule dialog for one bracket matchup open.

    The entrant's *only* route to booking a bracket game: the public bracket page
    gates its Schedule button behind ``is_staff``, so a DM that sent a player
    there left them at a dialog whose sole button was Close.
    """
    return home_url(HOME_PLAYER, schedule=bracket_match_id)


def player_startgg_schedule_url(startgg_set_id: int) -> str:
    """The Player tab with the schedule dialog for one start.gg set open.

    Its own param for the reason Challonge has one: the ids index a different table.
    """
    return home_url(HOME_PLAYER, startgg=startgg_set_id)


def player_challonge_schedule_url(challonge_match_id: int) -> str:
    """The Player tab with the schedule dialog for one Challonge matchup open.

    The Challonge peer of :func:`player_schedule_url`. A separate param because
    the two ids index different tables, and a native matchup id that happened to
    match a Challonge row would open the wrong dialog.
    """
    return home_url(HOME_PLAYER, book=challonge_match_id)


def player_match_url(match_id: int) -> str:
    """The Player tab narrowed to one match.

    Where the stage DMs land: the player's own board, showing the match the
    message is about and nothing else. ``/admin/match-schedule?match_id=…`` is the
    staff equivalent and no use here, because a player cannot open the admin
    board at all.
    """
    return home_url(HOME_PLAYER, match=match_id)


def crew_match_url(match_id: int) -> str:
    """My Schedule, scrolled to this match's card under Crew you signed up for.

    For the DMs a commentator or tracker gets about a match they crew. The
    player-side ``?match=`` filter is no use to them: it narrows *their own
    matches* to one, and they are not playing in it. The ``my-crew`` slug lands
    on the same tab, and the page reads ``match`` against the viewer's crew
    slots to find the card.
    """
    return home_url(HOME_MY_CREW, match=match_id)


def player_agree_url(request_id: int) -> str:
    """My Schedule with the opponent's reschedule request open for agreement.

    The opponent DM asks for one thing, "Agree if that works for you", and this
    is the web control that does it; without it the ask could only be answered
    from the Discord button.
    """
    return home_url(HOME_PLAYER, agree=request_id)


def player_hard_preset_url(match_id: int) -> str:
    """The Player tab with this match's hard-preset opt-in dialog open.

    The opt-in DM's button. It lands on the choice itself rather than the board,
    because the board's toggle is one small cell among nine columns and the DM's
    whole content is a question about it.
    """
    return home_url(HOME_PLAYER, hard=match_id)


def player_reschedule_url(match_id: int) -> str:
    """The Player tab with the reschedule-request dialog for one match open.

    Where a declined request ends: "ask again with a different time" is a real
    next step, so the DM's button opens the form rather than the tab.
    """
    return home_url(HOME_PLAYER, reschedule=match_id)


def admin_reschedule_request_url(request_id: int) -> str:
    """The admin Schedule board with one request's decision dialog open.

    Not the board filtered to the match: staff opening this have been asked to
    decide, and the reason and proposed time live in the dialog. Landing them on
    a row they must then find and click is the failure the link rules name.
    """
    return admin_url(SCHEDULE, reschedule_request=request_id)


def admin_ada_request_url(request_id: int) -> str:
    """Admin → Users with the ADA requests sub-tab open on one request's dialog.

    A DM that says "this request changed" has to land on that request, not on
    the members table two sub-tabs away from it.
    """
    return admin_url(USERS, ada_request=request_id)


def community_event_url(event_id: int) -> str:
    """One community event's own page, where a member subscribes or a host edits."""
    return f'/community-events/{event_id}'


def admin_community_event_url(event_id: int) -> str:
    """Admin → Community Events with one event's review dialog open."""
    return admin_url(COMMUNITY_EVENTS, community_event=event_id)


def admin_check_in_url() -> str:
    """Admin → Check-in, whose first control is the Matcherino login card."""
    return admin_url(CHECK_IN)
