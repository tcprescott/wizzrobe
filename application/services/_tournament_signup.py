"""Tournament signup — the player's own way in, and out.

Signing up used to be a checkbox on the Profile tab with no window, no
membership check and no event: the UI listed every active tournament in the
tenant and enrolled whoever ticked a box. Everything that makes it a real
opt-in lives here — the window check, the membership gate, the Challonge
carve-out, and the audit-plus-event pair every enrolment change shares,
whoever made it.

Mixed into :class:`~application.services.tournament_service.TournamentService`
— the same composition ``application/services/_bracket/`` and the async
qualifier use — so the orchestration module stays inside the file-length
guideline.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from application.errors import require_found
from application.events import EventType
from application.repositories import TournamentRepository
from application.services._challonge_signup import roster_locked
from application.services.audit_service import AuditActions, AuditService
from application.services.notification_catalog import PLAYERS, SIGNED_UP
from application.services.tenant_membership_service import TenantMembershipService
from application.utils.clients.startgg_client import event_url
from application.utils.discord_embeds import COLOR_SCHEDULED
from application.utils.tournament_signup import SignupWindow, signup_window_state
from models import FeatureFlag, Tournament, User

logger = logging.getLogger(__name__)

# Blurple, the same informational tone the join-request and crew DMs use — a
# signup confirmation is news, not something to act on.
COLOR_SIGNUP = COLOR_SCHEDULED


@dataclass
class TournamentSignupCard:
    """One tournament as the Tournaments tab, REST and MCP all describe it.

    A single shape because the three surfaces have to agree about what "you can
    sign up for this" means: the tab drawing a Sign up button the API would
    refuse is the exact bug that the enrollment checkbox had, only louder.
    """

    tournament: Tournament
    window: SignupWindow
    enrolled: bool
    entrant_count: int
    entrants: List[User] = field(default_factory=list)
    # The users holding Tournament Admin on this one tournament.
    admins: List[User] = field(default_factory=list)
    # Roster lives on a linked Challonge bracket: signing up adds the player
    # there, under the Challonge account linked on their profile.
    challonge_managed: bool = False
    # The viewer's linked Challonge username, None when they have not linked.
    challonge_name: Optional[str] = None
    # The bracket has started, so Challonge no longer takes roster changes.
    roster_locked: bool = False
    # Bracket entrants nobody has mapped to a Wizzrobe user, by Challonge name.
    unmapped_entrant_names: List[str] = field(default_factory=list)
    # Roster lives on a linked start.gg event, read-only: players sign up and
    # withdraw there, and the sync brings them in.
    startgg_url: Optional[str] = None

    @property
    def _roster_open(self) -> bool:
        if self.startgg_url:
            return False
        return not (self.challonge_managed and self.roster_locked)

    @property
    def can_sign_up(self) -> bool:
        return (
            not self.enrolled
            and self.window is SignupWindow.OPEN
            and self._roster_open
            and (not self.challonge_managed or self.challonge_name is not None)
        )

    @property
    def needs_challonge_link(self) -> bool:
        """Open to this viewer once they link Challonge, which they have not."""
        return (
            self.challonge_managed
            and not self.enrolled
            and self.window is SignupWindow.OPEN
            and self._roster_open
            and self.challonge_name is None
        )

    @property
    def signs_up_on_startgg(self) -> bool:
        """Open, but the sign-up itself happens on start.gg."""
        return bool(self.startgg_url) and not self.enrolled and self.window is SignupWindow.OPEN

    @property
    def can_withdraw(self) -> bool:
        # UPCOMING counts: staff can enrol someone before signups open, and
        # there is no roster to protect yet — locking them in before the window
        # has even started would be a rule with nothing behind it. Only a closed
        # window, or a Challonge bracket that has started, makes withdrawing
        # staff's call.
        return (
            self.enrolled
            and self.window is not SignupWindow.CLOSED
            and self._roster_open
        )


async def record_enrolment_change(
    audit_service: AuditService,
    actor: Optional[User],
    target: User,
    tournament_id: int,
    *,
    added: bool,
) -> None:
    """The audit row and the event every enrolment change shares.

    Every path that adds or removes a ``TournamentPlayers`` row calls this: the
    Tournaments tab's own signup, the tournament's roster dialog, the admin user
    dialog, and the auto-enrol that happens when staff schedule someone into a
    tournament they were not in. One audit action for all of them, because it is
    one fact reached from several screens — a parallel action per screen is how
    an audit log stops being answerable.

    The **event** splits by direction. A subscriber mirroring a roster needs to
    know which way it moved, and it has to hear about every move: staff adding an
    entrant is exactly as much a roster change as the player signing themselves
    up, and an event stream that carried only the latter would drift with no way
    to tell from the outside.

    One call per tournament rather than one batched call per screen, so the two
    payloads stay a single tournament wide and every consumer sees the same
    granularity whichever screen made the change.
    """
    # Optional in the signature because the scheduling paths that reach here
    # carry an optional actor; `write_log` raises on None rather than writing an
    # unattributable row, which is the enforcement — not an `if actor:` guard
    # that would silently skip the audit.
    await audit_service.write_and_publish(
        actor,  # type: ignore[arg-type]
        AuditActions.USER_TOURNAMENT_ENROLLMENT_UPDATED,
        {
            'target_user_id': target.id,
            'added_tournament_ids': [tournament_id] if added else [],
            'removed_tournament_ids': [] if added else [tournament_id],
        },
        EventType.TOURNAMENT_ENROLLED if added else EventType.TOURNAMENT_WITHDRAWN,
        event_extra={'tournament_id': tournament_id, 'user_id': target.id},
    )


class TournamentSignupMixin:
    """Player self-service signup, mixed into ``TournamentService``.

    The two collaborators below are set up by the host's ``__init__``; they are
    annotated here so this module type-checks on its own rather than relying on
    whoever mixes it in.
    """

    repository: TournamentRepository
    audit_service: AuditService

    @staticmethod
    async def _challonge_live() -> bool:
        """Whether this community has Challonge on at all.

        A leftover ``challonge_tournament_id`` on a tenant that has since
        disabled the feature must not silently freeze a tournament nobody can
        sign up for, so the flag is checked before the column ever is.
        """
        from application.services.feature_flag_service import FeatureFlagService

        return FeatureFlag.CHALLONGE in await FeatureFlagService().enabled_flags()

    async def _challonge_managed_ids(self) -> set[int]:
        """Tournaments whose roster is driven by a synced Challonge bracket.

        Empty when the community has the feature off. One query for the whole
        card list; ``_signup_target`` asks the same question of a single row it
        already holds.
        """
        if not await self._challonge_live():
            return set()
        rows = await self.repository.get_all(active_only=False)
        return {t.id for t in rows if t.challonge_tournament_id}

    async def list_signup_cards(
        self, user: Optional[User] = None, now: Optional[datetime] = None,
    ) -> List[TournamentSignupCard]:
        """Every active tournament, with this viewer's signup state on each.

        One place resolves the window, the enrolment and the entrant list, so the
        tab, the REST payload and the MCP tool cannot describe the same
        tournament differently. A Challonge-linked tournament's roster is its
        mirrored bracket, not the Wizzrobe player pool.
        """
        tournaments = await self.repository.get_all(active_only=True)
        if not tournaments:
            return []
        ids = [t.id for t in tournaments]
        counts = await self.repository.enrolment_counts()
        entrants = await self.repository.entrants_by_tournament(ids)
        admins = await self.repository.admins_by_tournament(ids)
        enrolled_ids = (
            await self.repository.enrolled_tournament_ids(user.id) if user else set()
        )
        challonge_ids = await self._challonge_managed_ids()
        startgg_live = await self._startgg_live()
        rosters = {}
        if challonge_ids:
            from application.services.challonge_service import ChallongeService

            rosters = await ChallongeService().signup_rosters(
                [t.id for t in tournaments if t.id in challonge_ids]
            )
        challonge_name = (
            user.challonge_username if user and user.challonge_user_id else None
        )
        cards = []
        for t in tournaments:
            window = signup_window_state(t.signups_open_at, t.signups_close_at, now)
            if t.id not in challonge_ids:
                cards.append(TournamentSignupCard(
                    tournament=t,
                    window=window,
                    enrolled=t.id in enrolled_ids,
                    entrant_count=counts.get(t.id, 0),
                    entrants=entrants.get(t.id, []),
                    admins=admins.get(t.id, []),
                    startgg_url=(
                        event_url(t.startgg_event_slug)
                        if startgg_live and t.startgg_event_slug else None
                    ),
                ))
                continue
            roster = rosters.get(t.id, [])
            cards.append(TournamentSignupCard(
                tournament=t,
                window=window,
                enrolled=user is not None and any(p.user_id == user.id for p in roster),
                entrant_count=len(roster),
                entrants=[p.user for p in roster if p.user is not None],
                admins=admins.get(t.id, []),
                challonge_managed=True,
                challonge_name=challonge_name,
                roster_locked=roster_locked(t.challonge_state),
                unmapped_entrant_names=sorted(
                    (p.name or '?' for p in roster if p.user is None), key=str.lower,
                ),
            ))
        return cards

    @staticmethod
    async def _startgg_live() -> bool:
        from application.services.feature_flag_service import FeatureFlagService

        return FeatureFlag.STARTGG in await FeatureFlagService().enabled_flags()

    async def _is_challonge_managed(self, tournament: Tournament) -> bool:
        # Asked of the row in hand rather than through ``_challonge_managed_ids``,
        # which loads the tenant's whole tournament table — worth it once per
        # page build, wasteful on every single signup.
        return bool(tournament.challonge_tournament_id) and await self._challonge_live()

    async def _signup_target(self, tournament_id: int, *, joining: bool) -> Tournament:
        """Load a tournament a player may act on, or say why they may not.

        A missing id — including another community's, since the repository read
        is tenant-scoped — is ``not_found``, not a refusal: "that tournament is
        not accepting signups" would tell a caller a tournament exists when it
        does not. Everything after that is a real refusal about a real row.

        ``joining`` separates the two directions at the one point they differ:
        you cannot sign up before the window opens, but you *can* back out —
        staff may have entered you early, and there is no roster to protect yet.
        """
        tournament = require_found(
            await self.repository.get_by_id(tournament_id), f'Tournament {tournament_id}',
        )
        if not tournament.is_active:
            raise ValueError('That tournament is not accepting signups.')
        if tournament.startgg_event_slug and await self._startgg_live():
            raise ValueError(
                f'{tournament.name} takes sign-ups on start.gg: '
                f'{event_url(tournament.startgg_event_slug)}'
            )
        state = signup_window_state(tournament.signups_open_at, tournament.signups_close_at)
        if state is SignupWindow.CLOSED:
            raise ValueError(
                f'Signups for {tournament.name} are closed. Ask staff if you still want in.'
            )
        if joining and state is SignupWindow.UPCOMING:
            raise ValueError(f'Signups for {tournament.name} have not opened yet.')
        return tournament

    async def self_enroll(self, tournament_id: int, user: User) -> Tournament:
        """Sign the caller up for a tournament, from the Tournaments tab.

        The membership check is the one the Profile-tab checkbox never had: it
        listed every active tournament in the tenant and enrolled whoever ticked
        a box, with no gate at all between the widget and the row.
        """
        tournament = await self._signup_target(tournament_id, joining=True)
        if not await TenantMembershipService().is_member(user):
            raise ValueError('Join this community before signing up for its tournaments.')
        if await self._is_challonge_managed(tournament):
            from application.services.challonge_service import ChallongeService

            await ChallongeService().sign_up_player(tournament, user)
            await self._notify_signup(user, tournament)
            return tournament
        if await self.repository.is_player_enrolled(tournament, user):
            raise ValueError(f'You are already signed up for {tournament.name}.')
        await self.repository.enroll_player(tournament, user)
        await self._record_enrolment(user, user, tournament, added=True)
        await self._notify_signup(user, tournament)
        return tournament

    async def self_withdraw(self, tournament_id: int, user: User) -> Tournament:
        """Withdraw the caller, any time before the signup window closes.

        Once it closes, withdrawing is staff's call — a roster the bracket has
        already been seeded from should not lose an entrant silently.
        """
        tournament = await self._signup_target(tournament_id, joining=False)
        if await self._is_challonge_managed(tournament):
            from application.services.challonge_service import ChallongeService

            await ChallongeService().withdraw_player(tournament, user)
            return tournament
        if not await self.repository.is_player_enrolled(tournament, user):
            raise ValueError(f'You are not signed up for {tournament.name}.')
        await self.repository.unenroll_player(tournament, user)
        await self._record_enrolment(user, user, tournament, added=False)
        return tournament

    async def _record_enrolment(
        self, actor: User, target: User, tournament: Tournament, *, added: bool,
    ) -> None:
        return await record_enrolment_change(
            self.audit_service, actor, target, tournament.id, added=added,
        )

    async def _notify_signup(self, user: User, tournament: Tournament) -> None:
        """Confirm a signup by DM, with a button back to the tab that made it.

        Best-effort: a Discord outage must not undo an enrolment that already
        committed, so every failure below is swallowed.
        """
        if not user.discord_id:
            return
        try:
            from application.services import notification_links
            from application.services.discord import DiscordService, discord_queue
            from application.services.tenant_service import TenantService
            from application.utils.discord_embeds import notification_embed
            from application.utils.discord_messages import tournament_signup_dm

            community = await TenantService.current_community_name()
            body = tournament_signup_dm(
                tournament.name,
                can_request=bool(getattr(tournament, 'allow_player_match_requests', False)),
            )
            embed = notification_embed(
                title='🏁 Signed up',
                color=COLOR_SIGNUP,
                community_name=community,
                description=body,
            )
            link = await notification_links.home_tournaments()
            discord_queue.enqueue(
                DiscordService().send_dm(
                    int(user.discord_id), body, embed=embed, link=link, notice=SIGNED_UP.to(PLAYERS),
                )
            )
        except Exception:
            logger.exception('tournament signup DM failed for user %s', user.id)

