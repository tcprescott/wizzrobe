"""
User Repository - Data Access Layer

Handles database operations for users.
"""

from collections import Counter
from typing import Dict, Iterable, List, NamedTuple, Optional, Tuple

from tortoise.expressions import Q

from application.repositories._base import TenantScopedRepository
from application.repositories._save import save_fields
from application.repositories._tenant import current_tenant_id
from models import SYSTEM_USER_DISCORD_ID, EventStatus, Role, Tournament, User, UserRole


class PersonSummary(NamedTuple):
    """The few columns a person picker or name search reads, without the rest of ``User``."""

    id: int
    username: str
    display_name: Optional[str]
    twitch_username: Optional[str]
    matcherino_username: Optional[str]
    discord_id: Optional[int]
    is_active: bool

    @property
    def preferred_name(self) -> str:
        return self.display_name if self.display_name else self.username


class UserRepository(TenantScopedRepository[User]):
    """Repository for user data access.

    ``User`` is a **global** identity with no tenant column, so ``create`` and
    ``get_by_id`` are overridden to stay unscoped; only the tenant-agnostic
    ``update``/``delete`` come from the base.
    """

    model = User

    @staticmethod
    async def get_by_id(user_id: int) -> Optional[User]:
        return await User.get_or_none(id=user_id)

    @staticmethod
    async def get_by_ids(user_ids: List[int]) -> dict[int, User]:
        """Resolve many users in a single query, keyed by id.

        Callers that need to validate a list of referenced user ids should use
        this instead of a per-id ``get_by_id`` loop (one round-trip, not N).
        """
        if not user_ids:
            return {}
        rows = await User.filter(id__in=list(set(user_ids)))
        return {u.id: u for u in rows}

    @staticmethod
    async def get_in_order(user_ids: List[int]) -> List[User]:
        """The users with ``user_ids``, in that order, skipping any that no longer exist."""
        found = await UserRepository.get_by_ids(user_ids)
        return [found[uid] for uid in user_ids if uid in found]

    @staticmethod
    async def get_by_discord_id(discord_id: str) -> Optional[User]:
        return await User.get_or_none(discord_id=discord_id)

    @staticmethod
    async def get_by_racetime_user_id(racetime_user_id: str) -> Optional[User]:
        return await User.filter(racetime_user_id=racetime_user_id).first()

    @staticmethod
    async def get_by_discord_ids(discord_ids: Iterable[int]) -> dict[int, User]:
        """Resolve many Discord ids in one query, keyed by Discord id."""
        wanted = list(set(discord_ids))
        if not wanted:
            return {}
        rows = await User.filter(discord_id__in=wanted)
        return {u.discord_id: u for u in rows if u.discord_id is not None}

    @staticmethod
    async def bulk_create_from_discord(members: Iterable[Tuple[int, str, Optional[str]]]) -> None:
        """New accounts for ``(discord_id, username, avatar)`` rows, in one insert.

        Ignores conflicts, so an account a concurrent login made in between is
        kept as it is rather than failing the batch; re-read with
        :meth:`get_by_discord_ids` to learn what exists.
        """
        rows = [
            User(discord_id=discord_id, username=username, discord_avatar=avatar or None)
            for discord_id, username, avatar in members
        ]
        if rows:
            await User.bulk_create(rows, ignore_conflicts=True)

    @staticmethod
    async def get_all(
        role: Optional[Role] = None,
        has_discord: bool = False,
    ) -> List[User]:
        """
        Get all users with optional filters.

        Args:
            role: Filter to users holding this role (via the userrole table).
            has_discord: Only return users with Discord IDs.
        """
        query = User.all().order_by('username')

        if role is not None:
            query = query.filter(roles__role=role).distinct()

        if has_discord:
            query = query.exclude(discord_id=None)

        return await query

    @staticmethod
    async def get_community_people(
        *,
        role: Optional[Role] = None,
        has_discord: bool = False,
        include_user_ids: Optional[List[int]] = None,
        include_inactive: bool = False,
    ) -> List[User]:
        """The people of the tenant in scope, for a per-community person picker.

        ``User`` is global, so "belongs to this community" has to be derived —
        and ``TenantMembership`` is what derives it. That was not always true:
        this query used to union role-holders with tournament entrants
        precisely *because* membership was a frozen backfill nothing wrote to,
        so scoping to it would have hidden a user a Discord login provisioned
        five minutes ago. The membership work closed that: holding any role in
        a tenant now implies membership in it, staff can add a member directly,
        an approved join request creates one, and an imported SpeedGaming player
        gets one. The old union is a strict subset of the new basis, and the
        basis is now the same thing the access gate uses — so a picker cannot
        offer someone the app would turn away at the door.

        ``include_user_ids`` force-includes specific people regardless — the
        caller's own actor, or the current holder of an asset, who must stay
        displayable even if they later fall out of the set.

        The system account is always excluded — by **both** its flag and its
        sentinel discord id, because either alone identifies it and a row
        carrying one without the other is still the automation account. It is an
        audit actor, not a person, and the multitenancy backfill made it a member
        of everywhere.
        Deactivated accounts are excluded too, unless ``include_inactive`` —
        which the match dialog passes, because a SpeedGaming placeholder entrant
        is inactive by construction and must stay selectable on the roster it is
        already on.

        This is a **default for a picker, not an authorization rule.** The hard
        rules are enforced by whichever service acts on the choice.

        ``current_tenant_id()`` raising with no tenant in scope is wanted: a
        per-community picker rendered outside a tenant is a bug, and this is
        where it should surface loudly.
        """
        return await UserRepository._community_people_query(
            role=role, has_discord=has_discord,
            include_user_ids=include_user_ids, include_inactive=include_inactive,
        ).order_by('username').distinct()

    @staticmethod
    async def get_community_people_summaries(
        *,
        include_user_ids: Optional[List[int]] = None,
        include_inactive: bool = False,
    ) -> List[PersonSummary]:
        """:meth:`get_community_people`, as :class:`PersonSummary` rows.

        For pickers and name searches over the whole community. Hydrating a
        ``User`` sets about forty fields, which for a community of 2,000 held
        the one event loop for most of a second in production; seven scalars a
        row cost a few milliseconds. Fetch the people actually chosen with
        :meth:`get_by_ids`.
        """
        rows = await UserRepository._community_people_query(
            include_user_ids=include_user_ids, include_inactive=include_inactive,
        ).order_by('username').distinct().values_list(*PersonSummary._fields)
        return [PersonSummary(*row) for row in rows]

    @staticmethod
    async def role_summary(
        tenant_id: int, event_id: Optional[int] = None,
    ) -> Tuple[Dict[int, List[Tuple[Role, Optional[str]]]], Counter, Counter]:
        """Each user's roles in ``tenant_id``, and how many of its tournaments
        each one admins and crew-coordinates: three flat reads, no ``User`` rows.

        Counts span every event (cross-event): the Members list is the community's.
        A role held in an event other than ``event_id`` comes with that event's
        name (None otherwise); grants in archived events are left out, since
        they authorise nothing.
        """
        roles: Dict[int, List[Tuple[Role, Optional[str]]]] = {}
        grants = await UserRole.filter(
            Q(event_id=None) | Q(event__status__in=[EventStatus.LIVE, EventStatus.DRAFT]),
            tenant_id=tenant_id,
        ).order_by('id').values_list('user_id', 'role', 'event_id', 'event__name')
        for user_id, role, held_in, event_name in grants:
            roles.setdefault(user_id, []).append(
                (Role(role), event_name if held_in is not None and held_in != event_id else None)
            )
        admins = Counter(await Tournament.filter(tenant_id=tenant_id, admins__id__isnull=False)
                         .values_list('admins__id', flat=True))
        coordinators = Counter(await Tournament.filter(tenant_id=tenant_id, crew_coordinators__id__isnull=False)
                               .values_list('crew_coordinators__id', flat=True))
        return roles, admins, coordinators

    @staticmethod
    def _community_people_query(
        *,
        role: Optional[Role] = None,
        has_discord: bool = False,
        include_user_ids: Optional[List[int]] = None,
        include_inactive: bool = False,
    ):
        tenant_id = current_tenant_id()
        query = User.filter(tenant_memberships__tenant_id=tenant_id)
        if include_user_ids:
            query = User.filter(
                Q(tenant_memberships__tenant_id=tenant_id) | Q(id__in=list(include_user_ids))
            )
        # Spelled positively: exclude(discord_id=SENTINEL) would silently drop NULL-id placeholders too.
        query = query.filter(
            Q(is_system=False),
            Q(discord_id__isnull=True) | Q(discord_id__not=SYSTEM_USER_DISCORD_ID),
        )
        if not include_inactive:
            query = query.filter(is_active=True)
        if role is not None:
            query = query.filter(roles__role=role, roles__tenant_id=tenant_id)
        if has_discord:
            query = query.exclude(discord_id=None)
        return query

    @staticmethod
    async def search_by_name(
        search_term: str,
        limit: int = 20,
    ) -> List[User]:
        return await User.filter(
            Q(username__icontains=search_term)
            | Q(display_name__icontains=search_term)
        ).limit(limit)

    @staticmethod
    async def create(
        username: str,
        discord_id: Optional[int] = None,
        display_name: Optional[str] = None,
        pronouns: Optional[str] = None,
        is_active: bool = True,
    ) -> User:
        return await User.create(
            username=username,
            discord_id=discord_id,
            display_name=display_name,
            pronouns=pronouns,
            is_active=is_active,
        )

    @staticmethod
    async def get_by_login_name(login_name: str) -> Optional[User]:
        return await User.get_or_none(login_name=login_name)

    @staticmethod
    async def create_password_account(
        username: str, display_name: str, login_name: str, password_hash: str, changed_at,
    ) -> User:
        return await User.create(
            username=username,
            display_name=display_name,
            login_name=login_name,
            password_hash=password_hash,
            password_changed_at=changed_at,
        )

    @staticmethod
    async def list_for_platform() -> List[User]:
        """Every real account (no placeholders, no system actor), with roles."""
        return await (
            User.filter(is_placeholder=False, is_system=False)
            .prefetch_related('tenant_memberships__tenant', 'roles')
            .order_by('username')
        )

    @staticmethod
    async def get_or_create_by_discord_id(
        discord_id: int,
        username: str,
        avatar: Optional[str] = None,
    ) -> tuple[User, bool]:
        return await User.get_or_create(
            discord_id=discord_id,
            defaults={'username': username, 'discord_avatar': avatar or None},
        )

    @staticmethod
    async def get_or_create_system_user() -> User:
        """Return the reserved system :class:`User`, creating it if absent.

        Idempotent: keyed on the ``SYSTEM_USER_DISCORD_ID`` sentinel so repeated
        calls (and the migration seed) converge on one global row. The row is
        deliberately inactive for login purposes but is a valid audit actor.
        """
        user, _ = await User.get_or_create(
            discord_id=SYSTEM_USER_DISCORD_ID,
            defaults={
                'username': 'System',
                'is_system': True,
                'is_active': False,
                'dm_notifications': False,
            },
        )
        return user

    @staticmethod
    async def get_by_username(username: str) -> Optional[User]:
        """Exact (case-insensitive) username match — global, for SG ETL resolution.

        SG supplies a ``discordTag`` (the account's handle) but not always a
        discord id; this resolves that handle to an existing real ``User`` before
        falling back to a placeholder. Cross-tenant on purpose (``User`` is
        global). Returns the first match; ``username`` is not unique, so this is a
        best-effort resolution step, not an identity guarantee.
        """
        return await User.filter(username__iexact=username).exclude(is_placeholder=True).first()

    @staticmethod
    async def get_placeholder_by_speedgaming_id(speedgaming_id: str) -> Optional[User]:
        """Find an existing placeholder previously created for this SG player."""
        return await User.get_or_none(speedgaming_id=speedgaming_id)

    @staticmethod
    async def create_placeholder(
        speedgaming_id: str,
        username: str,
        display_name: Optional[str] = None,
    ) -> User:
        """Create a placeholder ``User`` for an unresolved SG player.

        ``discord_id`` is NULL (allowed only because ``is_placeholder`` is True —
        the DB CHECK enforces that pairing). The row is inactive for login but is
        a valid ``MatchPlayers.user``, so an unmatched SG entrant still shows on
        the roster. Upgraded in place by :meth:`upgrade_placeholder` when a
        discord id later appears.
        """
        return await User.create(
            username=username,
            display_name=display_name,
            discord_id=None,
            is_placeholder=True,
            speedgaming_id=speedgaming_id,
            is_active=False,
            dm_notifications=False,
        )

    @staticmethod
    async def upgrade_placeholder(
        user: User,
        discord_id: int,
        username: Optional[str] = None,
    ) -> User:
        """Promote a placeholder to a real ``User`` once its discord id appears."""
        user.discord_id = discord_id
        user.is_placeholder = False
        user.is_active = True
        if username:
            user.username = username
        await save_fields(user, 'discord_id', 'is_placeholder', 'is_active', 'username')
        return user

    @staticmethod
    async def set_timezone(user: User, timezone_name: Optional[str]) -> None:
        """Persist a user's display-timezone preference (``None`` clears it)."""
        user.timezone = timezone_name
        await save_fields(user, 'timezone')

    @staticmethod
    async def set_hour_cycle(user: User, hour_cycle: Optional[str]) -> None:
        """Persist a user's 12/24-hour preference (``None`` clears it)."""
        user.hour_cycle = hour_cycle  # type: ignore[assignment]
        await save_fields(user, 'hour_cycle')

    @staticmethod
    async def update_discord_info(
        user: User, username: str, avatar: Optional[str] = None,
    ) -> None:
        """Refresh the Discord-sourced identity we persist: username and avatar.

        ``avatar`` is Discord's avatar *hash*; ``None`` means the caller has
        nothing to say about it and the stored hash is left alone. Clearing a
        removed avatar goes through :meth:`set_discord_avatar`, which can write
        ``NULL``.
        """
        user.username = username
        if avatar is not None:
            user.discord_avatar = avatar
        await save_fields(user, 'username', 'discord_avatar')

    @staticmethod
    async def set_discord_avatar(user: User, avatar: Optional[str]) -> bool:
        """Store (or clear) a user's Discord avatar hash. ``True`` if it changed.

        Separate from :meth:`update_discord_info` because this one is called on
        every ``on_member_update`` the bot sees, most of which change nothing —
        the no-op short-circuit is what keeps that from being a write per event.
        """
        if user.discord_avatar == avatar:
            return False
        user.discord_avatar = avatar  # type: ignore[assignment]
        await user.save(update_fields=['discord_avatar', 'updated_at'])
        return True
