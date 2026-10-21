"""The run sheet for the Event > Schedule board everyone reads.

The same one-line-per-match board staff got (``match_run_sheet``), with the
buttons a player or volunteer actually uses left on the line: who's on crew in a
Commentators and a Trackers column, with the viewer's own Sign up / Withdraw in
a tray above each role's names, Acknowledge on a match they play, and the watch
bell. Anything else about a match (seed, stations) is in the details sheet a
tapped line opens.

The viewer's own state is stamped server-side by :func:`arrange_player_run_sheet`
rather than worked out in the templates, so the rules live in one tested place:
a line's buttons are only ever as current as its row, which the board already
re-fetches after every change.
"""

from theme.tables.match_run_sheet import CREW_NAMES, HELD_TAG, crew_people, decorate_run_sheet_row
from theme.tables.match_slots import MY_ACTIONS_COLUMN, STREAM_VOLUNTEER_ACTIONABLE

PLAYER_RUN_SHEET_COLUMNS = [
    {'name': 'scheduled_at', 'label': 'Time', 'field': 'scheduled_at', 'align': 'left'},
    {'name': 'match', 'label': 'Match', 'field': 'title', 'align': 'left'},
    {'name': 'stage', 'label': 'Stage', 'field': 'stage', 'align': 'left'},
    {'name': 'commentators', 'label': 'Commentators', 'field': 'commentators', 'align': 'left'},
    {'name': 'trackers', 'label': 'Trackers', 'field': 'trackers', 'align': 'left'},
    {'name': 'status', 'label': 'Status', 'field': 'state', 'align': 'left'},
    {'name': 'watch', 'label': 'Watch', 'field': 'watch', 'align': 'center'},
]

# My Schedule's own matches add a ⋮ per line holding what only a player of that
# match can do: Ask to change, the harder-settings choice, the stream offer.
# Named MY_ACTIONS_COLUMN so the board still fetches the reschedule and
# harder-settings state those items read (``MatchTableView._shows_column``).
PLAYER_MENU_COLUMN = {'name': MY_ACTIONS_COLUMN, 'label': 'More', 'field': MY_ACTIONS_COLUMN,
                      'align': 'center'}

# (order, header) per group. What's happening now leads; results trail, newest
# first, for whoever brought them back with the State filter.
_LIVE = (0, 'Live now')
_NEXT = (1, 'Up next')
_NO_PLAYERS = (2, 'Waiting for players')
_DONE = (3, 'Finished')

_ROLES = (
    # (row key, singular the board's handlers take, label)
    ('commentators', 'commentator', 'Commentary'),
    ('trackers', 'tracker', 'Tracking'),
)


def player_status(row: dict) -> tuple[str, str]:
    """``(label, chip tone)`` for the Status cell, in a player's words.

    "To confirm" and "No result" are staff's to-do list, not something a player
    can act on, so a recorded result reads as Finished until it's official.
    """
    state = row.get('state')
    if state == 'Confirmed':
        return 'Confirmed', 'ok'
    if state == 'Finished':
        return 'Finished', 'neutral'
    if state == 'Started':
        return 'Live', 'live'
    if not row.get('players'):
        return 'Waiting for players', 'neutral'
    if state == 'Checked In':
        return 'Checked in', 'neutral'
    if row.get('is_overdue'):
        return 'Late', 'pending'
    return 'Scheduled', 'neutral'


def _group(row: dict) -> tuple[int, str]:
    state = row.get('state')
    if state in ('Finished', 'Confirmed'):
        return _DONE
    if state == 'Started':
        return _LIVE
    if not row.get('players'):
        return _NO_PLAYERS
    return _NEXT


def player_crew(row: dict, discord_id) -> list[dict]:
    """One entry per crew role the tournament uses, with the viewer's place in it.

    ``count`` is approved crew over need: someone waiting on approval hasn't
    filled the slot (the rule the Needs crew switch uses). ``mine`` is
    ``'approved'``, ``'waiting'`` or None; ``can_sign_up`` mirrors the gate the
    old crew cell used (``match_slots.CREW_CAN_SIGN_UP_JS``), and ``ack_idx`` is
    set when the viewer is approved but hasn't confirmed they'll cover it.
    ``unavailable`` is why there's no button to press. ``people`` are the names, the viewer's followed by "(you)", and ``empty`` is
    the "No signups yet" line while the role can still be filled.
    """
    wanted = row.get('crew_wanted') or {}
    required = row.get('crew_required') or {}
    open_ = bool(row.get('crew_signup_open'))
    plays = bool(discord_id) and any(
        str(p.get('discord_id')) == str(discord_id) for p in row.get('players') or [])
    roles = []
    for key, singular, label in _ROLES:
        people = row.get(key) or []
        if wanted.get(key) is False or not (wanted.get(key) or people):
            continue
        need = required.get(key, 1) or 0
        approved = sum(1 for p in people if p.get('approved'))
        mine, ack_idx = None, None
        if discord_id:
            for idx, person in enumerate(people):
                if str(person.get('discord_id')) == str(discord_id):
                    mine = 'approved' if person.get('approved') else 'waiting'
                    if person.get('approved') and not person.get('acknowledged'):
                        ack_idx = idx
                    break
        roles.append({
            'key': key,
            'singular': singular,
            'label': label,
            'count': f'{approved}/{need}' if need else str(approved),
            'short': open_ and approved < need,
            'mine': mine,
            'can_sign_up': bool(discord_id) and open_ and mine is None and not plays,
            'unavailable': _unavailable(mine, open_, plays),
            'can_withdraw': mine is not None,
            'ack_idx': ack_idx,
            'people': crew_people(people, discord_id, waiting_note='waiting for approval'),
            'empty': open_ and not people,
        })
    return roles


def _unavailable(mine, open_: bool, plays: bool) -> str:
    """Why the viewer has no crew button on a role, or '' when they have one.

    The line still draws a disabled Sign up with this beside it, so the
    button's spot never empties.
    """
    if mine is not None:
        return ''
    if plays:
        return "You're playing"
    if not open_:
        return 'Signups closed'
    return ''


def needs_my_ack(row: dict, discord_id) -> bool:
    """Whether the viewer plays this match and hasn't acknowledged it yet."""
    if not discord_id:
        return False
    return any(
        str(a.get('discord_id')) == str(discord_id) and not a.get('acknowledged')
        for a in row.get('acknowledgments') or []
    )


def decorate_player_row(row: dict, discord_id) -> dict:
    """The run sheet's display fields plus the viewer's own. Mutates ``row``."""
    decorate_run_sheet_row(row)
    label, tone = player_status(row)
    row['_status'] = label
    row['_tone'] = tone
    row['_roles'] = player_crew(row, discord_id)
    row['_role_by'] = {r['key']: r for r in row['_roles']}
    row['_ack'] = needs_my_ack(row, discord_id)
    row['_mine'] = bool(discord_id) and any(
        str(p.get('discord_id')) == str(discord_id) for p in row.get('players') or [])
    mark_my_late(row, discord_id)
    return row


def keep_own_late_only(rows: list, discord_id) -> None:
    """Drop every running-late entry but the viewer's own. Mutates ``rows``.

    A heads-up is between a player and staff: the opponent's copy of the row,
    and everyone's on Event > Schedule, must not carry it to the browser.
    """
    me = str(discord_id or '')
    for row in rows:
        ids = {p.get('user_id') for p in row.get('players') or []
               if me and str(p.get('discord_id')) == me}
        row['late'] = [entry for entry in row.get('late') or [] if entry.get('user_id') in ids]


def mark_my_late(row: dict, discord_id) -> None:
    """The viewer's own running-late state on their match. Mutates ``row``.

    ``_my_late`` is the "~20 min" tag once they've said so, and the Status chip
    says it in their words; ``_can_late`` offers the button, from half an hour
    before the match until check-in (``late_window``, worked out server-side).
    """
    me = next((p for p in row.get('players') or []
               if discord_id and str(p.get('discord_id')) == str(discord_id)), None)
    mine = me and next(
        (entry for entry in row.get('late') or [] if entry.get('user_id') == me.get('user_id')), None)
    row['_my_late'] = mine['tag'] if mine else ''
    row['_can_late'] = bool(
        me and not mine and row.get('late_window') and row.get('state') == 'Scheduled'
        and not row.get('is_racetime'))
    if mine:
        row['_status'] = f"Running {mine['tag']}"
        row['_tone'] = 'pending'


def arrange_player_run_sheet(rows: list, discord_id) -> list:
    """Decorate, group and order ``rows``; mark the first row of each group."""
    for row in rows:
        decorate_player_row(row, discord_id)

    def key(row):
        order, _ = _group(row)
        ts = row.get('scheduled_ts') or 0
        return (order, -ts if order == _DONE[0] else ts)

    arranged = sorted(rows, key=key)
    previous = None
    for row in arranged:
        _, header = _group(row)
        row['_group'] = header
        row['_group_start'] = header != previous
        previous = header
    return arranged


_WHEN = r'''<span v-if="props.row._time" class="run-sheet__time" :class="props.row._status === 'Late' ? 'st-pending' : ''">{{ props.row._time }}</span>
<span v-else class="run-sheet__day">No time</span>
<span v-if="props.row._day && props.row._day !== 'Today'" class="run-sheet__day">{{ props.row._day }}</span>''' + HELD_TAG

_TITLE = "props.row._who || (props.row.bracket ? props.row.bracket.name : props.row.title)"

# One role: the count, the viewer's own control in a tray of its own, then the
# names. Every role draws a button, a disabled Sign up with the reason beside it
# when there's nothing to press, and the cell is top-aligned, so the button sits
# in the same spot on every line. In the tray the negative action sits left of
# the positive one. The phone card has no
# column header above it, so it names the role; the desktop cell sits under a
# Commentators or Trackers header and doesn't need to. Every button stops the
# click so pressing it doesn't also open the details sheet.
_ROLE = r'''<span class="run-sheet__crew" :class="c.short ? 'st-pending' : ''">__LABEL__<b>{{ c.count }}</b><template v-if="c.short"> · needs crew</template></span>
<div class="wiz-crew-action">
<q-btn v-if="c.can_withdraw" outline no-caps no-wrap color="negative" dense icon="undo" :label="__WITHDRAW__"
       :aria-label="'Withdraw as ' + c.singular"
       @click.stop="$parent.$emit('undo_' + c.singular, props.row)" />
<q-btn v-else-if="c.can_sign_up" outline no-caps no-wrap color="primary" dense icon="assignment_ind" :label="__SIGN_UP__"
       :aria-label="'Sign up as ' + c.singular"
       @click.stop="$parent.$emit('signup_' + c.singular, props.row)" />
<q-btn v-if="c.ack_idx !== null" outline no-caps no-wrap color="primary" dense icon="check" label="Confirm"
       @click.stop="$parent.$emit('acknowledge_' + c.singular, { row: props.row, idx: c.ack_idx })">
    <q-tooltip>Confirm you can cover this {{ c.singular }} slot</q-tooltip>
</q-btn>
<template v-if="!c.can_withdraw && !c.can_sign_up">
<q-btn outline no-caps no-wrap dense disable icon="assignment_ind" :label="__SIGN_UP__" />
<span v-if="c.unavailable" class="wiz-crew-why st-neutral">{{ c.unavailable }}</span>
</template>
</div>
''' + CREW_NAMES

_ROLE_CELL = _ROLE.replace('__LABEL__', '').replace(
    '__WITHDRAW__', "'Withdraw'").replace('__SIGN_UP__', "'Sign up'")
_ROLE_CARD = _ROLE.replace('__LABEL__', '{{ c.label }} ').replace(
    '__WITHDRAW__', "'Withdraw as ' + c.singular").replace('__SIGN_UP__', "'Sign up as ' + c.singular")

_ACK = r'''<q-btn v-if="props.row._ack" unelevated no-caps no-wrap color="primary" dense label="Acknowledge"
       @click.stop="$parent.$emit('acknowledge_match', props.row)">
    <q-tooltip>Confirm you've seen this match and will play it</q-tooltip>
</q-btn>'''

# The player's own heads-up, beside Acknowledge: the button while they can
# still say it, then Undo once the chip says they did.
# On the phone card the late chip sits with its Undo rather than in the top line,
# where its length squeezed the names to a letter a line.
_LATE_CHIP = r'''<div v-if="props.row._my_late" class="player-sheet__late"><span class="wiz-chip wiz-chip--pending">{{ props.row._status }}</span>
<q-btn flat no-caps no-wrap color="primary" dense label="Undo"
       aria-label="Undo running late" @click.stop="$parent.$emit('take_back_late', props.row)" /></div>'''
_LATE_UNDO = r'''<q-btn v-if="props.row._my_late" flat no-caps no-wrap color="primary" dense label="Undo"
       aria-label="Undo running late" @click.stop="$parent.$emit('take_back_late', props.row)" />'''
_LATE = r'''<q-btn v-if="props.row._can_late" outline no-caps no-wrap color="primary" dense icon="schedule"
       label="Running late" @click.stop="$parent.$emit('running_late', props.row)">
    <q-tooltip>Tell staff on duty you're on your way</q-tooltip>
</q-btn>'''

_BELL = r'''<q-btn :icon="props.row._watching ? 'notifications' : 'notifications_none'"
       :color="props.row._watching ? 'primary' : 'grey-8'" flat round dense
       @click.stop="$parent.$emit('toggle_watch', props.row)"
       :aria-label="props.row._watching ? 'Stop watching this match' : 'Watch this match for Discord updates'">
    <q-tooltip>{{ props.row._watching ? 'Stop watching this match' : 'Watch this match for Discord updates' }}</q-tooltip>
</q-btn>'''

_ASK = "props.row._can_reschedule && props.row.state === 'Scheduled'"

_HAS_MENU = ("props.row._mine && ((" + _ASK + ") || props.row._reschedule_pending"
             " || props.row._hard_offered || (" + STREAM_VOLUNTEER_ACTIONABLE + "))")

# Every row has one, for Copy link; the rest is the viewer's own match only. A
# state that can't be changed from here (a request waiting on staff, settings
# staff fixed) is a disabled item rather than a missing one, so the menu still
# says where things stand.
_MENU = r'''<q-btn flat round dense icon="more_vert" color="grey-8"
       :aria-label="'More for ' + (__TITLE__)" @click.stop>
    <q-menu auto-close anchor="bottom right" self="top right">
        <q-list style="min-width: 240px">
            <template v-if="__HAS__">
            <q-item v-if="__ASK__" clickable @click.stop="$parent.$emit('request_reschedule', props.row)">
                <q-item-section avatar><q-icon name="edit_calendar" /></q-item-section>
                <q-item-section><q-item-label>Ask to change</q-item-label>
                    <q-item-label caption>Ask staff to move it or call it off</q-item-label></q-item-section>
            </q-item>
            <q-item v-else-if="props.row._reschedule_pending" disable>
                <q-item-section avatar><q-icon name="edit_calendar" /></q-item-section>
                <q-item-section><q-item-label>Change requested</q-item-label>
                    <q-item-label caption>Waiting on staff</q-item-label></q-item-section>
            </q-item>
            <template v-if="props.row._hard_offered">
                <q-item v-if="props.row._hard_override" disable>
                    <q-item-section avatar><q-icon name="gavel" /></q-item-section>
                    <q-item-section><q-item-label>{{ props.row._hard_override_name }}</q-item-label>
                        <q-item-label caption>Staff set these settings</q-item-label></q-item-section>
                </q-item>
                <q-item v-else-if="props.row._hard_agreed && props.row._hard_locked" disable>
                    <q-item-section avatar><q-icon name="bolt" /></q-item-section>
                    <q-item-section><q-item-label>Playing {{ props.row._hard_name }}</q-item-label>
                        <q-item-label caption>This match rolled it</q-item-label></q-item-section>
                </q-item>
                <q-item v-else-if="!props.row._hard_locked" clickable
                        @click.stop="$parent.$emit('open_hard_preset', props.row)">
                    <q-item-section avatar><q-icon name="bolt" /></q-item-section>
                    <q-item-section>
                        <q-item-label>{{ props.row._hard_agreed ? 'Playing ' + props.row._hard_name
                            : (props.row._hard_opted_in ? 'Opted in, waiting' : 'Harder settings') }}</q-item-label>
                        <q-item-label caption>{{ props.row._hard_opted_in && !props.row._hard_agreed
                            ? 'Nobody is told unless everyone opts in'
                            : 'Play on ' + props.row._hard_name + ' if everyone agrees' }}</q-item-label>
                    </q-item-section>
                </q-item>
            </template>
            <q-item v-if="__STREAM__" clickable @click.stop="$parent.$emit('toggle_stream_volunteer', props.row)">
                <q-item-section avatar><q-icon :name="props.row._stream_volunteer ? 'videocam_off' : 'videocam'" /></q-item-section>
                <q-item-section>
                    <q-item-label>{{ props.row._stream_volunteer ? 'Withdraw stream offer' : 'Offer for stream' }}</q-item-label>
                    <q-item-label caption>{{ props.row._stream_volunteer ? 'You offered this match for stream'
                        : "Tell staff you're happy to be on stream" }}</q-item-label>
                </q-item-section>
            </q-item>
            <q-separator />
            </template>
            <q-item clickable :data-wiz-copy-link="'match-' + props.row.id">
                <q-item-section avatar><q-icon name="link" /></q-item-section>
                <q-item-section><q-item-label>Copy link</q-item-label></q-item-section>
            </q-item>
        </q-list>
    </q-menu>
</q-btn>'''.replace('__HAS__', _HAS_MENU).replace('__ASK__', _ASK).replace(
    '__STREAM__', STREAM_VOLUNTEER_ACTIONABLE).replace('__TITLE__', _TITLE)

_STAGE = r'''<a v-if="props.row._stage && props.row.stage_url" :href="props.row.stage_url" target="_blank" rel="noopener noreferrer"
   class="table-link" @click.stop>{{ props.row._stage }}</a>
<span v-else-if="props.row._stage">{{ props.row._stage }}</span>
<span v-else class="run-sheet__none">None</span>'''

_BODY_SLOT = r'''
<q-tr v-if="props.row._group_start" class="run-sheet__group-row">
    <q-td colspan="100%" class="run-sheet__group">{{ props.row._group }}</q-td>
</q-tr>
<q-tr :props="props" class="run-sheet__row player-sheet__row" :class="[props.row._flash ? 'wiz-row-flash' : '', props.row._removed ? 'wiz-row-removed' : '']" :inert="props.row._removed ? '' : null" :data-wiz-row="props.row.id"
      @click="$parent.$emit('open_match', props.row.id)">
    <q-td v-for="col in props.cols" :key="col.name" :props="props"
          :class="col.name === 'commentators' || col.name === 'trackers' ? 'player-sheet__crew-cell' : ''">
        <template v-if="col.name === 'scheduled_at'">''' + _WHEN + '''</template>
        <template v-else-if="col.name === 'match'">
            <button type="button" class="run-sheet__open" @click.stop="$parent.$emit('open_match', props.row.id)">
                {{ ''' + _TITLE + ''' }}</button>
            <span class="run-sheet__meta" :title="props.row.tournament">{{ props.row.tournament_short || props.row.tournament }}<template v-if="props.row.bracket"> · {{ props.row.bracket.name }}<template v-if="props.row.bracket.game"> · Game {{ props.row.bracket.game }}</template></template></span>
        </template>
        <template v-else-if="col.name === 'stage'">''' + _STAGE + '''</template>
        <template v-else-if="col.name === 'commentators' || col.name === 'trackers'">
            <div v-for="c in (props.row._role_by[col.name] ? [props.row._role_by[col.name]] : [])" :key="c.key"
                 class="player-sheet__role">''' + _ROLE_CELL + '''</div>
        </template>
        <template v-else-if="col.name === 'status'">
            <div class="player-sheet__status">
                <span class="wiz-chip" :class="'wiz-chip--' + props.row._tone">{{ props.row._status }}</span>''' + _LATE_UNDO + _ACK + _LATE + '''
            </div>
        </template>
        <template v-else-if="col.name === 'watch'">''' + _BELL + '''</template>
        <template v-else-if="col.name === 'my_actions'">''' + _MENU + '''</template>
        <template v-else>{{ col.value }}</template>
    </q-td>
</q-tr>
'''

# Phone: the names are the button that opens the sheet; the bell and the crew
# buttons sit beside it rather than inside it (a button can't hold buttons).
_ITEM_SLOT = r'''
<div class="run-sheet__item">
    <div v-if="props.row._group_start" class="run-sheet__group">{{ props.row._group }}</div>
    <div class="player-sheet__card" :class="[props.row._flash ? 'wiz-row-flash' : '', props.row._removed ? 'wiz-row-removed' : '']" :inert="props.row._removed ? '' : null" :data-wiz-row="props.row.id">
        <div class="player-sheet__top">
            <button type="button" class="player-sheet__open" @click="$parent.$emit('open_match', props.row.id)">
                <span class="run-sheet__when">''' + _WHEN + '''</span>
                <span class="run-sheet__main">
                    <span class="run-sheet__who">{{ ''' + _TITLE + ''' }}</span>
                    <span class="run-sheet__meta" :title="props.row.tournament">{{ props.row.tournament_short || props.row.tournament }}<template v-if="props.row._stage"> · {{ props.row._stage }}</template></span>
                </span>
                <span v-if="props.row._status !== 'Scheduled' && !props.row._my_late" class="wiz-chip" :class="'wiz-chip--' + props.row._tone">{{ props.row._status }}</span>
            </button>''' + _BELL + '''__MENU__
        </div>
        <div v-if="props.row._ack || props.row._can_late || props.row._my_late || props.row._roles.length" class="player-sheet__acts">''' + _LATE_CHIP + _ACK + _LATE + '''
            <div v-for="c in props.row._roles" :key="c.key" class="player-sheet__role">''' + _ROLE_CARD + '''</div>
        </div>
    </div>
</div>
'''


def register_player_run_sheet_slots(table, *, menu: bool = False) -> None:
    """The player run sheet's desktop ``body`` and phone ``item`` slots.

    ``menu`` puts the ⋮ on the phone card as well; it's set when the board has
    the ``PLAYER_MENU_COLUMN`` that draws it on desktop.
    """
    table.add_slot('body', _BODY_SLOT)
    table.add_slot('item', _ITEM_SLOT.replace('__MENU__', _MENU if menu else ''))
