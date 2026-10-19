"""
Discord Service - Business Logic Layer

Handles Discord-related operations like sending DMs.
"""

import asyncio
import logging
import os
from typing import Awaitable, Callable, Dict, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from application.events import dispatch_queue as event_dispatch_queue
from application.services.discord.discord_guild_ops import (
    GuildMember,
    GuildOpsMixin,
    MockGuildOpsMixin,
)
from application.services.discord.discord_member_events import (
    sync_member_avatar,
    sync_member_roles,
)
from application.services.discord.discord_scheduled_events import (
    MockScheduledEventsMixin,
    ScheduledEventsMixin,
)
from application.services.notification_catalog import NoticeTarget
from application.services.web_push_service import WebPushService
from application.utils.discord_messages import DMLink

logger = logging.getLogger(__name__)

# send_dm's two "Discord said no" outcomes, as opposed to a failure on our side.
# Callers that report reachability (the check-in test DM) compare against these.
DM_USER_NOT_FOUND = "User not found"
DM_NO_DISCORD = 'This person has no Discord account, so no DM was sent.'
DM_REFUSED = "Cannot send DM to this user (DMs may be disabled)"
DM_UNREACHABLE = frozenset({DM_USER_NOT_FOUND, DM_REFUSED})
# Not a failure: the recipient turned Discord DMs off on their profile. Any
# device notifications still went out through the mirror.
DM_DISCORD_OFF = "Discord DMs are turned off for this user"
# The community turned this message off for this kind of recipient (Admin → Notifications).
DM_NOTICE_OFF = "This notification is turned off in the community's notification settings"
DM_SEND_TIMEOUT_SECONDS = 45

# Called with ``(channel_id, message_id)`` once Discord has accepted a DM, so a
# sender can find the message again to edit it.
DMSentHook = Callable[[int, int], Awaitable[None]]


async def discord_dms_enabled(discord_id: int) -> bool:
    """Whether the user behind ``discord_id`` still wants the Discord copy.

    ``User.dm_notifications`` is the *Discord* channel switch only: device
    notifications are governed by whether the device is subscribed. Enforcing it
    here, rather than at each sender, keeps the two channels independent: a
    sender that skipped opted-out users would silence their devices too. An
    unknown id gets the DM, as it always has.
    """
    from application.repositories import UserRepository

    user = await UserRepository.get_by_discord_id(str(discord_id))
    return user is None or user.dm_notifications


async def _apply_notice(notice: NoticeTarget) -> Tuple[bool, bool]:
    """``(muted, silent)`` for ``notice`` under the community's settings."""
    from application.services.notification_settings_service import NotificationSettingsService
    from models import NotificationMode

    mode = await NotificationSettingsService.mode_for(notice)
    return mode is NotificationMode.OFF, mode is NotificationMode.SILENT


def _mirror_dm_to_web_push(
    user_id: int, message: str, navigate: Optional[str] = None, silent: bool = False,
) -> None:
    """Fan every outgoing DM out to the recipient's web-push devices.

    send_dm is the chokepoint all notification paths flow through, so mirroring
    here gives device notifications the exact coverage DMs have. The mirror is
    enqueued fire-and-forget onto the event dispatch worker — the caller may be
    the serial discord_queue worker or a UI click handler awaiting send_dm
    inline, and neither may ever wait on push-service round-trips. No coroutine
    is created at all while VAPID is unconfigured.

    ``navigate`` is the DM's :class:`~application.utils.discord_messages.DMLink`
    target, so tapping the phone notification lands on the same control the
    Discord button does.
    """
    if not WebPushService.is_configured():
        return
    event_dispatch_queue.enqueue(
        WebPushService().mirror_dm(user_id, message, navigate=navigate, silent=silent)
    )


def _mirror_dm_to_channels(
    user_id: int, message: str, link: Optional[DMLink] = None, silent: bool = False,
) -> None:
    """Fan every outgoing DM out to the recipient's email and Telegram too.

    The same chokepoint as :func:`_mirror_dm_to_web_push`. The community the DM
    is sent from decides whether its ``notification_channels`` flag is on, so
    its id is captured here: the dispatch worker runs outside any request.
    """
    from application.services.notification_channel_service import (
        NotificationChannelService,
        email_available,
        telegram_available,
    )
    from application.tenant_context import get_current_tenant_id, tenant_scope

    if not (email_available() or telegram_available()):
        return
    tenant_id = get_current_tenant_id()
    if tenant_id is None:
        return

    async def _run() -> None:
        with tenant_scope(tenant_id):
            await NotificationChannelService().mirror_dm(
                user_id, message,
                navigate=link.url if link else None,
                label=link.label if link else None,
                silent=silent,
            )

    event_dispatch_queue.enqueue(_run())


def _with_link_button(
    view: Optional[discord.ui.View], link: Optional[DMLink],
) -> Optional[discord.ui.View]:
    """Append ``link`` to ``view`` as a Discord link button, creating one if needed.

    A link button needs no interaction handler and no ``custom_id``, so it
    composes with the acknowledgment / crew / unwatch views rather than replacing
    them: a player gets **Acknowledge** and **View match** side by side. A blank
    URL yields no button — Discord rejects one — so a caller whose tenant could
    not be resolved degrades to the DM it would have sent anyway.
    """
    if link is None or not link.url:
        return view
    view = view if view is not None else discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label=link.label, style=discord.ButtonStyle.link, url=link.url,
    ))
    return view


async def _run_sent_hook(hook: Optional[DMSentHook], channel_id: int, message_id: int) -> None:
    if hook is None:
        return
    try:
        await hook(channel_id, message_id)
    except Exception:
        logger.exception('Discord DM %s: sent hook failed', message_id)


# Shared bot instance (singleton pattern)
_bot_instance: Optional[commands.Bot] = None


# ---------------------------------------------------------------------------
# Interaction-handler and DM view-factory registries
#
# These invert what used to be a bidirectional import cycle between this module
# and the Discord bot package (previously held together by ~20 deferred,
# function-body imports). The bot package registers each interaction handler
# and view factory here at startup, so the dependency now runs one way (the bot
# package -> ``application.services``) and this module never imports it back.
# Mirrors ``application/events/match_live.py``.
# ---------------------------------------------------------------------------

# Stable view-factory kinds, shared with the bot package's registration so the
# DM senders below and the registering code agree on the lookup keys.
VIEW_CREW_SIGNUP = 'crew_signup'
VIEW_MATCH_ACK = 'match_ack'
VIEW_CREW_ACK = 'crew_ack'
VIEW_VOLUNTEER_ACK = 'volunteer_ack'
VIEW_VOLUNTEER_ARRIVAL = 'volunteer_arrival'
VIEW_UNWATCH = 'match_watch'
VIEW_RESCHEDULE_AGREE = 'reschedule_agree'
VIEW_HARD_PRESET = 'match_hard'

InteractionHandler = Callable[[discord.Interaction], Awaitable[None]]
ViewFactory = Callable[..., discord.ui.View]

AppCommand = app_commands.Command | app_commands.Group

_interaction_handlers: Dict[str, InteractionHandler] = {}
_view_factories: Dict[str, ViewFactory] = {}
_app_commands: list[AppCommand] = []


def register_interaction_handler(prefix: str, handler: InteractionHandler) -> None:
    """Register a component-interaction handler keyed by its custom_id prefix.

    ``on_interaction`` dispatches an incoming component interaction to the
    handler whose ``prefix`` matches the text before the first ``:`` in the
    button's ``custom_id``.
    """
    _interaction_handlers[prefix] = handler


def register_view_factory(kind: str, factory: ViewFactory) -> None:
    """Register a Discord ``View`` factory keyed by ``kind`` (a ``VIEW_*`` const).

    The DM senders look the factory up by ``kind`` instead of importing it from
    the bot package directly.
    """
    _view_factories[kind] = factory


def register_app_command(command: AppCommand) -> None:
    """Register a slash command (or command group) for the bot's command tree.

    Added to the tree and synced to Discord once, when the bot logs in.
    """
    _app_commands.append(command)


async def _install_app_commands(bot: commands.Bot) -> None:
    for command in _app_commands:
        bot.tree.add_command(command, override=True)
    try:
        synced = await bot.tree.sync()
        logger.info('Synced %d Discord application commands', len(synced))
    except discord.HTTPException:
        # Commands already registered with Discord keep working; only a change
        # to them waits for the next successful sync.
        logger.exception('Failed to sync Discord application commands')


def get_discord_bot() -> commands.Bot:
    """
    Get or create the shared Discord bot instance.
    
    Returns:
        The Discord bot instance
    """
    global _bot_instance
    if _bot_instance is None:
        # Intents required for DM, guild/role visibility
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.dm_messages = True
        
        # max_ratelimit_timeout: a 429 otherwise sleeps as long as Discord asks,
        # holding up every DM queued behind it.
        _bot_instance = commands.Bot(
            command_prefix='!', intents=intents, max_ratelimit_timeout=30,
        )

        async def setup_hook() -> None:
            await _install_app_commands(_bot_instance)

        _bot_instance.setup_hook = setup_hook  # type: ignore[method-assign]

        @_bot_instance.event
        async def on_ready() -> None:
            logger.info('Discord bot ready. Logged in as %s', _bot_instance.user)

        @_bot_instance.event
        async def on_interaction(interaction: discord.Interaction) -> None:
            # A modal's submit carries the modal's own custom_id, so the same
            # prefix registry routes it.
            if interaction.type in (
                discord.InteractionType.component, discord.InteractionType.modal_submit,
            ):
                custom_id = (interaction.data or {}).get('custom_id', '')
                prefix = custom_id.split(':', 1)[0]
                handler = _interaction_handlers.get(prefix)
                if handler is not None:
                    await handler(interaction)

        @_bot_instance.event
        async def on_member_update(before: discord.Member, after: discord.Member) -> None:
            # The event fires for nick/avatar/timeout/role changes alike, so each
            # sync decides for itself whether anything it cares about moved.
            if before.avatar != after.avatar:
                await sync_member_avatar(after)
            if {r.id for r in before.roles} != {r.id for r in after.roles}:
                # No snapshot for a bot, so a bot given a mapped role is
                # never provisioned an account.
                await sync_member_roles(
                    after.guild.id, after.id,
                    None if after.bot else GuildMember.from_member(after),
                )

        @_bot_instance.event
        async def on_member_remove(member: discord.Member) -> None:
            # Left/kicked/banned: re-sync strips their Discord-sourced roles.
            await sync_member_roles(member.guild.id, member.id)

    return _bot_instance


class DiscordService(GuildOpsMixin, ScheduledEventsMixin):
    """Service for Discord-related operations.

    This module owns the DM surface — the one path every notification flows
    through. Guild/role reads and writes and the Scheduled Events wrappers are
    mixed in from their own modules; ``MockDiscordService`` composes the mock
    twin of each, so the two surfaces cannot drift apart.
    """
    
    def __init__(self) -> None:
        self._bot = get_discord_bot()

    async def send_dm(
        self,
        user_id: int,
        message: str,
        view_factory: Optional[Callable[[], discord.ui.View]] = None,
        embed: Optional[discord.Embed] = None,
        link: Optional[DMLink] = None,
        on_sent: Optional[DMSentHook] = None,
        mirror: bool = True,
        silent: bool = False,
        notice: Optional[NoticeTarget] = None,
    ) -> Tuple[bool, str]:
        """
        Send a direct message to a Discord user, optionally with attached buttons.

        Args:
            user_id: Discord user ID
            message: Plain-text message. Always mirrored to the recipient's
                web-push devices, and sent as the Discord content **unless** an
                ``embed`` is given (then the embed is the Discord representation
                and ``message`` serves only as the web-push/fallback text).
            view_factory: Optional zero-arg callable returning the
                ``discord.ui.View`` (buttons) to attach. Called just before the
                message is sent, so it only runs once the bot is ready and the
                user has been fetched.
            embed: Optional rich embed. When present it is sent instead of the
                plain content (Discord would otherwise show both).
            link: Optional :class:`DMLink` rendered as a link button beside any
                buttons ``view_factory`` supplied, and used as the web-push
                mirror's tap target. Every DM that asks for an action should
                carry one — a markdown link in ``message`` is **not** a
                substitute, because an embed suppresses the content entirely.
            on_sent: Optional :data:`DMSentHook`, awaited only when Discord
                accepted the message. A failure inside it is logged, never
                reported as a failed send.
            mirror: ``False`` sends to Discord alone, skipping the copies to
                devices, email and Telegram (a per-channel test).
            silent: Deliver without a ping or phone buzz (Discord's @silent),
                for news the recipient doesn't need to act on. Device and
                Telegram copies go quietly too; email can't.
            notice: Which catalogued notification this is and to whom. The
                community's setting for it decides ``silent`` (overriding the
                argument), or stops the message on every channel when off.

        Returns:
            Tuple of (success: bool, message: str)
            - If successful: (True, "Message sent successfully.")
            - If failed: (False, error_message)
        """
        if not user_id:
            # A password-only account has no Discord to message.
            return False, DM_NO_DISCORD
        if notice is not None:
            muted, silent = await _apply_notice(notice)
            if muted:
                return True, DM_NOTICE_OFF
        # Mirror before the bot-readiness checks so device notifications still
        # go out when the bot is down or the user blocks Discord DMs. Corollary:
        # a (False, ...) return means the *Discord* send failed — subscribed
        # devices may already have been notified, so don't blindly re-send. The
        # mirror always uses the plain text, even when an embed carries the DM.
        if mirror:
            _mirror_dm_to_web_push(
                user_id, message, navigate=link.url if link else None, silent=silent,
            )
            _mirror_dm_to_channels(user_id, message, link, silent=silent)
        try:
            if self._bot is None:
                logger.error('Discord DM to %s not sent: bot not initialized', user_id)
                return False, "Discord bot not initialized"

            if not self._bot.is_ready():
                # Every DM fails this way while the gateway is down, so this is
                # the line that says Discord notifications have stopped.
                logger.error('Discord DM to %s not sent: bot is not connected', user_id)
                return False, "Discord bot is not connected. Please try again in a moment."

            if not await discord_dms_enabled(user_id):
                return True, DM_DISCORD_OFF

            async def _send():
                user = self._bot.get_user(user_id) or await self._bot.fetch_user(user_id)
                kwargs: dict = {'silent': silent}
                view = _with_link_button(
                    view_factory() if view_factory is not None else None, link,
                )
                if view is not None:
                    kwargs['view'] = view
                if embed is not None:
                    # Embed is the Discord representation; content omitted to avoid
                    # showing the plain text above the card.
                    return await user.send(embed=embed, **kwargs)
                return await user.send(message, **kwargs)

            # Every DM shares one serial queue, and discord.py's HTTP calls have
            # no deadline of their own, so one stalled send delayed every match
            # call behind it.
            sent = await asyncio.wait_for(_send(), DM_SEND_TIMEOUT_SECONDS)
            await _run_sent_hook(on_sent, sent.channel.id, sent.id)
            return True, "Message sent successfully."
        except asyncio.TimeoutError:
            logger.warning('Discord DM to %s timed out after %ss', user_id, DM_SEND_TIMEOUT_SECONDS)
            return False, "Discord didn't answer in time. Please try again in a moment."
        except discord.NotFound:
            return False, DM_USER_NOT_FOUND
        except discord.Forbidden:
            return False, DM_REFUSED
        except discord.HTTPException as e:
            logger.warning('Discord DM to %s failed with HTTP %s: %s', user_id, e.status, e)
            return False, f"Failed to send message: {e!s}"
        except Exception as e:
            # Not Discord refusing the send: a bug on our side (a bad embed, an
            # unregistered view). Callers only see the tuple, so log it here.
            logger.exception('Discord DM to %s crashed', user_id)
            return False, f"Discord bot error: {e!s}"

    async def edit_dm(
        self,
        channel_id: int,
        message_id: int,
        embed: discord.Embed,
        link: Optional[DMLink] = None,
    ) -> Tuple[bool, str]:
        """Replace the card and buttons of a DM the bot sent earlier.

        Discord doesn't notify anyone about an edit, and the web-push copy of
        the original can't be changed: this updates the message in place and
        nothing else. Any old buttons go, and ``link`` is the only one left.
        """
        if self._bot is None or not self._bot.is_ready():
            logger.error('Discord DM %s not edited: bot is not connected', message_id)
            return False, "Discord bot is not connected. Please try again in a moment."
        try:
            channel = self._bot.get_partial_messageable(
                channel_id, type=discord.ChannelType.private,
            )
            await channel.get_partial_message(message_id).edit(
                embed=embed, view=_with_link_button(None, link),
            )
            return True, "Message edited."
        except discord.NotFound:
            return False, "Message not found"
        except discord.HTTPException as e:
            logger.warning('Discord DM %s edit failed with HTTP %s: %s', message_id, e.status, e)
            return False, f"Failed to edit message: {e!s}"

    # The five view-bearing senders below are thin wrappers over send_dm: each
    # resolves its registered view factory by kind (populated by the bot package
    # at startup — see the registries above) and defers to send_dm.
    async def send_dm_with_crew_buttons(self, user_id: int, message: str, match_id: int, embed: Optional[discord.Embed] = None, link: Optional[DMLink] = None, notice: Optional[NoticeTarget] = None) -> Tuple[bool, str]:
        """Send a DM with the commentator/tracker crew signup buttons for a match."""
        return await self.send_dm(user_id, message, lambda: _view_factories[VIEW_CREW_SIGNUP](match_id), embed=embed, link=link, notice=notice)

    async def send_dm_with_acknowledgment_button(self, user_id: int, message: str, match_id: int, embed: Optional[discord.Embed] = None, link: Optional[DMLink] = None, notice: Optional[NoticeTarget] = None) -> Tuple[bool, str]:
        """Send a DM with a match Acknowledge button."""
        return await self.send_dm(user_id, message, lambda: _view_factories[VIEW_MATCH_ACK](match_id), embed=embed, link=link, notice=notice)

    async def send_dm_with_reschedule_agree_button(
        self,
        user_id: int,
        message: str,
        request_id: int,
        embed: Optional[discord.Embed] = None,
        link: Optional[DMLink] = None,
        notice: Optional[NoticeTarget] = None,
    ) -> Tuple[bool, str]:
        """Send a DM with the opponent's Agree button for a reschedule request."""
        return await self.send_dm(
            user_id, message, lambda: _view_factories[VIEW_RESCHEDULE_AGREE](request_id),
            embed=embed, link=link, notice=notice,
        )

    async def send_dm_with_hard_preset_buttons(
        self,
        user_id: int,
        message: str,
        match_id: int,
        opted_in: bool,
        embed: Optional[discord.Embed] = None,
        link: Optional[DMLink] = None,
        notice: Optional[NoticeTarget] = None,
    ) -> Tuple[bool, str]:
        """Send a DM with the harder-settings opt-in (or back-out) button.

        ``opted_in`` picks which single button the DM carries, so the message
        never shows a state the reader is not already in.
        """
        return await self.send_dm(
            user_id, message,
            lambda: _view_factories[VIEW_HARD_PRESET](match_id, opted_in),
            embed=embed, link=link, notice=notice,
        )

    async def send_dm_with_crew_acknowledgment_button(
        self,
        user_id: int,
        message: str,
        crew_type: str,
        crew_id: int,
        embed: Optional[discord.Embed] = None,
        link: Optional[DMLink] = None,
        notice: Optional[NoticeTarget] = None,
    ) -> Tuple[bool, str]:
        """Send a DM with a crew-assignment Acknowledge button.

        ``crew_type`` is 'commentator' or 'tracker'; ``crew_id`` is the crew row id.
        """
        return await self.send_dm(user_id, message, lambda: _view_factories[VIEW_CREW_ACK](crew_type, crew_id), embed=embed, link=link, notice=notice)

    async def send_dm_with_volunteer_acknowledgment_button(
        self,
        user_id: int,
        message: str,
        assignment_id: int,
        embed: Optional[discord.Embed] = None,
        link: Optional[DMLink] = None,
        notice: Optional[NoticeTarget] = None,
    ) -> Tuple[bool, str]:
        """Send a DM with a volunteer shift Acknowledge button."""
        return await self.send_dm(user_id, message, lambda: _view_factories[VIEW_VOLUNTEER_ACK](assignment_id), embed=embed, link=link, notice=notice)

    async def send_dm_with_volunteer_arrival_buttons(
        self,
        user_id: int,
        message: str,
        assignment_id: int,
        embed: Optional[discord.Embed] = None,
        link: Optional[DMLink] = None,
        notice: Optional[NoticeTarget] = None,
    ) -> Tuple[bool, str]:
        """Send the shift-start DM: I'm here / Running late / I can't make it."""
        return await self.send_dm(user_id, message, lambda: _view_factories[VIEW_VOLUNTEER_ARRIVAL](assignment_id), embed=embed, link=link, notice=notice)

    async def send_dm_with_unwatch_button(self, user_id: int, message: str, match_id: int, embed: Optional[discord.Embed] = None, link: Optional[DMLink] = None, notice: Optional[NoticeTarget] = None) -> Tuple[bool, str]:
        """Send a DM with an Unwatch button for match watchers."""
        return await self.send_dm(user_id, message, lambda: _view_factories[VIEW_UNWATCH](match_id), embed=embed, link=link, notice=notice)

    def get_bot(self) -> Optional[commands.Bot]:
        """Get the Discord bot instance."""
        return self._bot


class MockDiscordService(MockGuildOpsMixin, MockScheduledEventsMixin):
    """Stub Discord service for local development without a real bot.

    Mirrors the public surface of DiscordService. Methods log to stdout and
    return success tuples with shapes matching the real implementation, so
    notification code paths can be exercised end-to-end.
    """

    _mock_message_id = 0

    def __init__(self) -> None:
        self._bot = None

    async def send_dm(
        self,
        user_id: int,
        message: str,
        view_factory: Optional[Callable[[], "discord.ui.View"]] = None,
        embed: Optional["discord.Embed"] = None,
        link: Optional[DMLink] = None,
        on_sent: Optional[DMSentHook] = None,
        mirror: bool = True,
        silent: bool = False,
        notice: Optional[NoticeTarget] = None,
    ) -> Tuple[bool, str]:
        if notice is not None:
            muted, silent = await _apply_notice(notice)
            if muted:
                print(f"[MOCK Discord DM] -> {user_id}: skipped, {notice.key}:{notice.recipient.value} is off")
                return True, DM_NOTICE_OFF
        # Email and Telegram mirror in mock mode too: their transports print
        # instead of sending, so there's no external side effect to fear.
        if mirror:
            _mirror_dm_to_channels(user_id, message, link, silent=silent)
        # Deliberately no web-push mirror: mock mode must have no external side
        # effects (a dev with a prod DB snapshot + prod VAPID keys would push
        # to real users' phones). Real delivery requires the real service.
        if not await discord_dms_enabled(user_id):
            print(f"[MOCK Discord DM] -> {user_id}: skipped, Discord DMs off")
            return True, DM_DISCORD_OFF
        suffix = f" [embed: {embed.title}]" if embed is not None else ""
        # The link is printed because it is the half of the DM mock mode can
        # actually verify — button interactions need a live bot, but whether the
        # call to action points anywhere is checkable from stdout.
        if link is not None and link.url:
            suffix += f" [button: {link.label} -> {link.url}]"
        if silent:
            suffix += " [silent]"
        print(f"[MOCK Discord DM] -> {user_id}: {message}{suffix}")
        # Made-up ids, so a later edit_dm has something to print against.
        self.__class__._mock_message_id += 1
        await _run_sent_hook(on_sent, int(user_id), self.__class__._mock_message_id)
        return True, "Message sent (mock)"

    async def edit_dm(
        self,
        channel_id: int,
        message_id: int,
        embed: "discord.Embed",
        link: Optional[DMLink] = None,
    ) -> Tuple[bool, str]:
        suffix = f" [button: {link.label} -> {link.url}]" if link is not None and link.url else ""
        print(f"[MOCK Discord DM edit] {channel_id}/{message_id}: {embed.title}{suffix}")
        return True, "Message edited (mock)"

    # Thin wrappers matching DiscordService's public surface; all defer to the
    # single send_dm stub above (the buttons are irrelevant in mock mode).
    async def send_dm_with_crew_buttons(self, user_id: int, message: str, match_id: int, embed: Optional["discord.Embed"] = None, link: Optional[DMLink] = None, notice: Optional[NoticeTarget] = None) -> Tuple[bool, str]:
        return await self.send_dm(user_id, message, embed=embed, link=link, notice=notice)

    async def send_dm_with_acknowledgment_button(self, user_id: int, message: str, match_id: int, embed: Optional["discord.Embed"] = None, link: Optional[DMLink] = None, notice: Optional[NoticeTarget] = None) -> Tuple[bool, str]:
        return await self.send_dm(user_id, message, embed=embed, link=link, notice=notice)

    async def send_dm_with_reschedule_agree_button(self, user_id: int, message: str, request_id: int, embed: Optional["discord.Embed"] = None, link: Optional[DMLink] = None, notice: Optional[NoticeTarget] = None) -> Tuple[bool, str]:
        return await self.send_dm(user_id, message, embed=embed, link=link, notice=notice)

    async def send_dm_with_hard_preset_buttons(self, user_id: int, message: str, match_id: int, opted_in: bool, embed: Optional["discord.Embed"] = None, link: Optional[DMLink] = None, notice: Optional[NoticeTarget] = None) -> Tuple[bool, str]:
        return await self.send_dm(user_id, message, embed=embed, link=link, notice=notice)

    async def send_dm_with_crew_acknowledgment_button(self, user_id: int, message: str, crew_type: str, crew_id: int, embed: Optional["discord.Embed"] = None, link: Optional[DMLink] = None, notice: Optional[NoticeTarget] = None) -> Tuple[bool, str]:
        return await self.send_dm(user_id, message, embed=embed, link=link, notice=notice)

    async def send_dm_with_volunteer_acknowledgment_button(self, user_id: int, message: str, assignment_id: int, embed: Optional["discord.Embed"] = None, link: Optional[DMLink] = None, notice: Optional[NoticeTarget] = None) -> Tuple[bool, str]:
        return await self.send_dm(user_id, message, embed=embed, link=link, notice=notice)

    async def send_dm_with_volunteer_arrival_buttons(self, user_id: int, message: str, assignment_id: int, embed: Optional["discord.Embed"] = None, link: Optional[DMLink] = None, notice: Optional[NoticeTarget] = None) -> Tuple[bool, str]:
        return await self.send_dm(user_id, message, embed=embed, link=link, notice=notice)

    async def send_dm_with_unwatch_button(self, user_id: int, message: str, match_id: int, embed: Optional["discord.Embed"] = None, link: Optional[DMLink] = None, notice: Optional[NoticeTarget] = None) -> Tuple[bool, str]:
        return await self.send_dm(user_id, message, embed=embed, link=link, notice=notice)

    def get_bot(self) -> None:
        return None



from application.utils.mocks.mock_discord import is_mock_discord

# Stable handle to the real implementation; survives the mock swap below so tests
# can exercise the real error branches regardless of MOCK_DISCORD.
_RealDiscordService = DiscordService

if is_mock_discord():
    DiscordService = MockDiscordService  # type: ignore[misc,assignment]


def discord_bot_down() -> bool:
    """True when a real bot should be connected and isn't.

    For workers that stamp a reminder as sent before queueing the DM: while this
    holds they wait, so the reminder goes out after the reconnect instead of
    being stamped and dropped.
    """
    if is_mock_discord() or _bot_instance is None or not os.environ.get('DISCORD_TOKEN'):
        return False
    return not _bot_instance.is_ready()
