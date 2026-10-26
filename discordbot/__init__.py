"""Discord bot package.

Importing this package wires the bot's component-interaction handlers and DM
view factories into ``application.services.discord.discord_service`` via its registries.

This is the inversion of what used to be a bidirectional import cycle between
``discord_service.py`` and ``discordbot/`` — previously held together only by
~20 deferred, function-body imports on both sides. The dependency now runs one
way (``discordbot`` -> ``application.services``); ``discord_service`` no longer
imports ``discordbot`` at all. Mirrors ``application/events/match_live.py``.

``main.py``'s lifespan imports this package once at startup so the registration
runs before any Discord interaction is dispatched or DM is sent.
"""

from application.services.discord.discord_service import (
    VIEW_CREW_ACK,
    VIEW_CREW_SIGNUP,
    VIEW_HARD_PRESET,
    VIEW_MATCH_ACK,
    VIEW_PARTNER_INVITE,
    VIEW_PLAYER_LATE,
    VIEW_RESCHEDULE_AGREE,
    VIEW_SEED_READY,
    VIEW_UNWATCH,
    VIEW_VOLUNTEER_ACK,
    VIEW_VOLUNTEER_ARRIVAL,
    register_app_command,
    register_interaction_handler,
    register_view_factory,
)
from discordbot.crew_acknowledgment import (
    CUSTOM_ID_PREFIX as CREW_ACK_PREFIX,
)
from discordbot.crew_acknowledgment import (
    handle_crew_acknowledgment_interaction,
    make_crew_acknowledgment_view,
)
from discordbot.crew_signup import (
    CUSTOM_ID_PREFIX as CREW_SIGNUP_PREFIX,
)
from discordbot.crew_signup import (
    handle_crew_signup_interaction,
    make_crew_signup_view,
)
from discordbot.login import (
    CUSTOM_ID_PREFIX as LOGIN_PANEL_PREFIX,
)
from discordbot.login import (
    handle_login_panel_interaction,
    login_command,
    wizzrobe_group,
)
from discordbot.match_acknowledgment import (
    CUSTOM_ID_PREFIX as MATCH_ACK_PREFIX,
)
from discordbot.match_acknowledgment import (
    handle_match_acknowledgment_interaction,
    make_match_acknowledgment_view,
)
from discordbot.match_hard_preset import (
    CUSTOM_ID_PREFIX as HARD_PRESET_PREFIX,
)
from discordbot.match_hard_preset import (
    handle_hard_preset_interaction,
    make_hard_preset_view,
)
from discordbot.partner_invite import (
    CUSTOM_ID_PREFIX as PARTNER_INVITE_PREFIX,
)
from discordbot.partner_invite import (
    handle_partner_invite_interaction,
    make_partner_invite_view,
)
from discordbot.player_late import (
    LATE_MINUTES_PREFIX as PLAYER_LATE_MINUTES_PREFIX,
)
from discordbot.player_late import (
    LATE_PREFIX as PLAYER_LATE_PREFIX,
)
from discordbot.player_late import (
    handle_player_late_interaction,
    handle_player_late_minutes_interaction,
    make_player_late_view,
)
from discordbot.reschedule_agreement import (
    CUSTOM_ID_PREFIX as RESCHEDULE_AGREE_PREFIX,
)
from discordbot.reschedule_agreement import (
    handle_reschedule_agree_interaction,
    make_reschedule_agree_view,
)
from discordbot.seed_ready import (
    CUSTOM_ID_PREFIX as SEED_READY_PREFIX,
)
from discordbot.seed_ready import (
    handle_seed_ready_interaction,
    make_seed_ready_view,
)
from discordbot.volunteer_acknowledgment import (
    CUSTOM_ID_PREFIX as VOLUNTEER_ACK_PREFIX,
)
from discordbot.volunteer_acknowledgment import (
    handle_volunteer_acknowledgment_interaction,
    make_volunteer_acknowledgment_view,
)
from discordbot.volunteer_arrival import (
    CANT_PREFIX as VOLUNTEER_CANT_PREFIX,
)
from discordbot.volunteer_arrival import (
    CANT_REASON_PREFIX as VOLUNTEER_CANT_REASON_PREFIX,
)
from discordbot.volunteer_arrival import (
    HERE_PREFIX as VOLUNTEER_HERE_PREFIX,
)
from discordbot.volunteer_arrival import (
    LATE_MINUTES_PREFIX as VOLUNTEER_LATE_MINUTES_PREFIX,
)
from discordbot.volunteer_arrival import (
    LATE_PREFIX as VOLUNTEER_LATE_PREFIX,
)
from discordbot.volunteer_arrival import (
    handle_cant_interaction,
    handle_cant_reason_interaction,
    handle_here_interaction,
    handle_late_interaction,
    handle_late_minutes_interaction,
    make_volunteer_arrival_view,
)
from discordbot.watch_buttons import (
    CUSTOM_ID_PREFIX as WATCH_PREFIX,
)
from discordbot.watch_buttons import (
    handle_unwatch_interaction,
    make_unwatch_view,
)

# Component-interaction handlers, keyed by the custom_id prefix baked into each
# button (text before the first ':').
register_interaction_handler(CREW_SIGNUP_PREFIX, handle_crew_signup_interaction)
register_interaction_handler(MATCH_ACK_PREFIX, handle_match_acknowledgment_interaction)
register_interaction_handler(CREW_ACK_PREFIX, handle_crew_acknowledgment_interaction)
register_interaction_handler(VOLUNTEER_ACK_PREFIX, handle_volunteer_acknowledgment_interaction)
register_interaction_handler(VOLUNTEER_HERE_PREFIX, handle_here_interaction)
register_interaction_handler(VOLUNTEER_LATE_PREFIX, handle_late_interaction)
register_interaction_handler(VOLUNTEER_LATE_MINUTES_PREFIX, handle_late_minutes_interaction)
register_interaction_handler(VOLUNTEER_CANT_PREFIX, handle_cant_interaction)
register_interaction_handler(VOLUNTEER_CANT_REASON_PREFIX, handle_cant_reason_interaction)
register_interaction_handler(PLAYER_LATE_PREFIX, handle_player_late_interaction)
register_interaction_handler(PLAYER_LATE_MINUTES_PREFIX, handle_player_late_minutes_interaction)
register_interaction_handler(WATCH_PREFIX, handle_unwatch_interaction)
register_interaction_handler(SEED_READY_PREFIX, handle_seed_ready_interaction)
register_interaction_handler(RESCHEDULE_AGREE_PREFIX, handle_reschedule_agree_interaction)
register_interaction_handler(HARD_PRESET_PREFIX, handle_hard_preset_interaction)
register_interaction_handler(LOGIN_PANEL_PREFIX, handle_login_panel_interaction)
register_interaction_handler(PARTNER_INVITE_PREFIX, handle_partner_invite_interaction)

# Slash commands, added to the bot's command tree and synced when it logs in.
register_app_command(login_command)
register_app_command(wizzrobe_group)

# DM view factories, keyed by the VIEW_* kinds the DM senders look up.
register_view_factory(VIEW_CREW_SIGNUP, make_crew_signup_view)
register_view_factory(VIEW_MATCH_ACK, make_match_acknowledgment_view)
register_view_factory(VIEW_CREW_ACK, make_crew_acknowledgment_view)
register_view_factory(VIEW_VOLUNTEER_ACK, make_volunteer_acknowledgment_view)
register_view_factory(VIEW_VOLUNTEER_ARRIVAL, make_volunteer_arrival_view)
register_view_factory(VIEW_UNWATCH, make_unwatch_view)
register_view_factory(VIEW_RESCHEDULE_AGREE, make_reschedule_agree_view)
register_view_factory(VIEW_HARD_PRESET, make_hard_preset_view)
register_view_factory(VIEW_PLAYER_LATE, make_player_late_view)
register_view_factory(VIEW_SEED_READY, make_seed_ready_view)
register_view_factory(VIEW_PARTNER_INVITE, make_partner_invite_view)
