"""Discord-facing services — outbound sends and guild/event synchronization.

``DiscordService`` is the send surface (DMs, embeds, guild lookups, mockable via
``MOCK_DISCORD``); ``discord_queue`` is the single background worker that
serializes those sends so request handlers never block on Discord. The
``discord_event_*`` trio mirrors tournament matches into Discord scheduled
events, and the mapping/link services connect guild roles and accounts to app
identity. ``discord_member_events`` holds the gateway listeners' bodies (app-role
re-sync, cached avatar hash), and ``discord_guild_ops`` / ``discord_scheduled_events``
hold the halves of ``DiscordService`` that are not about DMs — each as a mixin
composed into it, beside the mock twin it must stay in step with.
"""

from application.services.discord import (
    bot_state,
    discord_event_worker,
    discord_guild_ops,
    discord_member_events,
    discord_queue,
    discord_role_sync_worker,
    discord_scheduled_events,
    seed_emojis,
)
from application.services.discord.discord_email_service import DiscordEmailService
from application.services.discord.discord_event_reconciler_service import (
    DiscordEventReconcilerService,
)
from application.services.discord.discord_event_sync_service import DiscordEventSyncService
from application.services.discord.discord_link_service import DiscordLinkService
from application.services.discord.discord_login_service import DiscordLoginService
from application.services.discord.discord_member_import_service import (
    DiscordImportPreview,
    DiscordImportResult,
    DiscordMemberImportService,
)
from application.services.discord.discord_role_mapping_service import DiscordRoleMappingService
from application.services.discord.discord_role_sync_service import DiscordRoleSyncService
from application.services.discord.discord_service import DiscordService

__all__ = [
    'bot_state',
    'DiscordEmailService',
    'DiscordEventReconcilerService',
    'DiscordEventSyncService',
    'DiscordImportPreview',
    'DiscordImportResult',
    'DiscordLinkService',
    'DiscordLoginService',
    'DiscordMemberImportService',
    'DiscordRoleMappingService',
    'DiscordRoleSyncService',
    'DiscordService',
    'discord_event_worker',
    'discord_guild_ops',
    'discord_member_events',
    'discord_queue',
    'discord_role_sync_worker',
    'discord_scheduled_events',
    'seed_emojis',
]
