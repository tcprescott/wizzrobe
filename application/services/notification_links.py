"""Where each notification's call to action goes.

Every DM that asks the recipient to *do* something gets a :class:`DMLink` from
here, which ``send_dm`` renders as a Discord link button and hands to the
web-push mirror as its tap target. One module rather than a private
``_url`` helper per service, because the same three mistakes kept recurring:

* a **relative** path, meaningless in a DM read outside any request context;
* a link to a page that merely *mentions* the thing, leaving the reader to find
  it — the ask has to land on the control that performs it;
* a link to a surface the recipient cannot act on. The matchup-ready DM pointed
  entrants at ``/brackets/<id>``, whose Schedule button is ``is_staff``-gated, so
  the one message in the app whose whole purpose is "go book this" ended at a
  dialog with a Close button. :func:`player_schedule` is that fix.

Every builder returns ``None`` when the tenant cannot be resolved. A DM without a
button is worse than no DM; a DM with a button pointing nowhere is worse than
both, and Discord rejects an empty link URL outright.

Service-layer, not ``utils``: resolving the tenant in scope needs
:class:`TenantService`. The *paths* stay pure in
:mod:`application.utils.app_links`, shared with the pages that render the same
routes.
"""

import logging
from typing import Any, Optional

from application.event_context import get_current_event_id
from application.services.notification_catalog import CATALOG, NoticeTarget
from application.tenant_context import get_current_tenant_id
from application.utils.app_links import (
    HOME_MY_CREW,
    HOME_SCHEDULE,
    HOME_TOURNAMENTS,
    SCHEDULE,
    TOURNAMENTS,
    USERS,
    VOL_SCHEDULE,
    admin_ada_request_url,
    admin_check_in_url,
    admin_community_event_url,
    admin_match_comments_url,
    admin_qualifier_queue_url,
    admin_reschedule_request_url,
    admin_url,
    community_event_url,
    crew_match_url,
    home_url,
    my_message_url,
    player_agree_url,
    player_challonge_schedule_url,
    player_hard_preset_url,
    player_match_comments_url,
    player_match_url,
    player_reschedule_url,
    player_schedule_url,
    player_startgg_schedule_url,
    qualifier_runs_url,
    qualifier_url,
    survey_url,
    volunteer_my_shifts_url,
    volunteer_release_url,
)
from application.utils.discord_messages import DMLink
from application.utils.settings_footnote import SettingsFootnote
from application.utils.tenant_urls import event_path, tenant_url

logger = logging.getLogger(__name__)


async def _current_tenant() -> Optional[Any]:
    """The tenant in scope, or ``None``.

    Callable from a request handler *and* from inside the ``discord_queue``
    worker, which re-binds the enqueuing request's ``tenant_scope`` around each
    coroutine it awaits.
    """
    from application.services.tenant_service import TenantService

    tenant_id = get_current_tenant_id()
    if tenant_id is None:
        return None
    return await TenantService.get_by_id(tenant_id)


async def link_for(label: str, path: str) -> Optional[DMLink]:
    """``label`` → the tenant-absolute form of ``path`` in the event in scope, or ``None``.

    Absolute and tenant-qualified because a DM is read outside any request
    context: a bare ``/home/player`` resolves against nothing, and a path-mode
    community's prefix (or its own domain) is exactly what ``tenant_url`` adds.
    The event's ``/e/<slug>`` goes in too, so a DM sent this year still opens
    this year's page after next year's event becomes the default.

    Never raises. Every caller is a best-effort notifier that swallows its own
    Discord failures, so a builder that threw here would take the whole DM down
    with it — recipients would lose the message entirely over a decoration on it.
    """
    try:
        tenant = await _current_tenant()
    except Exception:
        logger.exception('notification link lookup failed for %s', path)
        return None
    # A bare link still opens: a row page finds its own event, any other the default.
    try:
        slug = await event_slug(get_current_tenant_id(), get_current_event_id())
    except Exception:
        logger.warning('event lookup for a notification link failed; sending it bare', exc_info=True)
        slug = None
    return link_for_tenant(tenant, label, path, event_slug=slug)


async def event_slug(tenant_id: Optional[int], event_id: Optional[int]) -> Optional[str]:
    """The slug of ``event_id`` in ``tenant_id``, or ``None`` when either is unknown."""
    from application.services.event_service import EventService

    if tenant_id is None or event_id is None:
        return None
    ref = await EventService.ref(tenant_id, event_id)
    return ref.slug if ref else None


def link_for_tenant(
    tenant: Any, label: str, path: str, *, event_slug: Optional[str] = None,
) -> Optional[DMLink]:
    """:func:`link_for` for a caller that already holds the tenant row.

    The qualifier notifications run off a loaded ``run.tenant`` rather than the
    ambient scope, and re-resolving it would be a query for a row in hand.
    Without ``event_slug`` the link is bare: fine for a row page, which opens in
    its row's own event wherever the link pointed.
    """
    return DMLink(label, tenant_url(tenant, event_path(event_slug, path))) if tenant else None


# ---------------------------------------------------------------------------
# Player-facing targets
# ---------------------------------------------------------------------------

async def player_schedule(
    bracket_match_id: int, *, label: str = 'Pick a time',
) -> Optional[DMLink]:
    """"Pick a time" — the Player tab with this matchup's schedule dialog open.

    The entrant's only route to booking a bracket game, and now one press from
    the DM that tells them to book it.
    """
    return await link_for(label, player_schedule_url(bracket_match_id))


async def player_challonge_schedule(
    challonge_match_id: int, *, label: str = 'Pick a time',
) -> Optional[DMLink]:
    """"Pick a time" for a Challonge matchup — its schedule dialog, already open."""
    return await link_for(label, player_challonge_schedule_url(challonge_match_id))


async def player_startgg_schedule(
    startgg_set_id: int, *, label: str = 'Pick a time',
) -> Optional[DMLink]:
    """"Pick a time" for a start.gg set — its schedule dialog, already open."""
    return await link_for(label, player_startgg_schedule_url(startgg_set_id))


def player_qualifier(tenant: Any, qualifier_id: int, *, label: str) -> Optional[DMLink]:
    """One qualifier's player page, where a run is started, submitted or forfeited.

    Takes the tenant row because the qualifier DMs hold it already (see
    :func:`link_for_tenant`).
    """
    return link_for_tenant(tenant, label, qualifier_url(qualifier_id))


def player_qualifier_runs(
    tenant: Any, qualifier_id: int, *, label: str = 'See your runs',
) -> Optional[DMLink]:
    """The qualifier page scrolled to My runs — the verdict, the note, the status.

    Not the leaderboard: players cannot see it until the qualifier closes.
    """
    return link_for_tenant(tenant, label, qualifier_runs_url(qualifier_id))


async def player_reschedule(
    match_id: int, *, label: str = 'Ask again',
) -> Optional[DMLink]:
    """"Ask again" — the Player tab with this match's request form open.

    A decline is only a dead end if the DM makes it one. The requester's next
    move is a different time, so the button opens the form that takes one.
    """
    return await link_for(label, player_reschedule_url(match_id))


async def player_hard_preset(
    match_id: int, *, label: str = 'Choose your settings',
) -> Optional[DMLink]:
    """"Choose your settings" — the Player tab with this match's opt-in open.

    The one route to an opt-in that is deliberately invisible everywhere else:
    a reader who cannot find the control cannot use the feature, and the control
    is a single cell on a nine-column board.
    """
    return await link_for(label, player_hard_preset_url(match_id))


async def player_match(
    match_id: int, *, label: str = 'View your match',
) -> Optional[DMLink]:
    """"View your match" — the Player tab showing this one match.

    The stage DMs' button. :func:`admin_match` points at the same match on the
    admin board, which most of these recipients cannot open, so a stage call
    sent there would be the third failure this module's docstring lists.
    """
    return await link_for(label, player_match_url(match_id))


async def player_match_comments(
    match_id: int, *, label: str = 'Open the match',
) -> Optional[DMLink]:
    """The staff-comment DM's button: the player's match with its comments open."""
    return await link_for(label, player_match_comments_url(match_id))


async def player_agree(
    request_id: int, *, label: str = 'Open the request',
) -> Optional[DMLink]:
    """The opponent's reschedule request, open with its Agree button.

    The DM's Discord Agree button covers a reader in Discord; this covers the
    web-push tap and anyone who would rather read the request in full first.
    """
    return await link_for(label, player_agree_url(request_id))


async def crew_match(
    match_id: int, *, label: str = 'View your crew slot',
) -> Optional[DMLink]:
    """A crew member's own slot on this match, under Crew you signed up for.

    :func:`player_match` filters the reader's *playing* schedule, which is empty
    for someone commentating, so a stage DM that sent crew there landed on
    "No matches to show yet".
    """
    return await link_for(label, crew_match_url(match_id))


async def crew_invites(*, label: str = 'Open your invites') -> Optional[DMLink]:
    """My Schedule, whose first card lists the commentary invites waiting on the reader."""
    return await link_for(label, home_url(HOME_MY_CREW))


def race_room(url: str, *, label: str = 'Join the race room') -> Optional[DMLink]:
    """The racetime.gg room itself — already absolute, and the only place to join.

    Not tenant-qualified because it is not ours: the room lives on racetime.gg,
    and the join control is there. ``None`` for a blank URL, since Discord
    rejects an empty link button.
    """
    return DMLink(label, url) if url else None


async def community_schedule(*, label: str = 'View the schedule') -> Optional[DMLink]:
    """The community schedule — for a DM about somebody *else's* match.

    A subscriber weighing whether to crew a stream candidate, or a watcher told
    where a match moved, is not being shown their own fixture: a button that
    says "your matches" would claim they were, and the player board is empty for
    them.
    """
    return await link_for(label, home_url(HOME_SCHEDULE))


async def home_tournaments(*, label: str = 'View tournaments') -> Optional[DMLink]:
    """The Tournaments tab — where signing up and withdrawing both happen.

    Also the join-approved DM's button: a new member's next step is finding
    something to enter, not the community's match board.

    The signup confirmation's button, so the next thought after "am I in?" —
    checking, or backing out while the window is still open — is one press away
    rather than a hunt through Home.
    """
    return await link_for(label, home_url(HOME_TOURNAMENTS))


# ---------------------------------------------------------------------------
# Staff-facing targets
# ---------------------------------------------------------------------------

async def admin_match(match_id: int, *, label: str = 'Open the match') -> Optional[DMLink]:
    """The admin Schedule board filtered to one match — where crew gets refilled."""
    return await link_for(label, admin_url(SCHEDULE, match_id=match_id))


async def admin_match_comments(
    match_id: int, *, label: str = 'Read the comment',
) -> Optional[DMLink]:
    """A player's comment, read where staff answer it: the match sheet's comments."""
    return await link_for(label, admin_match_comments_url(match_id))


async def admin_reschedule_request(
    request_id: int, *, label: str = 'Review the request',
) -> Optional[DMLink]:
    """The decision dialog for one reschedule request, open on arrival.

    ``admin_match`` would land staff on the board filtered to the match, which
    shows the schedule but not the ask: the proposed time and the player's
    reason are the whole point and live in the dialog.
    """
    return await link_for(label, admin_reschedule_request_url(request_id))


async def profile_connected_accounts(*, label: str = 'Link racetime') -> Optional[DMLink]:
    return await link_for(label, '/home/profile?focus=connected-accounts')


async def profile_notifications(*, label: str = 'Notification settings') -> Optional[DMLink]:
    return await link_for(label, '/home/profile?focus=notifications')


async def settings_footnote(
    notice: Optional[NoticeTarget], message_id: Optional[int] = None,
) -> Optional[SettingsFootnote]:
    """The footnote for a catalogued DM, or ``None``.

    Only catalogued messages get one: anything else (a channel test, the
    platform health alert) has no setting to point at. With the logged
    message's id it opens that message in your history, its switch beside it;
    without one (the log write failed) it falls back to Profile → Notifications.
    """
    if notice is None:
        return None
    if message_id is not None:
        link = await link_for('Notification settings', my_message_url(message_id))
    else:
        link = await profile_notifications()
    return SettingsFootnote(CATALOG[notice.key].title, link) if link else None


async def volunteer_my_shifts(*, label: str = 'Your shifts') -> Optional[DMLink]:
    """My Schedule at your shifts, for a DM whose own buttons already carry the actions."""
    return await link_for(label, volunteer_my_shifts_url())


async def volunteer_release(
    assignment_id: int, *, label: str = "Can't make it",
) -> Optional[DMLink]:
    """My Schedule with this shift's give-back dialog open, for the volunteer on it."""
    return await link_for(label, volunteer_release_url(assignment_id))


async def admin_volunteer_schedule(
    day: Any = None, *, label: str = 'Open the shift',
) -> Optional[DMLink]:
    """The volunteer schedule, on the day of the shift that just opened up."""
    return await link_for(label, admin_url(VOL_SCHEDULE, day=day))


async def admin_tournaments(*, label: str = 'Open tournaments') -> Optional[DMLink]:
    """Admin → Tournaments, where a tournament's players are managed."""
    return await link_for(label, admin_url(TOURNAMENTS))


async def admin_users(*, label: str = 'Review the request') -> Optional[DMLink]:
    """The Members tab, where join requests are approved or denied."""
    return await link_for(label, admin_url(USERS))


async def admin_ada_request(
    request_id: int, *, label: str = 'Open the request',
) -> Optional[DMLink]:
    """One ADA request's Update dialog, open on arrival."""
    return await link_for(label, admin_ada_request_url(request_id))


async def community_event(event_id: int, *, label: str = 'Open the event') -> Optional[DMLink]:
    """One community event's page, for its host and its subscribers."""
    return await link_for(label, community_event_url(event_id))


async def admin_community_event(
    event_id: int, *, label: str = 'Review the event',
) -> Optional[DMLink]:
    """Admin → Community Events with this event's review dialog open on arrival."""
    return await link_for(label, admin_community_event_url(event_id))


async def survey(survey_id: int, *, label: str = 'Take the survey') -> Optional[DMLink]:
    """The survey itself, open on its list of parts: the control the DM asks for."""
    return await link_for(label, survey_url(survey_id))


async def admin_matcherino_login(*, label: str = 'Update the Matcherino login') -> Optional[DMLink]:
    """Admin → Check-in, where the Matcherino login card takes a fresh token."""
    return await link_for(label, admin_check_in_url())


async def admin_qualifier_queue(
    qualifier_id: int, *, label: str = 'Open the review queue',
) -> Optional[DMLink]:
    """One qualifier's review queue, open on arrival — the control, not the page."""
    return await link_for(label, admin_qualifier_queue_url(qualifier_id))
