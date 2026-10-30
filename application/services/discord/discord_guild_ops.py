"""Reading and writing a tenant guild's roles and membership.

Split out of ``discord_service`` the way ``discord_member_events`` was, and
composed the way :class:`StationAssignmentMixin` is composed into
``MatchService``: :class:`GuildOpsMixin` reaches ``self._bot`` through the class
it is mixed into. Every method is a thin, fail-closed wrapper that returns
``(ok, data_or_message)`` rather than raising — the admin tabs and the role-sync
service render the message straight to the user.

The mock twin lives here too rather than in ``discord_service``. Its whole
contract is mirroring this surface method for method, and a mirror kept in
another file drifts: a method added here and forgotten there fails only under
MOCK_DISCORD, which is the mode nobody runs the tests in.
"""

import logging
from dataclasses import dataclass
from typing import AbstractSet, Dict, FrozenSet, List, Optional, Set, Tuple, Union

import discord
from discord.ext import commands

from application.utils.mocks import mock_discord_data

logger = logging.getLogger(__name__)


def log_discord_failure(what: str, exc: Exception) -> None:
    """Log a guild/event operation that fell through to its catch-all.

    These methods report failure as a ``(False, message)`` tuple, so the
    traceback dies here unless it is logged here. Discord refusing (missing
    permission, rate limit, 5xx) is a warning; anything else is our bug.
    """
    if isinstance(exc, discord.DiscordException):
        logger.warning('%s: %s', what, exc)
    else:
        logger.exception('%s', what, exc_info=exc)


@dataclass(frozen=True)
class GuildMember:
    """The slice of a guild member that role sync needs to provision an account.

    ``avatar`` is the *global* avatar hash (what the OAuth login stores), ``None``
    when the account has no custom avatar. ``role_ids`` excludes ``@everyone``.
    """

    id: int
    username: str
    avatar: Optional[str]
    role_ids: FrozenSet[int]
    bot: bool = False

    @classmethod
    def from_member(cls, member: discord.Member) -> 'GuildMember':
        return cls(
            id=member.id,
            username=member.name,
            avatar=member.avatar.key if member.avatar else None,
            role_ids=frozenset(r.id for r in member.roles if r.id != member.guild.id),
            bot=member.bot,
        )


class GuildOpsMixin:
    """Guild, role and member operations against the live bot."""

    #: Set by the composed class's ``__init__``; declared so the mixin's own
    #: reads type-check without the composed class being visible from here.
    _bot: Optional[commands.Bot]

    async def list_guilds(self) -> Tuple[bool, Union[List[Dict[str, Union[int, str]]], str]]:
        """
        Retrieve the list of guilds (servers) the bot is currently connected to.

        Returns:
            Tuple[success, data]
            - On success: (True, [{"id": int, "name": str, "color": int,
              "mappable": bool}, ...]) — ``color`` is Discord's RGB int (0 is
              "no colour"); ``mappable`` is False for @everyone and roles a bot
              or integration owns, which no person is ever handed.
            - On failure: (False, error_message)
        """
        try:
            if self._bot is None:
                return False, "Discord bot not initialized"

            if not self._bot.is_ready():
                return False, "Discord bot is not connected. Please try again in a moment."

            guilds = self._bot.guilds
            data = [{"id": g.id, "name": g.name} for g in guilds]
            return True, data
        except Exception as e:
            log_discord_failure("Failed to retrieve guilds", e)
            return False, f"Failed to retrieve guilds: {e!s}"

    async def list_guild_roles(self, guild_id: int) -> Tuple[bool, Union[List[Dict[str, Union[int, str]]], str]]:
        """
        Retrieve all roles for a given guild.

        Args:
            guild_id: The Discord guild ID (snowflake)

        Returns:
            Tuple[success, data]
            - On success: (True, [{"id": int, "name": str, "color": int,
              "mappable": bool, "position": int}, ...]) — ``position`` is
              Discord's role order, higher outranking lower.
            - On failure: (False, error_message)
        """
        try:
            if self._bot is None:
                return False, "Discord bot not initialized"

            if not self._bot.is_ready():
                return False, "Discord bot is not connected. Please try again in a moment."

            guild = self._bot.get_guild(guild_id)
            if guild is None:
                try:
                    guild = await self._bot.fetch_guild(guild_id)
                except discord.NotFound:
                    return False, "Guild not found"
                except discord.Forbidden:
                    return False, "Insufficient permissions to access this guild"

            roles_list: List[discord.Role]
            try:
                # Prefer explicit fetch to ensure complete/updated role list
                roles_list = await guild.fetch_roles()
            except Exception:
                roles_list = list(getattr(guild, "roles", []))

            data = [
                {
                    "id": r.id,
                    "name": r.name,
                    "color": r.color.value,
                    "mappable": not (r.is_default() or r.is_bot_managed() or r.is_integration()),
                    "position": r.position,
                }
                for r in roles_list
            ]
            return True, data
        except discord.HTTPException as e:
            return False, f"Discord HTTP error while retrieving roles: {e!s}"
        except Exception as e:
            log_discord_failure("Failed to retrieve roles", e)
            return False, f"Failed to retrieve roles: {e!s}"

    async def _modify_role(
        self, guild_id: int, user_id: int, role_id: int, reason: Optional[str], *, add: bool,
    ) -> Tuple[bool, str]:
        """Add or remove a guild role for a member, depending on ``add``."""
        gerund = "adding" if add else "removing"
        past = "added to" if add else "removed from"
        verb = "add" if add else "remove"
        try:
            if self._bot is None:
                return False, "Discord bot not initialized"
            if not self._bot.is_ready():
                return False, "Discord bot is not connected. Please try again in a moment."

            guild = self._bot.get_guild(guild_id) or await self._bot.fetch_guild(guild_id)
            if guild is None:
                return False, "Guild not found"

            member = guild.get_member(user_id)
            if member is None:
                try:
                    member = await guild.fetch_member(user_id)
                except discord.NotFound:
                    return False, "Member not found in guild"

            role = guild.get_role(role_id)
            if role is None:
                try:
                    roles_list = await guild.fetch_roles()
                    role = next((r for r in roles_list if r.id == role_id), None)
                except Exception:
                    role = None

            if role is None:
                return False, "Role not found in guild"

            if add:
                await member.add_roles(role, reason=reason)
            else:
                await member.remove_roles(role, reason=reason)
            return True, f"Role {past} user"
        except discord.Forbidden:
            return False, "Bot lacks permissions or role hierarchy prevents this action"
        except discord.HTTPException as e:
            return False, f"Discord HTTP error while {gerund} role: {e!s}"
        except Exception as e:
            log_discord_failure(f"Failed to {verb} role", e)
            return False, f"Failed to {verb} role: {e!s}"

    async def add_role_to_user(self, guild_id: int, user_id: int, role_id: int, reason: Optional[str] = None) -> Tuple[bool, str]:
        """
        Add a role to a user in a given guild.

        Args:
            guild_id: Target guild ID
            user_id: Target user ID (member)
            role_id: Role ID to add
            reason: Optional audit log reason

        Returns:
            (success, message)
        """
        return await self._modify_role(guild_id, user_id, role_id, reason, add=True)

    async def remove_role_from_user(self, guild_id: int, user_id: int, role_id: int, reason: Optional[str] = None) -> Tuple[bool, str]:
        """
        Remove a role from a user in a given guild.

        Args:
            guild_id: Target guild ID
            user_id: Target user ID (member)
            role_id: Role ID to remove
            reason: Optional audit log reason

        Returns:
            (success, message)
        """
        return await self._modify_role(guild_id, user_id, role_id, reason, add=False)

    async def bot_role_access(self, guild_id: int) -> Tuple[bool, Union[Dict[str, Union[bool, int]], str]]:
        """What the bot itself may do with roles in ``guild_id``.

        ``(True, {"manage_roles": bool, "top_position": int})``: whether it holds
        Manage Roles (Administrator implies it), and the position of its highest
        role. Discord lets a bot hand out only roles strictly below that.
        """
        try:
            if self._bot is None:
                return False, "Discord bot not initialized"
            if not self._bot.is_ready():
                return False, "Discord bot is not connected. Please try again in a moment."
            guild = self._bot.get_guild(guild_id)
            if guild is None:
                return False, "The bot is not in this server."
            me = guild.me
            if me is None:
                me = await guild.fetch_member(self._bot.user.id)
            perms = me.guild_permissions
            return True, {
                "manage_roles": bool(perms.administrator or perms.manage_roles),
                "top_position": me.top_role.position,
            }
        except discord.Forbidden:
            return False, "The bot cannot access this server."
        except discord.HTTPException as e:
            return False, f"Discord HTTP error while reading the bot's roles: {e!s}"
        except Exception as e:
            log_discord_failure("Failed to read the bot's roles", e)
            return False, f"Failed to read the bot's roles: {e!s}"

    async def member_role_snapshot(
        self, guild_id: int, user_ids: AbstractSet[int],
    ) -> Tuple[bool, Union[Dict[int, FrozenSet[int]], str]]:
        """The roles each of ``user_ids`` holds in ``guild_id``; absent ids aren't members.

        One pass over the member cache when the guild is chunked (the bot asks
        for the members intent), else one paged walk of the REST member list,
        rather than a fetch per person.
        """
        try:
            if self._bot is None:
                return False, "Discord bot not initialized"
            if not self._bot.is_ready():
                return False, "Discord bot is not connected. Please try again in a moment."
            if not user_ids:
                return True, {}
            guild = self._bot.get_guild(guild_id)
            if guild is None:
                return False, "The bot is not in this server."
            if guild.chunked:
                members = [m for m in (guild.get_member(uid) for uid in user_ids) if m is not None]
            else:
                members = [m async for m in guild.fetch_members(limit=None) if m.id in user_ids]
            return True, {
                m.id: frozenset(r.id for r in m.roles if r.id != guild_id) for m in members
            }
        except discord.Forbidden:
            return False, "Bot lacks permissions to read guild members"
        except discord.HTTPException as e:
            return False, f"Discord HTTP error while reading members: {e!s}"
        except Exception as e:
            log_discord_failure("Failed to read members", e)
            return False, f"Failed to read members: {e!s}"

    async def get_member_role_ids(self, guild_id: int, user_id: int) -> Tuple[bool, Union[Set[int], str]]:
        """
        Retrieve the set of Discord role IDs a member currently holds in a guild.

        Returns:
            Tuple[success, data]
            - On success: (True, {role_id, ...}); the ``@everyone`` role is excluded.
            - When the user is not a member of the guild: (True, set())
            - On a hard failure (bot not ready, API error): (False, error_message)
        """
        try:
            if self._bot is None:
                return False, "Discord bot not initialized"
            if not self._bot.is_ready():
                return False, "Discord bot is not connected. Please try again in a moment."

            guild = self._bot.get_guild(guild_id) or await self._bot.fetch_guild(guild_id)
            if guild is None:
                return False, "Guild not found"

            member = guild.get_member(user_id)
            if member is None:
                try:
                    member = await guild.fetch_member(user_id)
                except discord.NotFound:
                    return True, set()

            # Exclude @everyone, whose role id equals the guild id.
            return True, {r.id for r in member.roles if r.id != guild_id}
        except discord.Forbidden:
            return False, "Bot lacks permissions to read guild members"
        except discord.HTTPException as e:
            return False, f"Discord HTTP error while reading member roles: {e!s}"
        except Exception as e:
            log_discord_failure("Failed to read member roles", e)
            return False, f"Failed to read member roles: {e!s}"

    async def list_members_with_roles(
        self, guild_id: int, role_ids: AbstractSet[int],
    ) -> Tuple[bool, Union[List[GuildMember], str]]:
        """Every non-bot member of ``guild_id`` holding at least one of ``role_ids``."""
        if not role_ids:
            return True, []
        ok, payload = await self.list_members(guild_id)
        if not ok or isinstance(payload, str):
            return ok, payload
        return True, [m for m in payload if not m.bot and m.role_ids & role_ids]

    async def list_members(self, guild_id: int) -> Tuple[bool, Union[List[GuildMember], str]]:
        """Every member of ``guild_id``, bots included (``GuildMember.bot``).

        Reads the gateway member cache when the guild is fully chunked and pages
        through the REST member list otherwise; both need the members intent,
        which the bot requests.
        """
        try:
            if self._bot is None:
                return False, "Discord bot not initialized"
            if not self._bot.is_ready():
                return False, "Discord bot is not connected. Please try again in a moment."

            guild = self._bot.get_guild(guild_id)
            if guild is None:
                try:
                    guild = await self._bot.fetch_guild(guild_id)
                except discord.NotFound:
                    return False, "The bot is not in this server."
                except discord.Forbidden:
                    return False, "Insufficient permissions to access this guild"

            if guild.chunked:
                members = list(guild.members)
            else:
                members = [m async for m in guild.fetch_members(limit=None)]
            return True, [GuildMember.from_member(m) for m in members]
        except discord.Forbidden:
            return False, "Bot lacks permissions to read guild members"
        except discord.HTTPException as e:
            return False, f"Discord HTTP error while listing members: {e!s}"
        except Exception as e:
            log_discord_failure("Failed to list members", e)
            return False, f"Failed to list members: {e!s}"

    async def get_guild_summary(self, guild_id: int) -> Tuple[bool, Union[Dict[str, Union[int, str]], str]]:
        """Return ``{"id", "name"}`` for a guild the bot can see, else an error.

        Used to render the connected server's name and to confirm the bot is
        actually in the guild after a link.
        """
        try:
            if self._bot is None:
                return False, "Discord bot not initialized"
            if not self._bot.is_ready():
                return False, "Discord bot is not connected. Please try again in a moment."
            guild = self._bot.get_guild(guild_id)
            if guild is None:
                try:
                    guild = await self._bot.fetch_guild(guild_id)
                except discord.NotFound:
                    return False, "The bot is not in this server."
                except discord.Forbidden:
                    return False, "Insufficient permissions to access this guild"
            return True, {"id": guild.id, "name": guild.name}
        except discord.HTTPException as e:
            return False, f"Discord HTTP error while reading guild: {e!s}"
        except Exception as e:
            log_discord_failure("Failed to read guild", e)
            return False, f"Failed to read guild: {e!s}"

    async def member_can_manage_guild(self, guild_id: int, user_id: int) -> Tuple[bool, Union[bool, str]]:
        """Whether ``user_id`` may administer ``guild_id`` (owner / Administrator / Manage Server).

        This is the authorization proof for linking a tenant to a Discord server:
        only a member who could add the bot in the first place passes. Requires
        the bot to be in the guild (it is, right after the bot-auth flow) and the
        members intent (enabled). Returns ``(True, bool)`` on a definitive answer,
        ``(False, error)`` when the bot cannot determine it (not ready, not in the
        guild, API error) so callers fail closed rather than treating an error as
        authorized.
        """
        try:
            if self._bot is None:
                return False, "Discord bot not initialized"
            if not self._bot.is_ready():
                return False, "Discord bot is not connected. Please try again in a moment."
            guild = self._bot.get_guild(guild_id)
            if guild is None:
                try:
                    guild = await self._bot.fetch_guild(guild_id)
                except discord.NotFound:
                    return False, "The bot is not in this server."
                except discord.Forbidden:
                    return False, "The bot cannot access this server."
            if user_id == guild.owner_id:
                return True, True
            member = guild.get_member(user_id)
            if member is None:
                try:
                    member = await guild.fetch_member(user_id)
                except discord.NotFound:
                    # Not a member of the guild → cannot administer it.
                    return True, False
            perms = member.guild_permissions
            return True, bool(perms.administrator or perms.manage_guild)
        except discord.Forbidden:
            return False, "Bot lacks permissions to read guild members"
        except discord.HTTPException as e:
            return False, f"Discord HTTP error while checking permissions: {e!s}"
        except Exception as e:
            log_discord_failure("Failed to check permissions", e)
            return False, f"Failed to check permissions: {e!s}"

    async def is_guild_member(self, guild_id: int, user_id: int) -> Tuple[bool, Union[bool, str]]:
        """Whether ``user_id`` is currently in ``guild_id``.

        ``(True, bool)`` on a definitive answer, ``(False, error)`` when the bot
        cannot tell (not ready, not in the guild, API error). The membership
        door's Discord auto-join reads this, and must treat an error as "not
        known to be a member" rather than as either answer.
        """
        try:
            if self._bot is None:
                return False, "Discord bot not initialized"
            if not self._bot.is_ready():
                return False, "Discord bot is not connected. Please try again in a moment."
            guild = self._bot.get_guild(guild_id)
            if guild is None:
                try:
                    guild = await self._bot.fetch_guild(guild_id)
                except discord.NotFound:
                    return False, "The bot is not in this server."
                except discord.Forbidden:
                    return False, "The bot cannot access this server."
            if guild.get_member(user_id) is not None:
                return True, True
            try:
                await guild.fetch_member(user_id)
            except discord.NotFound:
                return True, False
            return True, True
        except discord.Forbidden:
            return False, "Bot lacks permissions to read guild members"
        except discord.HTTPException as e:
            return False, f"Discord HTTP error while reading member: {e!s}"
        except Exception as e:
            log_discord_failure("Failed to read member", e)
            return False, f"Failed to read member: {e!s}"


class MockGuildOpsMixin:
    """Canned answers for the same surface, served from ``mock_discord_data``."""

    async def list_guilds(self) -> Tuple[bool, Union[List[Dict[str, Union[int, str]]], str]]:
        return True, mock_discord_data.all_guilds()

    async def list_guild_roles(self, guild_id: int) -> Tuple[bool, Union[List[Dict[str, Union[int, str]]], str]]:
        return True, mock_discord_data.roles_for(guild_id)

    async def add_role_to_user(self, guild_id: int, user_id: int, role_id: int, reason: Optional[str] = None) -> Tuple[bool, str]:
        print(f"[MOCK Discord] add_role guild={guild_id} user={user_id} role={role_id} reason={reason!r}")
        mock_discord_data.give_role(guild_id, user_id, role_id, add=True)
        return True, "Role added (mock)"

    async def remove_role_from_user(self, guild_id: int, user_id: int, role_id: int, reason: Optional[str] = None) -> Tuple[bool, str]:
        print(f"[MOCK Discord] remove_role guild={guild_id} user={user_id} role={role_id} reason={reason!r}")
        mock_discord_data.give_role(guild_id, user_id, role_id, add=False)
        return True, "Role removed (mock)"

    async def bot_role_access(self, guild_id: int) -> Tuple[bool, Union[Dict[str, Union[bool, int]], str]]:
        return True, mock_discord_data.bot_access(guild_id)

    async def member_role_snapshot(
        self, guild_id: int, user_ids: AbstractSet[int],
    ) -> Tuple[bool, Union[Dict[int, FrozenSet[int]], str]]:
        return True, {
            uid: frozenset(mock_discord_data.member_role_ids(guild_id, uid))
            for uid in user_ids if mock_discord_data.is_guild_member(guild_id, uid)
        }

    async def get_member_role_ids(self, guild_id: int, user_id: int) -> Tuple[bool, Union[Set[int], str]]:
        print(f"[MOCK Discord] get_member_role_ids guild={guild_id} user={user_id}")
        return True, mock_discord_data.member_role_ids(guild_id, user_id)

    async def list_members_with_roles(
        self, guild_id: int, role_ids: AbstractSet[int],
    ) -> Tuple[bool, Union[List[GuildMember], str]]:
        print(f"[MOCK Discord] list_members_with_roles guild={guild_id} roles={sorted(role_ids)}")
        found = []
        for member_id, username in mock_discord_data.MOCK_GUILD_ROSTER.items():
            held = frozenset(mock_discord_data.member_role_ids(guild_id, member_id))
            if held & role_ids:
                found.append(GuildMember(id=member_id, username=username, avatar=None, role_ids=held))
        return True, found

    async def list_members(self, guild_id: int) -> Tuple[bool, Union[List[GuildMember], str]]:
        print(f"[MOCK Discord] list_members guild={guild_id}")
        return True, [
            GuildMember(
                id=member_id, username=username, avatar=None,
                role_ids=frozenset(mock_discord_data.member_role_ids(guild_id, member_id)),
                bot=member_id in mock_discord_data.MOCK_GUILD_BOTS,
            )
            for member_id, username in {
                **mock_discord_data.MOCK_GUILD_ROSTER,
                **mock_discord_data.MOCK_GUILD_IMPORTABLE,
            }.items()
        ]

    async def get_guild_summary(self, guild_id: int) -> Tuple[bool, Union[Dict[str, Union[int, str]], str]]:
        print(f"[MOCK Discord] get_guild_summary guild={guild_id}")
        return True, {"id": guild_id, "name": mock_discord_data.guild(guild_id)["name"]}

    async def member_can_manage_guild(self, guild_id: int, user_id: int) -> Tuple[bool, Union[bool, str]]:
        print(f"[MOCK Discord] member_can_manage_guild guild={guild_id} user={user_id}")
        return True, mock_discord_data.user_can_manage(guild_id, user_id)

    async def is_guild_member(self, guild_id: int, user_id: int) -> Tuple[bool, Union[bool, str]]:
        print(f"[MOCK Discord] is_guild_member guild={guild_id} user={user_id}")
        return True, mock_discord_data.is_guild_member(guild_id, user_id)
