"""The door beside the membership gate.

A non-member reaching a tenant page gets this rather than a 403. The distinction
matters: forbidden-by-role is a dead end, but not-a-member is a state with a
remedy, and the page whose whole job is to offer that remedy should not open by
telling you no.

It is also the community's whole first impression, so it carries two previews of
what is behind the gate: the published brackets (already world-readable, just
unreachable without the URL) and — only where staff turned it on — today's match
times and player names.

Rendered synchronously into the current page context, like
:mod:`theme.error_page`, so it can be called from the middleware decorator
without restructuring it. Everything that needs an ``await`` is resolved by
:func:`resolve_join_preview` first and handed in.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

from nicegui import background_tasks, context, ui

from application.utils.match_labels import has_undecided, matchup_parts
from application.utils.tenant_urls import login_path
from models import JoinRequestStatus, User
from theme.base import BaseLayout, current_local_url
from theme.links import link_button
from theme.matchup import matchup_label

logger = logging.getLogger(__name__)

#: How many of today's matches the door lists. It is a first impression, not a
#: schedule board — a real event day runs to thirty-plus rows, which buries the
#: Request access button under a wall of names nobody outside the community can
#: act on. The rest are counted, not listed.
PREVIEW_LIMIT = 12


@dataclass
class PreviewMatch:
    """One row of the today's-matches preview, already formatted."""

    time: str
    players: str
    tournament: str
    #: ``players`` in pieces when a side is still a bracket placeholder, else empty.
    players_parts: list = field(default_factory=list)


@dataclass
class JoinPreview:
    """What a non-member may see of this community, resolved and formatted."""

    #: ``(tournament_id, tournament_name)`` for each tournament with published
    #: stages. Empty when BRACKETS is off or nothing is published yet.
    tournaments: List[Tuple[int, str]] = field(default_factory=list)
    #: Today's matches, on the **community's** clock. Player names are public.
    matches: List[PreviewMatch] = field(default_factory=list)
    #: Matches today beyond the ones listed above, or 0.
    more_matches: int = 0
    #: The zone the times above are in, e.g. ``EST``.
    timezone_label: str = ''
    #: Staff's invite to the community's Discord server, or None.
    discord_invite_url: Optional[str] = None
    #: Whether members of the linked server get in without asking. Only True
    #: when staff turned it on *and* a server is linked, since the door must not
    #: promise a way in that the gate will not honour.
    discord_auto_join: bool = False
    #: Whether the door takes join requests. Off leaves auto-join (if on) as
    #: the only way in short of staff adding someone by hand.
    join_requests: bool = True
    #: Whether anyone who signs in is let straight in. A signed-in visitor
    #: only sees the door then if letting them in failed.
    open_membership: bool = False


def roster_parts(match) -> list:
    """:func:`roster_label` in pieces, flagging the sides a bracket hasn't decided.

    Empty for a match with no roster, which reads as its title instead.
    """
    return matchup_parts(
        [p.user.preferred_name for p in match.players],
        list(getattr(match, 'bracket_placeholders', None) or []),
        max_names=2,
    )


def _undecided_parts(match) -> list:
    parts = roster_parts(match)
    return parts if has_undecided(parts) else []


def roster_label(match) -> str:
    """Who is playing, in one line that stays one line.

    A bracket match names both players. A ten-racer play-in named all ten and
    ran off the card, so past two the rest are counted.
    """
    names = [p.user.preferred_name for p in match.players]
    names += list(getattr(match, 'bracket_placeholders', None) or [])
    if not names:
        return match.title or 'TBD'
    if len(names) <= 2:
        return ' vs '.join(names)
    return f'{names[0]} vs {names[1]} +{len(names) - 2} more'


async def resolve_join_preview(tenant_id: int) -> JoinPreview:
    """Everything the join page shows a non-member, or an empty preview.

    Never raises: this decorates the door, and a community whose bracket list or
    schedule read fails should still be able to take a join request.
    """
    from application.services import BracketService, MatchService
    from application.services.feature_flag_service import FeatureFlagService
    from application.services.system_config_service import (
        KEY_DISCORD_AUTO_JOIN,
        KEY_JOIN_REQUESTS,
        KEY_OPEN_MEMBERSHIP,
        SystemConfigService,
    )
    from application.services.tenant_service import TenantService
    from application.services.timezone_service import TimezoneService
    from application.timezone_context import tz_scope
    from application.utils.timezone import format_local_time, timezone_label, today_local
    from models import FeatureFlag

    preview = JoinPreview()

    try:
        preview.join_requests = await SystemConfigService.get_bool(
            KEY_JOIN_REQUESTS, default=True,
        )
        preview.open_membership = await SystemConfigService.get_bool(KEY_OPEN_MEMBERSHIP)
    except Exception:
        logger.exception('Join page: join-request setting failed for tenant %s', tenant_id)

    try:
        if await FeatureFlagService().is_enabled(FeatureFlag.BRACKETS):
            # Anonymous, always: a DRAFT or CANCELLED stage is unpublished, and
            # the door is read by people who hold no role here by definition.
            stages = await BracketService().list_all_visible_brackets(None)
            seen: dict = {}
            for stage in stages:
                seen.setdefault(
                    stage.tournament_id,
                    stage.tournament.name,
                )
            preview.tournaments = list(seen.items())
    except Exception:
        logger.exception('Join page: bracket preview failed for tenant %s', tenant_id)

    try:
        # The community's own clock, not the visitor's: "today" here means
        # the event's day, and someone reading from two zones over must not
        # be shown a different list than the person standing in the venue.
        tz = await TimezoneService.tenant_timezone_name(tenant_id)
        with tz_scope(tz):
            preview.timezone_label = timezone_label()
            # Unfinished only: a door showing what already happened this
            # morning answers a question nobody standing outside is asking.
            matches = await MatchService().get_matches_for_date(
                today_local(), exclude_finished=True, require_stage=False,
            )
            preview.matches = [
                PreviewMatch(
                    time=format_local_time(match.scheduled_at),
                    players=roster_label(match),
                    tournament=match.tournament.name if match.tournament else '',
                    players_parts=_undecided_parts(match),
                )
                for match in matches[:PREVIEW_LIMIT]
            ]
            preview.more_matches = max(0, len(matches) - PREVIEW_LIMIT)
    except Exception:
        logger.exception('Join page: match preview failed for tenant %s', tenant_id)

    try:
        preview.discord_invite_url = await SystemConfigService.get_discord_invite_url()
        if await SystemConfigService.get_bool(KEY_DISCORD_AUTO_JOIN):
            tenant = await TenantService.get_by_id(tenant_id)
            preview.discord_auto_join = bool(tenant and tenant.discord_guild_id)
    except Exception:
        logger.exception('Join page: Discord settings failed for tenant %s', tenant_id)

    return preview


def _render_discord(preview: JoinPreview, *, signed_in: bool) -> None:
    """The invite, and for auto-join, the button that re-runs the gate.

    The invite opens in a new tab so this one is still here, with its Check
    again button, when they come back from accepting it.
    """
    url = preview.discord_invite_url
    recheck = signed_in and preview.discord_auto_join
    if not url and not recheck:
        return
    with ui.row().classes('items-center justify-center gap-2'):
        if url:
            link_button('Join the Discord server', url, icon='forum') \
                .props('color=primary outline no-caps')
        if recheck:
            ui.button(
                'I’ve joined, check again', icon='refresh',
                on_click=ui.navigate.reload,
            ).props('flat no-caps')


def _render_preview(preview: JoinPreview) -> None:
    """The two look-inside sections, beneath whatever the door is offering."""
    ui.separator().classes('separator-spacing')

    if preview.tournaments:
        ui.label('Brackets').classes('text-subtitle2 text-bold')
        for tournament_id, name in preview.tournaments:
            ui.button(
                name, icon='account_tree',
                # The static twin, not the interactive page: the door is where a
                # crowd lands, and every interactive tab costs a websocket.
                on_click=lambda tid=tournament_id: ui.navigate.to(
                    f'/live/tournament/{tid}/brackets'
                ),
            ).props('flat dense no-caps no-wrap align=left') \
                .classes('w-full join-preview-button').tooltip(name)

    label = 'Today' + (f' ({preview.timezone_label})' if preview.timezone_label else '')
    ui.label(label).classes('text-subtitle2 text-bold q-mt-md')
    if not preview.matches:
        ui.label('Nothing scheduled today.').classes('italic-note')
        return
    for match in preview.matches:
        with ui.row().classes('items-center gap-3 no-wrap w-full'):
            ui.label(match.time).classes('text-mono text-caption')
            if match.players_parts:
                matchup_label(match.players_parts, 'text-body2 col ellipsis', tag='div')
            else:
                ui.label(match.players).classes('text-body2 col ellipsis')
            if match.tournament:
                ui.label(match.tournament).classes('text-caption text-grey ellipsis')
    if preview.more_matches:
        ui.label(f'…and {preview.more_matches} more today.').classes('text-caption text-grey')


WATCH_LINE = 'Here to watch? Streams and what’s on now, no sign-in needed.'


#: First, because a member who is merely signed out — the usual reader of a
#: Discord button opened on a phone — lands here too, and the way past the door
#: for them is one tap, not a request.
_MEMBER_LINE = 'Already a member? Sign in and you’ll land right back on this page.'


def _signed_out_message(preview: JoinPreview) -> str:
    if preview.open_membership:
        return 'Anyone can join. Sign in with Discord and you’re in, right on this page.'
    if preview.discord_auto_join and preview.join_requests:
        return (
            f'{_MEMBER_LINE} New here? Members of this community’s Discord '
            'server get in straight away; anyone else can ask its staff.'
        )
    if preview.discord_auto_join:
        return (
            f'{_MEMBER_LINE} New here? Members of this community’s Discord '
            'server get in straight away.'
        )
    if preview.join_requests:
        return f'{_MEMBER_LINE} New here? Sign in to ask to join.'
    return f'{_MEMBER_LINE} This community isn’t taking join requests.'


def _non_member_message(preview: JoinPreview) -> str:
    if preview.open_membership:
        # Only reachable when letting them in just failed.
        return (
            'This community is open to anyone, but we couldn’t add you just now. '
            'Reload the page to try again.'
        )
    if preview.discord_auto_join and preview.join_requests:
        return (
            'You aren’t a member of this community. Members of its Discord '
            'server get in automatically, so join it and check again, or ask to '
            'join and its staff will decide.'
        )
    if preview.discord_auto_join:
        return (
            'You aren’t a member of this community. Members of its Discord '
            'server get in automatically, so join it and check again.'
        )
    if preview.join_requests:
        return (
            'You aren’t a member of this community. Ask to join and its '
            'staff will decide.'
        )
    return (
        'You aren’t a member of this community, and it isn’t taking join '
        'requests. Its staff can add you directly.'
    )


def render_join_page(
    *,
    tenant_id: int,
    tenant_name: str,
    user: Optional[User],
    pending: bool = False,
    preview: Optional[JoinPreview] = None,
    layout: Optional[BaseLayout] = None,
    removed: bool = False,
    ask_again_at: Optional[datetime] = None,
) -> None:
    """Ask to join, or say the request is already in.

    ``pending``, ``preview``, ``layout`` and ``ask_again_at`` (set while a
    declined requester's cooldown runs) are resolved by the caller (which has
    already loaded the user), so this stays synchronous.
    """
    ui.page_title(f'{tenant_name} — Join')
    preview = preview or JoinPreview()

    try:
        (layout or BaseLayout(user=user)).render_chrome()
    except Exception:  # pragma: no cover - defensive, mirroring error_page
        pass

    with ui.column().classes('error-page-container'):
        with ui.card().classes('error-card join-card'):
            ui.icon('meeting_room').props('size=xl color=primary')
            ui.label(tenant_name).classes('error-headline').tag = 'h1'

            # First, for everyone: most people who reach this door from a tweet,
            # the stream or a QR code came to watch, and watching needs no
            # account. The Watch page is static, so the crowd costs no socket.
            ui.label(WATCH_LINE).classes('error-message')
            ui.button(
                'Watch live', icon='live_tv',
                on_click=lambda: ui.navigate.to('/live'),
            ).props('color=primary unelevated size=lg no-caps').classes('join-watch-button')
            ui.separator().classes('separator-spacing')

            if user is None:
                ui.label(_signed_out_message(preview)).classes('error-message')
                # Absolute and carrying the page: a relative 'login' resolved
                # against /home/<section> to /home/login, which is this door again.
                sign_in = login_path(current_local_url())
                ui.button('Sign in', icon='login',
                          on_click=lambda: ui.navigate.to(sign_in)).props('color=primary outline')
                _render_discord(preview, signed_in=False)
                _render_preview(preview)
                return

            if pending:
                # Once asked, there is nothing else to offer — a second button
                # would only produce a second identical request.
                ui.label(
                    'Your request to join is with this community’s staff. '
                    'You will get a message either way.'
                    + (
                        ' Joining its Discord server gets you in without waiting.'
                        if preview.discord_auto_join else ''
                    )
                ).classes('error-message')
                _render_discord(preview, signed_in=True)
                _render_preview(preview)
                return

            if ask_again_at is not None and preview.join_requests:
                # Said up front, with the date, rather than a form that refuses
                # on submit. Auto-join (if on) still works meanwhile.
                from application.utils.timezone import format_local_display

                # With auto-join on, the server is the way in and the cooldown
                # only holds back a new web request; say that first.
                when = format_local_display(ask_again_at)
                ui.label(
                    'Staff declined your request to join. Members of its Discord '
                    'server get in automatically, so join it and check again. You '
                    f'can send a new request from {when}.'
                    if preview.discord_auto_join else
                    f'Staff declined your request to join. You can ask again from {when}.'
                ).classes('error-message')
                _render_discord(preview, signed_in=True)
                _render_preview(preview)
                return

            if removed:
                # Open membership and Discord auto-join would otherwise have let
                # them in before this page drew, so say why they didn't.
                ui.label(
                    'Staff removed you from this community, so you can’t rejoin on '
                    'your own. '
                    + ('Ask to join and its staff will decide.' if preview.join_requests
                       else 'Reach out to its staff if you think that’s a mistake.')
                ).classes('error-message')
            else:
                ui.label(_non_member_message(preview)).classes('error-message')
                _render_discord(preview, signed_in=True)

            if not preview.join_requests:
                _render_preview(preview)
                return

            message = ui.textarea(
                'Anything they should know? (optional)',
            ).classes('w-full').props('maxlength=500 counter autogrow')

            async def submit(client) -> None:
                from application.services import TenantMembershipService

                with client:
                    try:
                        await TenantMembershipService().request_to_join(
                            user, tenant_id, message.value,
                        )
                    except (ValueError, PermissionError) as e:
                        ui.notify(str(e), color='warning')
                        return
                    ui.notify('Request sent.', color='positive')
                    # Re-enter the page so it re-renders in its pending state
                    # rather than leaving a button that would resubmit.
                    ui.navigate.reload()

            ui.button(
                'Request access', icon='how_to_reg',
                on_click=lambda: background_tasks.create(submit(context.client)),
            ).props('color=primary')

            _render_preview(preview)


async def resolve_removed(user: Optional[User], tenant_id: int) -> bool:
    """Whether staff removed this user here, so the door explains why it stayed shut."""
    if user is None:
        return False
    from application.services import TenantMembershipService

    return await TenantMembershipService.was_removed(user, tenant_id)


async def resolve_join_state(user: Optional[User], tenant_id: int) -> bool:
    """Whether this user already has a pending request in this tenant."""
    if user is None:
        return False
    from application.services import TenantMembershipService

    request = await TenantMembershipService().get_request(user, tenant_id)
    return request is not None and request.status is JoinRequestStatus.PENDING


async def resolve_ask_again_at(user: Optional[User], tenant_id: int) -> Optional[datetime]:
    """When a declined requester may ask again, or None if they may now."""
    if user is None:
        return None
    from application.services import TenantMembershipService

    return await TenantMembershipService().next_request_allowed_at(user, tenant_id)
