"""Discord Message Templates: volunteer shifts

The DM copy for ``application/services/volunteer/`` — the shift a coordinator
scheduled, the reminder before it, the removal, the move, the hand-back to the
coordinators, and the ephemeral reply
``discordbot/volunteer_acknowledgment.py`` sends after the button.

A sibling of :mod:`application.utils.discord_messages` rather than more lines in
it, on the same rule the qualifier and reschedule copy already follow: volunteer
scheduling is a domain of its own, and the parent module holds the match
lifecycle. The convention is otherwise identical — all Discord text lives in a
builder, and none of it is inlined in service or handler code.

Times arrive as **display strings** already rendered by
``discord_embeds.time_field``, so each recipient's client localises them.
"""

from typing import Optional

import discord

# A volunteer's own words, shown to coordinators in a Discord embed. Escaped so
# they read as plain text (no links, mentions or formatting) and capped so one
# message can't fill the card.
REASON_MAX_CHARS = 300


def _volunteer_shift_lines(
    position_name: str,
    label: Optional[str],
    starts_display: str,
    ends_display: str,
) -> list[str]:
    title = position_name
    if label:
        title = f"{position_name} — {label}"
    lines = [f"**Position:** {title}"]
    if starts_display:
        lines.append(f"**Start:** {starts_display}")
    if ends_display:
        lines.append(f"**End:** {ends_display}")
    return lines


def volunteer_assignment_dm(
    position_name: str,
    label: Optional[str],
    starts_display: str,
    ends_display: str,
) -> str:
    """DM sent when a volunteer is assigned to a shift.

    No community name is hardcoded — the app is multi-tenant, so a fixed org
    label would be wrong for every community but one. Blank lines separate the
    intro, the shift block, and the call to action.
    """
    details = _volunteer_shift_lines(position_name, label, starts_display, ends_display)
    blocks = ["You've been scheduled for a volunteer shift."]
    if details:
        blocks.append("\n".join(details))
    blocks.append("Please click below to acknowledge your shift.")
    return "\n\n".join(blocks)


def volunteer_reminder_dm(
    position_name: str,
    label: Optional[str],
    starts_display: str,
    ends_display: str,
    acknowledged: bool = False,
) -> str:
    """Reminder DM sent ahead of a volunteer shift.

    Someone who already acknowledged isn't asked again; either way the DM points
    at the way out, because the reminder is when people find out they can't go.
    """
    details = _volunteer_shift_lines(position_name, label, starts_display, ends_display)
    blocks = ["⏰ Reminder: you have an upcoming volunteer shift."]
    if details:
        blocks.append("\n".join(details))
    if acknowledged:
        blocks.append("You've already confirmed it. If you can't make it after all, "
                      "tap **Can't make it** so your coordinator can find cover.")
    else:
        blocks.append("Tap **Acknowledge** to confirm, or **Can't make it** if you can't.")
    return "\n\n".join(blocks)


def volunteer_reminder_description(acknowledged: bool) -> str:
    """The reminder embed's one-line call to action."""
    if acknowledged:
        return "You've confirmed this one. Tap **Can't make it** if that's changed."
    return 'Your shift is coming up. Tap **Acknowledge** to confirm you are covering it.'


def volunteer_unassigned_dm(
    position_name: str,
    label: Optional[str],
    starts_display: str,
    ends_display: str,
) -> str:
    """DM sent when a coordinator takes a volunteer off a shift.

    No acknowledgment button: there is nothing for them to confirm, and the point
    is that the removal stops being silent.
    """
    details = _volunteer_shift_lines(position_name, label, starts_display, ends_display)
    blocks = ["You have been taken off a volunteer shift."]
    if details:
        blocks.append("\n".join(details))
    blocks.append("No action needed. Ask your coordinator if this looks wrong.")
    return "\n\n".join(blocks)


def volunteer_shift_changed_dm(
    position_name: str,
    label: Optional[str],
    starts_display: str,
    ends_display: str,
    old_starts_display: str = "",
    old_ends_display: str = "",
) -> str:
    """DM sent when a shift a volunteer is on moves in time.

    Both windows are shown, because "the shift moved" without the old time is
    unverifiable against what they remember agreeing to.
    """
    details = _volunteer_shift_lines(position_name, label, starts_display, ends_display)
    blocks = ["A shift you're on has moved."]
    if old_starts_display or old_ends_display:
        was = " → ".join(p for p in (old_starts_display, old_ends_display) if p)
        blocks.append(f"**Was:** {was}")
    if details:
        blocks.append("\n".join(details))
    blocks.append("Tap **Acknowledge** to confirm you can still cover it.")
    return "\n\n".join(blocks)


def volunteer_released_dm(
    volunteer_name: str,
    position_name: str,
    label: Optional[str],
    starts_display: str,
    ends_display: str,
    hours_notice: Optional[float] = None,
    reason: Optional[str] = None,
) -> str:
    """DM to the coordinators when a volunteer gives a shift back."""
    details = _volunteer_shift_lines(position_name, label, starts_display, ends_display)
    blocks = [f"**{volunteer_name}** has given back a volunteer shift."]
    if details:
        blocks.append("\n".join(details))
    notice = released_notice(hours_notice)
    if notice:
        blocks.append(notice)
    if reason:
        blocks.append(f'Their reason: "{reason}"')
    blocks.append("This slot is open again.")
    return "\n\n".join(blocks)


def released_notice(hours_notice: Optional[float]) -> Optional[str]:
    """How much warning the coordinators got, or that the shift had already begun."""
    if hours_notice is None:
        return None
    if hours_notice <= 0:
        return "Notice: given after the shift started."
    hours = round(hours_notice)
    if hours < 1:
        return "Notice: less than an hour."
    return f"Notice: about {hours} hour{'' if hours == 1 else 's'}."


def quoted_reason(reason: Optional[str]) -> Optional[str]:
    """A volunteer's reason, made safe to drop into an embed description."""
    text = ' '.join((reason or '').split())
    if not text:
        return None
    if len(text) > REASON_MAX_CHARS:
        text = text[:REASON_MAX_CHARS - 1].rstrip() + '…'
    text = discord.utils.escape_mentions(discord.utils.escape_markdown(text))
    return f'Their reason: “{text}”'


def released_description(
    volunteer_name: str, hours_notice: Optional[float], reason: Optional[str],
) -> str:
    """The coordinators' release embed: who, how much notice, and why.

    The embed replaces the message text in Discord, so the reason has to be in
    here or the coordinator never sees it.
    """
    lines = [f'**{discord.utils.escape_markdown(volunteer_name)}** can no longer cover this. '
             'The slot is open again.']
    notice = released_notice(hours_notice)
    if notice:
        lines.append(notice)
    said = quoted_reason(reason)
    if said:
        lines.append(said)
    return '\n'.join(lines)


LATE_CHOICES: tuple[int, ...] = (10, 20, 30)


def late_label(minutes: int) -> str:
    """'About 10 minutes' / 'About 20 minutes' / '30 minutes or more'."""
    if minutes >= LATE_CHOICES[-1]:
        return f'{LATE_CHOICES[-1]} minutes or more'
    return f'About {minutes} minutes'


def volunteer_shift_start_dm(
    position_name: str,
    label: Optional[str],
    starts_display: str,
    ends_display: str,
) -> str:
    """DM at the start of a shift, asking the volunteer to say they've arrived."""
    details = _volunteer_shift_lines(position_name, label, starts_display, ends_display)
    blocks = ["Your volunteer shift is starting."]
    if details:
        blocks.append("\n".join(details))
    blocks.append("Tap **I'm here** so your coordinator knows, **Tell staff I'm late** if "
                  "you're on your way, or **I can't make it**.")
    return "\n\n".join(blocks)


VOLUNTEER_SHIFT_START_DESCRIPTION = (
    "Your shift is starting. Tap **I'm here** so your coordinator knows."
)


def volunteer_running_late_dm(
    volunteer_name: str,
    position_name: str,
    label: Optional[str],
    starts_display: str,
    ends_display: str,
    minutes: int,
) -> str:
    """DM to the coordinators when a volunteer says they're running late."""
    details = _volunteer_shift_lines(position_name, label, starts_display, ends_display)
    blocks = [f"**{volunteer_name}** is running late for their shift "
              f"({late_label(minutes).lower()})."]
    if details:
        blocks.append("\n".join(details))
    blocks.append("They're keeping the shift.")
    return "\n\n".join(blocks)


def running_late_description(volunteer_name: str, minutes: int) -> str:
    return (f'**{discord.utils.escape_markdown(volunteer_name)}** is on their way '
            f'({late_label(minutes).lower()} late). They\'re keeping the shift.')


def volunteer_here_confirmation(position_name: str) -> str:
    return f"Thanks! You're checked in for **{position_name}**."


def volunteer_late_confirmation(minutes: int) -> str:
    return (f"Thanks for the heads-up. Your coordinator knows you're running late "
            f"({late_label(minutes).lower()}). Tap **I'm here** when you arrive.")


def volunteer_released_confirmation() -> str:
    return "Your shift is given back and your coordinator has been told."


def volunteer_ack_confirmation(position_name: str) -> str:
    return f"Thanks! Your **{position_name}** shift is acknowledged."


def _call_off_note(note: Optional[str], by_name: Optional[str]) -> Optional[str]:
    """Staff's optional note, flattened, capped and escaped like a volunteer's reason."""
    text = ' '.join((note or '').split())
    if not text:
        return None
    if len(text) > REASON_MAX_CHARS:
        text = text[:REASON_MAX_CHARS - 1].rstrip() + '…'
    text = discord.utils.escape_mentions(discord.utils.escape_markdown(text))
    who = discord.utils.escape_markdown(by_name) if by_name else 'your coordinator'
    return f'**Note from {who}:** {text}'


def call_off_title(position_name: str, started: bool) -> str:
    """The call-off DM's headline: before the shift, or partway through it."""
    where = position_name or 'your shift'
    return f'You can head out from {where}' if started else f"You're not needed for {where}"


def call_off_description(
    hours: float, started: bool, note: Optional[str] = None, by_name: Optional[str] = None,
) -> str:
    """The call-off embed's body: don't come (or go home), and it still counts."""
    worked = f'{hours:g} hour{"" if hours == 1 else "s"}'
    if started:
        lines = ['Thanks for your help today. You can leave now, and the full shift '
                 f'still counts as worked: all {worked} go toward your volunteer total.']
    else:
        lines = ['No need to come in. It still counts as worked: '
                 f'all {worked} go toward your volunteer total.']
    said = _call_off_note(note, by_name)
    if said:
        lines.append(said)
    return '\n\n'.join(lines)


def volunteer_called_off_dm(
    position_name: str,
    label: Optional[str],
    starts_display: str,
    ends_display: str,
    hours: float,
    started: bool,
    note: Optional[str] = None,
    by_name: Optional[str] = None,
) -> str:
    """Plain-text twin of the call-off embed, for web push and the fallback."""
    details = _volunteer_shift_lines(position_name, label, starts_display, ends_display)
    blocks = [call_off_title(position_name, started) + '.']
    if details:
        blocks.append('\n'.join(details))
    blocks.append(call_off_description(hours, started, note, by_name))
    return '\n\n'.join(blocks)


def call_off_undone_title(position_name: str) -> str:
    return f"You're needed for {position_name or 'your shift'} after all"


def volunteer_call_off_undone_dm(
    position_name: str,
    label: Optional[str],
    starts_display: str,
    ends_display: str,
    by_name: Optional[str] = None,
) -> str:
    """DM when staff take back a call-off: the shift is on again."""
    details = _volunteer_shift_lines(position_name, label, starts_display, ends_display)
    blocks = [call_off_undone_title(position_name) + '.']
    if details:
        blocks.append('\n'.join(details))
    blocks.append(call_off_undone_description(by_name))
    return '\n\n'.join(blocks)


def call_off_undone_description(by_name: Optional[str] = None) -> str:
    who = discord.utils.escape_markdown(by_name) if by_name else 'Your coordinator'
    return f'{who} put you back on this shift. Please check in when you arrive.'
