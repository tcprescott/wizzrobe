"""
Discord Message Templates

All Discord DM and ephemeral message text lives here. Import individual
builder functions and constants where needed; do not inline message text
in service or handler code.

Messages never expose raw match ID numbers — they are meaningless to
recipients. A match is identified by the players involved, the scheduled
time, and the stage (when assigned).

This module holds the match lifecycle. Every other domain has a sibling of its
own, all of them moved out as this module kept crossing the 800-line budget:
:mod:`~application.utils.discord_messages_crew` (crew assignment),
:mod:`~application.utils.discord_messages_volunteer` (volunteer shifts),
:mod:`~application.utils.discord_messages_qualifier` (async qualifiers),
:mod:`~application.utils.discord_messages_reschedule` (reschedule requests),
:mod:`~application.utils.discord_messages_forfeit` (forfeits and drops) and
:mod:`~application.utils.discord_messages_tenant` (community join requests).
"""

from typing import NamedTuple, Optional, Sequence

import discord

from application.utils.match_labels import and_join, players_label


class DMLink(NamedTuple):
    """A one-click route out of a notification: a button label and where it goes.

    Every DM that asks the recipient to *do* something carries one. ``send_dm``
    renders it as a Discord link button and hands the same URL to the web-push
    mirror as its tap target, so the ask is one press away on either surface —
    rather than a sentence naming a page the reader then has to go find.

    ``url`` must be absolute: a DM is read outside any request context, so a bare
    ``/home/player`` would resolve against nothing. Build them with
    :mod:`application.services.notification_links`.
    """

    label: str
    url: str


MSG_NO_ACCOUNT = (
    "You don't have a Wizzrobe account yet. Log in at the website first and "
    "this will work."
)


def msg_unexpected_error(action: str = 'finish this') -> str:
    """Standard fallback text for a failed Discord button interaction.

    ``action`` names what the website can still do instead of the button —
    "acknowledge", "sign up" — falling back to a generic phrase for
    interactions with no single verb (e.g. unwatch).
    """
    return f"Something went wrong. Try again in a moment, or {action} on the website."


MSG_UNEXPECTED_ERROR = msg_unexpected_error()


_players_label = players_label


def _match_info_lines(
    *,
    player_names: Optional[list[str]] = None,
    scheduled_at_display: str = '',
    stage_name: str = '',
    time_label: str = 'Scheduled for',
    bracket_line: str = '',
) -> list[str]:
    """Consistent identifying block for a match: round, players, time, stage.

    Omits any line whose data is empty. ``bracket_line`` is the series context
    for a match that is one game of a bracket matchup — "Semifinals · Game 2 of 3
    · Series 1-0" — and leads, because it is the thing that tells a player *what
    this match is for*. Empty for the vast majority of matches, which no bracket
    scheduled.
    """
    lines: list[str] = []
    if bracket_line:
        lines.append(f"Round: {bracket_line}")
    players = _players_label(player_names)
    if players:
        lines.append(f"Players: {players}")
    if scheduled_at_display:
        lines.append(f"{time_label}: {scheduled_at_display}")
    if stage_name:
        lines.append(f"Stage: {stage_name}")
    return lines


AUDIENCE_PLAYER = 'player'
AUDIENCE_CREW = 'crew'
AUDIENCE_WATCHER = 'watcher'
AUDIENCE_SUBSCRIBER = 'subscriber'

CREW_COMMENTATOR = 'commentator'
CREW_TRACKER = 'tracker'
CREW_BROADCASTER = 'broadcaster'


_CREW_VERB = {CREW_COMMENTATOR: 'commentating', CREW_TRACKER: 'tracking', CREW_BROADCASTER: 'broadcasting'}


def crew_slot_label(crew_roles: Sequence[str]) -> str:
    """The slot(s) a crew member holds, as prose: "tracker", "commentator and tracker"."""
    return and_join(crew_roles)


def crew_slot_title(title: str, crew_roles: Sequence[str]) -> str:
    """A crew card's title naming their slot: "Your commentator slot is on stage".

    Titles written for crew say "crew slot", which becomes the slot itself; a
    title shared with every audience ("Match started") gains the slot after it.
    """
    if not crew_roles:
        return title
    label = crew_slot_label(crew_roles)
    if 'crew slot' in title:
        return title.replace('crew slot', f'{label} slot')
    return f'{title} · your {label} slot'


def crew_slot_line(message: str, crew_roles: Sequence[str]) -> str:
    """Shared match copy with the reader's slot added, for the web-push text."""
    if not crew_roles:
        return message
    return _join_blocks(message, f"You're on this match as {crew_slot_label(crew_roles)}.")

_MATCH_SUBJECT = {
    AUDIENCE_PLAYER: 'Your match',
    AUDIENCE_CREW: "A match you're on crew for",
    AUDIENCE_WATCHER: "A match you're watching",
    AUDIENCE_SUBSCRIBER: 'A match',
}


def _subject(audience: str, crew_roles: Sequence[str] = ()) -> str:
    if audience == AUDIENCE_CREW and crew_roles:
        verbs = and_join([_CREW_VERB.get(r, r) for r in crew_roles])
        return f"A match you're {verbs}"
    return _MATCH_SUBJECT.get(audience, _MATCH_SUBJECT[AUDIENCE_SUBSCRIBER])


def _join_blocks(*blocks: str) -> str:
    return "\n\n".join(b for b in blocks if b)


def scheduled_dm(
    tournament_name: str,
    scheduled_at_display: str,
    *,
    player_names: Optional[list[str]] = None,
    stage_name: str = '',
    bracket_line: str = '',
    audience: str = AUDIENCE_SUBSCRIBER,
    crew_roles: Sequence[str] = (),
) -> str:
    """A new match, told to someone who is not playing it.

    Subscribers get crew signup buttons under the card, so their tail points at
    them; crew already hold a slot and need it on their calendar; a watcher asked
    only to be kept informed.
    """
    info = _match_info_lines(
        player_names=player_names,
        scheduled_at_display=scheduled_at_display,
        stage_name=stage_name,
        bracket_line=bracket_line,
    )
    tail = {
        AUDIENCE_CREW: "Make sure it's on your calendar.",
        AUDIENCE_WATCHER: '',
    }.get(audience, "Want to commentate or track it? Use the buttons below.")
    if audience == AUDIENCE_SUBSCRIBER:
        intro = f"A match was just scheduled in **{tournament_name}**."
    else:
        intro = f"{_subject(audience, crew_roles)} in **{tournament_name}** is scheduled."
    return _join_blocks(intro, "\n".join(info), tail)


def rescheduled_dm(
    tournament_name: str,
    new_scheduled_at_display: str,
    *,
    player_names: Optional[list[str]] = None,
    stage_name: str = '',
    bracket_line: str = '',
    audience: str = AUDIENCE_SUBSCRIBER,
    crew_roles: Sequence[str] = (),
) -> str:
    """A match moved, told to someone who is not playing it."""
    info = _match_info_lines(
        player_names=player_names,
        scheduled_at_display=new_scheduled_at_display,
        stage_name=stage_name,
        time_label='New time',
        bracket_line=bracket_line,
    )
    tail = {
        AUDIENCE_CREW: (
            "Make sure it's on your calendar. If you can't make the new time, "
            "withdraw from My Schedule so staff can find cover."
        ),
        AUDIENCE_WATCHER: '',
    }.get(audience, "Want to commentate or track it? Use the buttons below.")
    intro = f"{_subject(audience, crew_roles)} in **{tournament_name}** has moved. Here's the new time."
    return _join_blocks(intro, "\n".join(info), tail)


def tournament_signup_dm(tournament_name: str, *, can_request: bool) -> str:
    """The signup confirmation, true however the tournament gets scheduled.

    It used to say "Staff can now schedule you", which is false for a
    bracket-run tournament where players book their own games. At signup time
    the bracket often does not exist yet, so the copy names what happens in
    every mode: the match shows up on My Schedule and a DM says so, including
    the one that asks them to pick a time. ``can_request`` adds the one extra
    route that only some tournaments offer.
    """
    body = (
        f"You're signed up for **{tournament_name}**. Your matches will show up "
        "on My Schedule, and you'll get a DM when one is booked or is waiting on "
        "you to pick a time."
    )
    if can_request:
        body += " You can also request a match from My Schedule."
    return body


def acknowledgment_request_dm(
    tournament_name: str,
    scheduled_at_display: str,
    *,
    rescheduled: bool,
    stage_name: str = '',
    player_names: Optional[list[str]] = None,
    bracket_line: str = '',
) -> str:
    if rescheduled:
        intro = f"Your match in **{tournament_name}** has been rescheduled."
        time_label = 'New time'
    else:
        intro = f"A match has been scheduled for you in **{tournament_name}**."
        time_label = 'Scheduled for'
    info = _match_info_lines(
        player_names=player_names,
        scheduled_at_display=scheduled_at_display,
        stage_name=stage_name,
        time_label=time_label,
        bracket_line=bracket_line,
    )
    body = "\n".join(info)
    return (
        f"{intro}\n\n"
        f"{body}\n\n"
        f"Click **Acknowledge** below to confirm you've seen this."
    )


def _stage_info_lines(
    player_names: Optional[list[str]], scheduled_at_display: str,
) -> str:
    """Players and time for a stage DM, with the stage left out of the block.

    The stage is the headline of all three messages, so a ``Stage:`` line
    underneath would say the same thing twice.
    """
    return "\n".join(_match_info_lines(
        player_names=player_names, scheduled_at_display=scheduled_at_display,
    ))


def stage_assigned_dm(
    tournament_name: str,
    stage_name: str,
    scheduled_at_display: str,
    *,
    player_names: Optional[list[str]] = None,
    audience: str = AUDIENCE_PLAYER,
    crew_roles: Sequence[str] = (),
) -> str:
    """"You're on Kraid" — sent the moment a stage is assigned.

    Where to go is the whole message, so it states the substitution outright:
    the stage, not the tournament room. Crew go with the match; a watcher is
    told where it will be played and asked to do nothing.
    """
    tail = {
        AUDIENCE_CREW: "That's where you'll be crewing it.",
        AUDIENCE_WATCHER: "It'll be played at the stage.",
    }.get(audience, "Play it at the stage rather than in the tournament room.")
    return _join_blocks(
        f"{_subject(audience, crew_roles)} in **{tournament_name}** is on **{stage_name}**.",
        _stage_info_lines(player_names, scheduled_at_display),
        tail,
    )


def stage_cleared_dm(
    tournament_name: str,
    scheduled_at_display: str,
    *,
    player_names: Optional[list[str]] = None,
    audience: str = AUDIENCE_PLAYER,
    crew_roles: Sequence[str] = (),
) -> str:
    """The retraction. Telling someone they're on a stage and never taking it
    back sends them to an empty one."""
    tail = {
        AUDIENCE_CREW: "It's back in the tournament room, so crew it as usual.",
        AUDIENCE_WATCHER: "It'll be played in the tournament room.",
    }.get(audience, "Play it in the tournament room as usual.")
    return _join_blocks(
        f"{_subject(audience, crew_roles)} in **{tournament_name}** is off the stage.",
        _stage_info_lines(player_names, scheduled_at_display),
        tail,
    )


def stage_reminder_dm(
    tournament_name: str,
    stage_name: str,
    scheduled_at_display: str,
    *,
    player_names: Optional[list[str]] = None,
    audience: str = AUDIENCE_PLAYER,
    crew_roles: Sequence[str] = (),
) -> str:
    """The nudge shortly before the match, naming the stage and the time again.

    The assignment DM may have arrived hours ago and two matches back.
    """
    tail = {
        AUDIENCE_CREW: "Head to the stage.",
        AUDIENCE_WATCHER: '',
    }.get(audience, "Head to the stage rather than the tournament room.")
    return _join_blocks(
        f"{_subject(audience, crew_roles)} in **{tournament_name}** is coming up on **{stage_name}**.",
        _stage_info_lines(player_names, scheduled_at_display),
        tail,
    )


def race_room_open_dm(
    tournament_name: str,
    scheduled_at_display: str,
    *,
    player_names: Optional[list[str]] = None,
    bracket_line: str = '',
) -> str:
    """To each player when their racetime room opens.

    The room is the only place the race happens, and before this the only way
    to find it was racetime.gg itself. The button under the card opens it.
    """
    info = _match_info_lines(
        player_names=player_names,
        scheduled_at_display=scheduled_at_display,
        bracket_line=bracket_line,
    )
    body = "\n".join(info)
    block = f"{body}\n\n" if body else ''
    return (
        f"Your race room for **{tournament_name}** is open.\n\n"
        f"{block}"
        f"Join it on racetime.gg before your start time."
    )


def checked_in_dm(
    tournament_name: str,
    *,
    player_names: Optional[list[str]] = None,
    scheduled_at_display: str = '',
    stage_name: str = '',
    bracket_line: str = '',
    seats: str = '',
    your_seat: str = '',
    closing: str = '',
) -> str:
    """``seats`` lists every assigned seat; ``your_seat`` is the recipient's
    own, for a player who has one, and leads so it reads at a glance.
    ``closing`` replaces the good-luck line (the Ready for seed ask)."""
    info = _match_info_lines(
        player_names=player_names,
        scheduled_at_display=scheduled_at_display,
        stage_name=stage_name,
        bracket_line=bracket_line,
    )
    if seats:
        info.append(f"Seats: {seats}")
    block = ("\n".join(info) + "\n\n") if info else ''
    lead = f"Your seat: **{your_seat}**\n\n" if your_seat else ''
    return (
        f"You're checked in for your match in **{tournament_name}**.\n\n"
        f"{lead}"
        f"{block}"
        + (closing or "The match is about to begin — good luck!")
    )


def cancelled_dm(
    tournament_name: str,
    *,
    reason: str = '',
    player_names: Optional[list[str]] = None,
    scheduled_at_display: str = '',
    stage_name: str = '',
    bracket_line: str = '',
    released: bool = False,
) -> str:
    info = _match_info_lines(
        player_names=player_names,
        scheduled_at_display=scheduled_at_display,
        stage_name=stage_name,
        time_label='Was scheduled for',
        bracket_line=bracket_line,
    )
    block = ("\n".join(info) + "\n\n") if info else ''
    return (
        f"Your match in **{tournament_name}** has been cancelled.\n\n"
        f"{block}"
        f"{cancelled_description(reason=reason, released=released)}"
    )


def cancelled_description(*, reason: str = '', released: bool = False) -> str:
    """The why and what-next of a cancelled-match DM, shared by its text and its card.

    The card needs it as its own description: Discord shows only the embed when
    there is one, so a reason left in the text alone never reached anyone there.
    """
    why = f"{reason}\n\n" if reason else ''
    closing = (
        "This game has been released — the matchup is open to reschedule."
        if released else "You're all set. Nothing else to do here."
    )
    return f"{why}{closing}"


def removed_from_match_dm(
    tournament_name: str,
    *,
    scheduled_at_display: str = '',
    stage_name: str = '',
    bracket_line: str = '',
) -> str:
    """DM to a player staff took off a match's line-up."""
    info = _match_info_lines(
        scheduled_at_display=scheduled_at_display,
        stage_name=stage_name,
        time_label='Was scheduled for',
        bracket_line=bracket_line,
    )
    block = ("\n".join(info) + "\n\n") if info else ''
    return (
        f"Staff have taken you off a match in **{tournament_name}**, so you're no "
        "longer playing in it.\n\n"
        f"{block}"
        "Nothing else to do here. If you think this is a mistake, ask staff."
    )


def state_changed_dm(
    tournament_name: str,
    new_state: str,
    *,
    player_names: Optional[list[str]] = None,
    scheduled_at_display: str = '',
    stage_name: str = '',
    bracket_line: str = '',
) -> str:
    info = _match_info_lines(
        player_names=player_names,
        scheduled_at_display=scheduled_at_display,
        stage_name=stage_name,
        bracket_line=bracket_line,
    )
    block = ("\n\n" + "\n".join(info)) if info else ''
    return f"Your match in **{tournament_name}** is now: **{new_state}**.{block}"


def matchup_ready_dm(
    tournament_name: str,
    round_name: str,
    opponent_name: str,
    *,
    opponent_seed: Optional[int] = None,
    best_of: int = 1,
    schedule_url: str = '',
    rebook: bool = False,
) -> str:
    """The one new bracket notification: "you have a matchup to schedule".

    A DM is sent when it asks the recipient to *do* something, and this is the
    only thing in a bracket that nobody else can do for them —
    ``allow_player_match_requests`` is off for bracket-run tournaments, so this is
    their sole route to booking a time. Advancing and being eliminated get no DM:
    the bracket shows those.

    ``rebook`` is the released-game variant (D3): the matchup was booked, the
    game was called off, and its slot is open again. It says so explicitly rather
    than reading as a duplicate of the first invitation.

    ``schedule_url`` opens the picker itself (``/home/player?schedule=<id>``), not
    the bracket page: the bracket's Schedule button is staff-only, so the two
    people this DM addresses could never have used it.
    """
    opponent = f"{opponent_name} (#{opponent_seed})" if opponent_seed else opponent_name
    lines = [f"Opponent: {opponent}", f"Round: {round_name}"]
    if best_of > 1:
        lines.append(f"Format: Best of {best_of}")
    body = "\n".join(lines)
    if rebook:
        intro = (
            f"Your **{round_name}** match in **{tournament_name}** was called off, "
            f"and the matchup is open to reschedule."
        )
        action = "Pick a new time"
    else:
        intro = (
            f"Your **{round_name}** matchup in **{tournament_name}** is ready to "
            f"schedule."
        )
        action = "Pick a time"
    call = (
        f"[{action}]({schedule_url})" if schedule_url
        else f"{action} on the Player tab of your community's site."
    )
    return f"{intro}\n\n{body}\n\n{call}"


def stream_candidate_dm(
    tournament_name: str,
    scheduled_at_display: str,
    *,
    player_names: Optional[list[str]] = None,
) -> str:
    info = _match_info_lines(
        player_names=player_names,
        scheduled_at_display=scheduled_at_display,
    )
    body = "\n".join(info)
    return (
        f"A match in **{tournament_name}** might be heading to stream!\n\n"
        f"{body}\n\n"
        f"Use the buttons below to sign up as crew."
    )


def is_seed_link(seed: str) -> bool:
    """Whether a rolled seed opens in a browser, or is pasted into a randomizer."""
    return seed.startswith(('https://', 'http://'))


def seed_code_line(code: Optional[list[str]]) -> str:
    """The file-select code as one line, or ``''`` when there isn't one."""
    return 'Code: ' + ' · '.join(code) if code else ''


def seed_paste_block(seed: str, instructions: str) -> str:
    """The instructions, then the seed alone in a code block.

    A code block is what makes it one press to copy in Discord (long-press on
    mobile) and keeps a base64 permalink from being reflowed or markdown-mangled.
    """
    return f"{instructions}\n```\n{seed}\n```"


def seed_dm(
    player_name: str,
    tournament_name: str,
    seed_url: str,
    *,
    player_names: Optional[list[str]] = None,
    scheduled_at_display: str = '',
    stage_name: str = '',
    bracket_line: str = '',
    paste_instructions: Optional[str] = None,
    code_line: str = '',
) -> str:
    """The seed DM's plain text: the web-push body, and the fallback without an embed.

    ``paste_instructions`` is given for a seed that isn't a link (a WWR
    permalink, Z1R seed and flags). The seed goes on a line of its own, not in
    the embed's code block: web push shows this text verbatim, fences and all.
    """
    info = _match_info_lines(
        player_names=player_names,
        scheduled_at_display=scheduled_at_display,
        stage_name=stage_name,
        bracket_line=bracket_line,
    )
    block = ("\n".join(info) + "\n\n") if info else ''
    seed = f"{paste_instructions}\n\n{seed_url}" if paste_instructions else seed_url
    if code_line:
        seed = f"{seed}\n{code_line}"
    return (
        f"Hello {player_name},\n\n"
        f"A seed has been generated for your upcoming match in **{tournament_name}**.\n\n"
        f"{block}"
        f"{seed}\n\n"
        f"Good luck and have fun!"
    )


def match_ack_confirmation(player_names: str) -> str:
    """Ephemeral success reply after a user clicks the match Acknowledge button."""
    if player_names:
        return f"You've acknowledged your match ({player_names}). Thanks!"
    return "You've acknowledged your match. Thanks!"


def unwatch_confirmation(player_names: str, was_watching: bool) -> str:
    """Ephemeral reply after a user clicks the Unwatch button on a match DM."""
    match_ref = f' ({player_names})' if player_names else ''
    if was_watching:
        return f'You are no longer watching the match{match_ref}.'
    return f'You were not watching the match{match_ref}.'


def hard_preset_invite_dm(tournament_name: str, preset_name: str) -> str:
    """The offer, sent with the match's scheduling DM.

    Says the one thing a player needs to decide and the one thing that makes
    deciding safe: nobody hears about it unless everyone agrees.
    """
    return (
        f"**{tournament_name}** offers a harder preset for this match: "
        f"**{preset_name}**.\n\n"
        "You'll only play it if everyone in the match opts in. Your choice stays "
        "private either way — nobody is told you opted in unless all of you did, "
        "and nobody is told if you don't. You can change your mind until the seed "
        "is rolled."
    )


def hard_preset_agreed_dm(tournament_name: str, preset_name: str) -> str:
    """Everyone opted in. The moment the choice stops being private."""
    return (
        f"Everyone in your **{tournament_name}** match opted in, so you're playing "
        f"**{preset_name}**.\n\n"
        "Anyone can still back out until the seed is rolled."
    )


def hard_preset_broken_dm(
    tournament_name: str, preset_name: str, standard_preset_name: str, actor_name: str,
) -> str:
    """A complete set broke. Sent only to players who were told it was complete.

    An empty ``actor_name`` means the roster changed rather than someone backing
    out — nobody in this conversation did anything, and saying a name would
    accuse one of them of it.
    """
    falling_back = (
        f"**{standard_preset_name}**" if standard_preset_name else "the standard settings"
    )
    if actor_name:
        opening = (
            f"{actor_name} backed out of **{preset_name}** for your "
            f"**{tournament_name}** match, so it's back to {falling_back}."
        )
    else:
        opening = (
            f"Your **{tournament_name}** match changed players, so **{preset_name}** "
            f"is off. It's back to {falling_back}."
        )
    return f"{opening}\n\nOpt in again any time before the seed is rolled."


def hard_preset_override_dm(
    tournament_name: str, preset_name: str, *, forced: bool,
) -> str:
    """Staff chose the match's settings, or handed the choice back."""
    if forced:
        opening = (
            f"Staff set your **{tournament_name}** match to **{preset_name}**."
            if preset_name else
            f"Staff chose the settings for your **{tournament_name}** match."
        )
        return f"{opening}\n\nThat decides the settings for this match."
    return (
        f"Staff handed the settings for your **{tournament_name}** match back to "
        "the players.\n\n"
        "It's yours to choose again until the seed is rolled."
    )


def hard_preset_opt_in_confirmation(preset_name: str, *, everyone_in: bool) -> str:
    """Ephemeral reply after a player opts in from a DM button.

    The wording never describes the other players' state, only the caller's own,
    with one exception: once everyone is in there is nothing left to protect.
    """
    if everyone_in:
        return (
            f"You're in, and so is everyone else — this match is playing "
            f"**{preset_name}**."
        )
    return (
        f"You're in for **{preset_name}**. Nobody will be told unless everyone "
        "opts in. You can change your mind until the seed is rolled."
    )


def hard_preset_withdraw_confirmation(preset_name: str) -> str:
    """Ephemeral reply after a player takes their opt-in back from a DM button."""
    return (
        f"You're out of **{preset_name}**. This match will use the standard "
        "settings unless you opt back in before the seed is rolled."
    )


def player_comment_staff_dm(player_name: str, tournament_name: str, body: str) -> str:
    """A player commented on their match; the copy staff get.

    The comment and the name are player-written, so markdown and mentions are
    escaped: a masked link must not hide where it goes.
    """
    def safe(text: str) -> str:
        return discord.utils.escape_mentions(discord.utils.escape_markdown(text))

    return (
        f"**{safe(player_name)}** commented on their **{tournament_name}** match:\n\n"
        f"{safe(body)}"
    )


def player_late_staff_dm(
    player_name: str, tournament_name: str, minutes: int, note: Optional[str],
) -> str:
    """A player said they're running late; the copy staff get. Name and note are escaped."""
    from application.utils.discord_messages_volunteer import late_label

    def safe(text: str) -> str:
        return discord.utils.escape_mentions(discord.utils.escape_markdown(text))

    lines = [
        f"**{safe(player_name)}** is running late for their **{tournament_name}** match: "
        f"{late_label(minutes).lower()}."
    ]
    if note:
        lines.append(f"> {safe(note)}")
    return '\n\n'.join(lines)


def match_comment_dm(author_name: str, tournament_name: str, body: str) -> str:
    """Staff left a comment the players of a match can read."""
    return f"**{author_name}** left a comment on your **{tournament_name}** match:\n\n{body}"
