#!/usr/bin/env python3
"""Print every Discord DM and ephemeral reply the bot can send, with sample data.

Calls the real builders in ``application/utils/discord_messages.py`` (plain text)
and ``application/utils/discord_embeds.py`` (the colour-coded cards) — no bot, no
Discord connection, no database. This is how you "see" the Discord surface: the
copy and card shape are fully determined by these functions. Run from the repo
root:

    poetry run python .claude/skills/discord-ux/render_surface.py

Every notification is *sent* as an embed; the plain-text string still flows
through ``send_dm`` for the web-push mirror and the mock, so both are rendered
below. If a builder's signature changes, update the sample call to match.
See .claude/skills/discord-ux/SKILL.md for the full reconstruction workflow.
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.getcwd())

from application.utils import discord_embeds as e
from application.utils import discord_messages as m
from application.utils import discord_messages_crew as mc
from application.utils import discord_messages_volunteer as mv

# Representative sample data (two players → "A vs B"; a stage; an Eastern time).
P = ["Player One", "Player Two"]
T = "Wizzrobe Dev Tournament"
WHEN = "2026-07-19 22:41 EDT"
STAGE = "Stage 2"
WW_PERMALINK = "eJxLSS2LL0nMy8o31jPUMzTQM9czjk8xNkgxMk9mcEktC05NTQnPzEsJT8xO..."
WW_PASTE = ("Paste this permalink into Wind Waker Randomizer build **dev_tanjo3.1.10.7.3**. "
            "Other builds will reject it.")
COMMUNITY = "Wizzrobe Dev Community"  # the per-embed footer (which community sent this)
# Raw UTC drives the embeds' native <t:unix:F>/<t:unix:R> timestamps (the plain
# text builders take the pre-formatted Eastern string above instead).
WHEN_UTC = datetime(2026, 7, 20, 2, 41, tzinfo=timezone.utc)
SHIFT = ("Race Proctor", "Shift 1", "2026-07-19 08:00 EDT", "2026-07-19 12:00 EDT")
SHIFT_START_UTC = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)
SHIFT_END_UTC = datetime(2026, 7, 19, 16, 0, tzinfo=timezone.utc)
HARD = "Hard Mode"
STANDARD = "Open Standard"

# Buttons that ride each DM (see docs/reference/discord-integration.md flow table).
NOTIFICATIONS = [
    ("Acknowledgment request (new match)", "[Acknowledge]",
     m.acknowledgment_request_dm(T, WHEN, rescheduled=False, stage_name=STAGE, player_names=P)),
    ("Acknowledgment request (rescheduled)", "[Acknowledge]",
     m.acknowledgment_request_dm(T, WHEN, rescheduled=True, stage_name=STAGE, player_names=P)),
    ("Scheduled — crew/subscriber info", "[Unwatch] (watchers only)",
     m.scheduled_dm(T, WHEN, player_names=P, stage_name=STAGE)),
    ("Rescheduled — crew/subscriber info", "[Unwatch] (watchers only)",
     m.rescheduled_dm(T, WHEN, player_names=P, stage_name=STAGE)),
    ("Checked in", "[Unwatch] (watchers only)",
     m.checked_in_dm(T, player_names=P, scheduled_at_display=WHEN, stage_name=STAGE)),
    ("State changed — Started (also Finished/Confirmed)", "[Unwatch] (watchers only)",
     m.state_changed_dm(T, "Started", player_names=P, scheduled_at_display=WHEN, stage_name=STAGE)),
    ("Stream candidate alert", "[Sign up as Commentator] [Sign up as Tracker]",
     m.stream_candidate_dm(T, WHEN, player_names=P)),
    ("Crew signup invitation (subscribers)", "[Sign up as Commentator] [Sign up as Tracker]",
     m.scheduled_dm(T, WHEN, player_names=P, stage_name=STAGE)),
    ("Crew approved", "[Acknowledge]",
     mc.crew_assignment_dm("commentator", "Player One vs Player Two", WHEN, STAGE, P)),
    ("Seed generated", "[Open your seed] (link, off-site)",
     m.seed_dm("Player One", T, "https://alttpr.com/en/h/abcd1234",
               player_names=P, scheduled_at_display=WHEN, stage_name=STAGE)),
    ("Seed generated — pasted seed (Wind Waker permalink; Z1R likewise)", "[Copy it from Wizzrobe] (link only)",
     m.seed_dm("Player One", T, WW_PERMALINK, player_names=P, scheduled_at_display=WHEN,
               stage_name=STAGE, paste_instructions=WW_PASTE)),
    ("Volunteer shift assigned", "[Acknowledge]",
     mv.volunteer_assignment_dm(*SHIFT)),
    ("Volunteer shift reminder", "[Acknowledge]",
     mv.volunteer_reminder_dm(*SHIFT)),
    ("Settings set by staff", "[View your match] (link only)",
     m.preset_override_dm(T, HARD, forced=True)),
    ("Settings handed back by staff", "[Answer the questions]",
     m.preset_override_dm(T, HARD, forced=False)),
]

EPHEMERAL = [
    ("crew signup", mc.crew_signup_confirmation("commentator", "Player One vs Player Two")),
    ("match ack", m.match_ack_confirmation("Player One vs Player Two")),
    ("crew ack", mc.crew_ack_confirmation("commentator", "Player One vs Player Two")),
    ("volunteer ack", mv.volunteer_ack_confirmation("Race Proctor")),
    ("unwatch (was watching)", m.unwatch_confirmation("Player One vs Player Two", True)),
    ("unwatch (was not)", m.unwatch_confirmation("Player One vs Player Two", False)),
    ("no account", m.MSG_NO_ACCOUNT),
]

# The embed *card* the bot actually sends (the plain text above rides along only
# for the web-push mirror). These mirror what the services build — keep the
# titles/colours in sync with match_schedule_service / crew_service /
# volunteer_schedule_service / volunteer_reminder if they change.
EMBEDS = [
    ("Match scheduled", e.match_embed(
        title="📣 Match scheduled", color=e.COLOR_SCHEDULED, tournament=T,
        community_name=COMMUNITY, player_names=P, when=WHEN_UTC, stage_name=STAGE)),
    ("Match rescheduled", e.match_embed(
        title="🔄 Match rescheduled", color=e.COLOR_RESCHEDULED, tournament=T,
        community_name=COMMUNITY, player_names=P, when=WHEN_UTC, stage_name=STAGE)),
    ("Acknowledgment request", e.match_embed(
        title="📣 Match scheduled", color=e.COLOR_SCHEDULED, tournament=T,
        community_name=COMMUNITY, player_names=P, when=WHEN_UTC, stage_name=STAGE,
        description="Tap **Acknowledge** below to confirm you have seen this.")),
    ("Checked in", e.match_embed(
        title="✅ Match checked in", color=e.COLOR_CHECKED_IN, tournament=T,
        community_name=COMMUNITY, player_names=P, when=WHEN_UTC, stage_name=STAGE,
        description="The match is about to begin — good luck!")),
    ("State changed — Started", e.state_changed_embed(
        T, "Started", community_name=COMMUNITY, player_names=P, when=WHEN_UTC, stage_name=STAGE)),
    ("State changed — Finished", e.state_changed_embed(
        T, "Finished", community_name=COMMUNITY, player_names=P, when=WHEN_UTC, stage_name=STAGE)),
    ("State changed — Confirmed", e.state_changed_embed(
        T, "Confirmed", community_name=COMMUNITY, player_names=P, when=WHEN_UTC, stage_name=STAGE)),
    ("Stream candidate", e.match_embed(
        title="🎥 Stream candidate", color=e.COLOR_STREAM, tournament=T,
        community_name=COMMUNITY, player_names=P, when=WHEN_UTC,
        description="This match may be streamed — sign up to crew below.")),
    ("Seed ready", e.match_embed(
        title="🎲 Seed ready", color=e.COLOR_SEED, tournament=T, community_name=COMMUNITY,
        player_names=P, when=WHEN_UTC, stage_name=STAGE,
        description="[Open your seed](https://alttpr.com/en/h/abcd1234)",
        url="https://alttpr.com/en/h/abcd1234")),
    ("Seed ready — pasted seed", e.match_embed(
        title="🎲 Seed ready", color=e.COLOR_SEED, tournament=T, community_name=COMMUNITY,
        player_names=P, when=WHEN_UTC, stage_name=STAGE,
        description=m.seed_paste_block(WW_PERMALINK, WW_PASTE))),
    ("Crew assignment", e.notification_embed(
        title="🎙️ Commentator assignment", color=e.COLOR_CREW, community_name=COMMUNITY,
        description="Tap **Acknowledge** below to confirm you can cover this.",
        fields=[("Match", "Player One vs Player Two", False),
                ("Time", e.time_field(WHEN_UTC), False), ("Stage", STAGE, True)])),
    ("Volunteer shift assigned", e.volunteer_embed(
        title="🙋 Volunteer shift assigned", position="Race Proctor", community_name=COMMUNITY,
        starts=SHIFT_START_UTC, ends=SHIFT_END_UTC,
        description="**Shift 1**\nTap **Acknowledge** below to confirm you can cover this shift.")),
    ("Volunteer shift reminder", e.volunteer_embed(
        title="⏰ Volunteer shift reminder", position="Race Proctor", community_name=COMMUNITY,
        starts=SHIFT_START_UTC, ends=SHIFT_END_UTC,
        description="**Shift 1**\nYour shift is coming up. Tap **Acknowledge** to confirm you are covering it.")),
    ("Settings set by staff", e.match_embed(
        title="⚙️ Settings set by staff", color=e.COLOR_SEED, tournament=T,
        community_name=COMMUNITY, player_names=P,
        when=WHEN_UTC, description=m.preset_override_dm(T, HARD, forced=True))),
    ("Settings back in your hands", e.match_embed(
        title="⚙️ Settings back in your hands", color=e.COLOR_SEED, tournament=T,
        community_name=COMMUNITY, player_names=P,
        when=WHEN_UTC, description=m.preset_override_dm(T, HARD, forced=False))),
]


def _rule(char: str = "─") -> str:
    return char * 72


def _print_embed(name: str, embed) -> None:
    footer = embed.footer.text if embed.footer else ""
    print(f"\n### {name}\n{_rule()}")
    print(f"╔ {embed.title}   (colour #{embed.colour.value:06X})")
    if embed.description:
        print(f"║ {embed.description}")
    for field in embed.fields:
        inline = " ·inline" if field.inline else ""
        print(f"║ {field.name}: {field.value}{inline}")
    if footer:
        print(f"╚ — {footer}")
    else:
        print("╚")


def main() -> None:
    print(_rule("="))
    print("DISCORD EMBED CARDS  (what the bot sends — colour-coded, <t:> timestamps)")
    print(_rule("="))
    print("Note: <t:unix:F>=viewer-local date, <t:unix:R>=live countdown.")
    for name, embed in EMBEDS:
        _print_embed(name, embed)
    print(f"\n{_rule('=')}")
    print("PLAIN-TEXT DMs  (web-push mirror / fallback — rides under each embed)")
    print(_rule("="))
    for name, buttons, text in NOTIFICATIONS:
        print(f"\n### {name}\nbuttons: {buttons}\n{_rule()}")
        print(text)
    print(f"\n{_rule('=')}")
    print("EPHEMERAL REPLIES  (private, shown after a button click)")
    print(_rule("="))
    for name, text in EPHEMERAL:
        print(f"\n[{name}]  {text}")
    print()


if __name__ == "__main__":
    main()
