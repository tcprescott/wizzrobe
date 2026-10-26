"""Grid (mobile) slot builder for the match table.

Below Quasar's ``lt.md`` breakpoint ``ui.table`` renders the ``item`` slot per
row instead of columns. This module builds a single purpose-built match card for
that mode, driven by the same server-injected board-kind / capability booleans
and the current user's discord id the column slots use (``match_slots.py``).

The card is deliberately bespoke (not a generic ``label: value`` loop): a
headline row (scheduled time + compact state chip), a players line (ack icons,
``(auto)`` markers, winner emphasis, stations, self-ack), the dispute flag as
text, a muted caption (tournament + ``#id`` edit link), ``v-if``-gated detail
rows that render nothing when empty (commentators, trackers, broadcasters, stage, seed,
comment), and a single top-bordered actions row (lifecycle button, Assign
Stations, stream-volunteer and watch toggles).

Two orders exist. By default the actions row is last, below every detail —
right for a board that is read more than it is acted on. With
``actions_first=True`` (the proctor board) it sits directly under the player
names so the next action is reachable without scrolling past the metadata, and
carries ``mgc-actions--first`` to move its divider from top to bottom.

Styling lives entirely in ``.match-grid-card`` / ``.mgc-*`` (``styles.css``) so
light/dark parity is automatic — no inline colors here. The template is
assembled from plain (non-f) string fragments; the server-side values are
substituted via ``__IA__`` / ``__RUN__`` / ``__CONFIRM__`` / ``__CREW__`` /
``__STREAM__`` / ``__DID__`` / ``__WATCH__`` / ``__VOL__`` / ``__VOLACT__`` /
``__HARD__`` /
``__ACTCLS__`` placeholders (the same technique ``match_slots.py`` uses), which
keeps every literal Vue ``{{ }}`` unescaped and the braces valid.

Frozen event contract (payload shapes the handlers in ``match_handlers.py``
depend on — do not change):
    edit_match                     -> { row: props.row }
    acknowledge_match              -> props.row
    signup_/undo_commentator|tracker|broadcaster -> props.row
    acknowledge_/view_/toggle_commentator|tracker|broadcaster -> { row: props.row, idx }
    seat/start/finish/confirm      -> { key: props.row.id }
    edit_result                    -> { key: props.row.id }
    roll                           -> { key: props.row.id }   (+ _generating_seed)
    set_stage                      -> { key: props.row.id, stage: <id|'candidate'|null> }
    assign_stations                -> { row: props.row }
    toggle_watch                   -> props.row
    toggle_stream_volunteer        -> props.row
    open_hard_preset               -> props.row
"""

from theme.busy import slot_busy_template
from theme.tables.match_access import MatchBoardAccess
from theme.tables.match_slots import (
    CREW_CAN_SIGN_UP_JS,
    CREW_EMPTY_JS,
    MY_ACTIONS_COLUMN,
    SEED_ROLLABLE,
    STAGE_VALUE_JS,
    STREAM_VOLUNTEER_ACTIONABLE,
    crew_signup_section,
    crew_wanted_js,
    seed_value_template,
)

# --- Headline: scheduled time (large) + compact state chip -----------------

_STATE_CHIP = '''
        <span class="mgc-state">
            <span v-if="props.row.state === 'Confirmed'" class="wiz-chip wiz-chip--ok">
                <q-icon name="verified" size="14px" />Confirmed</span>
            <span v-else-if="props.row.state === 'Finished'" class="wiz-chip wiz-chip--pending">
                <q-icon name="flag" size="14px" />Finished</span>
            <span v-else-if="props.row.state === 'Started'" class="wiz-chip wiz-chip--live">
                <q-icon name="play_arrow" size="14px" />Started</span>
            <span v-else-if="props.row.state === 'Checked In'" class="wiz-chip wiz-chip--neutral">
                <q-icon name="check" size="14px" />Checked In</span>
            <span v-else class="wiz-chip wiz-chip--neutral">
                <q-icon name="schedule" size="14px" />{{ props.row.state || 'Scheduled' }}</span>
            <span v-if="props.row.state_time" class="cell-timestamp">{{ props.row.state_time }}</span>
        </span>'''


# --- Players line ----------------------------------------------------------

_PLAYERS = '''
        <div class="mgc-players">
            <template v-for="(player, idx) in props.row.players">
                <div class="mgc-player">
                    <q-icon v-if="props.row.acknowledgments && props.row.acknowledgments[idx] && props.row.acknowledgments[idx].acknowledged"
                            name="how_to_reg" class="st-ok" size="xs">
                        <q-tooltip>Confirmed they're playing{{ props.row.acknowledgments[idx].ts ? ' — ' + props.row.acknowledgments[idx].ts : '' }}. Not a check-in.</q-tooltip>
                    </q-icon>
                    <q-icon v-else-if="props.row.acknowledgments && props.row.acknowledgments[idx]"
                            name="person_outline" class="st-pending" size="xs">
                        <q-tooltip>Hasn't confirmed they're playing</q-tooltip>
                    </q-icon>
                    <span :class="player.finish_rank === 1 ? 'st-ok-strong' : ''">
                        {{ player.name }}<span v-if="player.station" class="wiz-chip wiz-chip--neutral q-ml-xs">
                            <q-icon name="chair" size="12px" />{{ player.station }}</span>
                    </span>
                    <q-btn v-if="player.ada" icon="accessible" color="primary" size="sm" dense flat round
                           class="wiz-tap-24"
                           @click.stop="$parent.$emit('show_accommodation', {name: player.name, note: player.ada_note})" aria-label="ADA accommodation arranged. Tap for staff notes.">
                        <q-tooltip>ADA accommodation arranged. Tap for staff notes.</q-tooltip>
                    </q-btn>
                    <span v-if="player.finish_rank === 1" class="wiz-chip wiz-chip--ok q-ml-xs">
                        <q-icon name="emoji_events" size="12px" />Winner</span>
                    <span v-if="props.row.acknowledgments && props.row.acknowledgments[idx] && props.row.acknowledgments[idx].acknowledged && props.row.acknowledgments[idx].auto"
                          class="st-neutral italic-note"> (auto)</span>
                    <q-btn v-if="!__IA__ && props.row.acknowledgments && props.row.acknowledgments[idx] && !props.row.acknowledgments[idx].acknowledged && props.row.acknowledgments[idx].discord_id && props.row.acknowledgments[idx].discord_id == __DID__"
                           icon="check" color="primary" dense flat no-caps no-wrap label="Acknowledge"
                           @click="$parent.$emit('acknowledge_match', props.row)">
                        <q-tooltip>Confirm you've seen this match and will play it</q-tooltip>
                    </q-btn>
                </div>
            </template>
        </div>'''


# --- Dispute flag ----------------------------------------------------------

# Mirrors the "Needs review" chip in the desktop State cell. Sits directly under
# the player names: "an admin should look at this" outranks every other line on
# the card. The desktop hangs the explanation on a q-tooltip, which is
# unreachable on a touch screen, so here it renders as text.
_REVIEW_DETAIL = '''
        <div class="mgc-detail" v-if="props.row.needs_review">
            <span class="mgc-label">Review</span>
            <span class="mgc-detail-value">
                <span class="wiz-chip wiz-chip--pending">
                    <q-icon name="report_problem" size="14px" />Needs review</span>
                <span class="st-pending" style="flex-basis: 100%;">The proctor flagged this result. Confirming clears the flag.</span>
            </span>
        </div>'''


# --- Detail rows (each v-if-gated: empty -> renders nothing) ----------------

# Commentators / trackers / broadcasters. Row shows when the list is non-empty, or while a
# signup can still fill it (CREW_EMPTY_JS: "No signups yet", every viewer), or
# when this viewer could sign up. The viewer's own Sign up / Withdraw sits in its
# own tray above the names (crew_signup_section, shared with the desktop cell),
# so the button doesn't move as names come and go. Emits props.row for
# signup/undo and { row, idx } for view/toggle/acknowledge.
_CREW_DETAIL = (
    '''
        <div class="mgc-detail" v-if="(props.row.__KEY__ && props.row.__KEY__.length) || '''
    + CREW_EMPTY_JS.replace('__LIST__', 'props.row.__KEY__')
    + ' || (!__IA__ && ' + CREW_CAN_SIGN_UP_JS.replace('__LIST__', 'props.row.__KEY__') + ''')">
            <span class="mgc-label">__LABEL__</span>
            <span class="mgc-detail-value">'''
    + crew_signup_section('props.row.__KEY__', name_the_role=True).replace('\n', '\n        ')
    + '''
                <template v-for="(item, idx) in props.row.__KEY__">
                    <!-- For an approver the name itself is the approval toggle:
                         one full-size chip per person, outlined in the accent
                         while pending and green once approved. Tapping it opens
                         the usual approve / un-approve confirmation. -->
                    <template v-if="__CREW__">
                        <q-btn class="mgc-crew-toggle" dense no-caps no-wrap outline
                               :color="item.approved ? 'positive' : 'primary'"
                               :aria-label="(item.approved ? 'Un-approve ' : 'Approve ') + item.name"
                               @click="$parent.$emit('toggle___SING__', { row: props.row, idx })">
                            <q-icon :name="item.approved ? 'check_circle' : 'radio_button_unchecked'" size="18px" />
                            <span class="mgc-crew-toggle-name">{{ item.name }}</span>
                            <q-icon v-if="item.approved && item.acknowledged" name="how_to_reg" size="16px" />
                            <q-icon v-else-if="item.approved" name="schedule" size="16px" class="st-pending" />
                        </q-btn>
                        <q-btn v-if="!__IA__ && item.approved && !item.acknowledged && item.discord_id == __DID__"
                               icon="check" color="primary" dense flat no-caps no-wrap label="Acknowledge"
                               @click="$parent.$emit('acknowledge___SING__', { row: props.row, idx })" />
                    </template>
                    <span v-else class="mgc-crew-item">
                        <q-icon v-if="item.approved && item.acknowledged" name="check_circle" class="st-ok" size="xs">
                            <q-tooltip v-if="item.ack_ts">Acknowledged {{ item.ack_ts }}</q-tooltip>
                        </q-icon>
                        <q-icon v-else-if="item.approved && !item.acknowledged" name="schedule" class="st-pending" size="xs">
                            <q-tooltip>Approved, awaiting acknowledgment</q-tooltip>
                        </q-icon>
                        <span :class="item.approved ? 'st-ok-strong' : 'st-pending'">{{ item.name }}{{ idx < props.row.__KEY__.length - 1 ? ', ' : '' }}</span>
                        <q-btn v-if="!__IA__ && item.approved && !item.acknowledged && item.discord_id == __DID__"
                               icon="check" color="primary" dense flat no-caps no-wrap label="Acknowledge"
                               @click="$parent.$emit('acknowledge___SING__', { row: props.row, idx })">
                            <q-tooltip>Confirm you can cover this __SING__ slot</q-tooltip>
                        </q-btn>
                    </span>
                </template>
            </span>
        </div>'''
)

# Stage / stage. Shows when assigned, a candidate, or an admin who can
# assign; otherwise nothing. The admin's copy is the desktop cell's select —
# stages plus the ``Candidate`` pseudo-stage — emitting
# { key: props.row.id, stage: <stage id | 'candidate' | null> }; everyone else
# reads the name, or the candidate chip while no stage is set.
_STREAM_DETAIL = '''
        <div class="mgc-detail" v-if="props.row.stage || props.row.is_stream_candidate || __STREAM__ || (props.row.stream_volunteers && props.row.stream_volunteers.length)">
            <span class="mgc-label">__LABEL__</span>
            <span class="mgc-detail-value">
                <q-select v-if="__STREAM__" :model-value="__STAGEVAL__" :options="props.row.stage_options || []"
                          @update:model-value="val => $parent.$emit('set_stage', { key: props.row.id, stage: val })"
                          dense options-dense outlined emit-value map-options
                          style="min-width: 140px;" />
                <a v-else-if="props.row.stage && props.row.stage_url" :href="props.row.stage_url" target="_blank" rel="noopener noreferrer" style="color: var(--wiz-link); text-decoration: underline;">{{ props.row.stage }}</a>
                <span v-else-if="props.row.stage">{{ props.row.stage }}</span>
                <span v-if="!__STREAM__ && props.row.is_stream_candidate && !props.row.stage" class="wiz-chip wiz-chip--candidate q-ml-xs">candidate</span>
                <a v-if="__STREAM__ && props.row.stage && props.row.stage_url" :href="props.row.stage_url"
                   target="_blank" rel="noopener noreferrer" style="color: var(--wiz-link);" class="q-ml-xs"
                   aria-label="Open the stream">
                    <q-icon name="open_in_new" size="18px" />
                </a>
                <span v-if="props.row.stream_volunteers && props.row.stream_volunteers.length"
                      class="st-neutral italic-note" style="flex-basis: 100%;">
                    Offered for stream by {{ props.row.stream_volunteers.join(', ') }} — advisory, not a booking.
                </span>
            </span>
        </div>'''

# Generated seed. Admin with a configured generator and no seed gets a Generate
# button (emitting { key: props.row.id } and flagging _generating_seed); once a
# seed exists everyone gets a truncated link. Empty for non-admins -> nothing.
# A racetime.gg room owns its own seed, so no Generate button there.
_SEED_DETAIL = '''
        <div class="mgc-detail" v-if="props.row.generated_seed || (__RUN__ && __ROLLABLE__)">
            <span class="mgc-label">__LABEL__</span>
            <span class="mgc-detail-value">
                <span v-if="props.row.seed_rolling_since && !props.row.generated_seed" class="st-pending">
                    <q-spinner size="14px" class="q-mr-xs" />{{ props.row.seed_rolling_label }}
                </span>
                <q-btn v-if="__RUN__ && __ROLLABLE__ && !props.row.generated_seed && !props.row.seed_rolling_since"
                       :disable="!!props.row._generating_seed"
                       :class="{'wiz-busy-btn': !!props.row._generating_seed}"
                       :icon="props.row._generating_seed ? undefined : 'casino'" color="primary" size="sm" dense outline
                       @click="(props.row._generating_seed = true, $parent.$emit('roll', { key: props.row.id }))">
                    ''' + slot_busy_template('props.row._generating_seed') + '''{{ props.row._generating_seed ? 'Rolling…' : 'Generate' }}
                </q-btn>
                <span v-if="props.row.seed_roll_error && !props.row.generated_seed && !props.row.seed_rolling_since"
                      class="st-pending" style="flex-basis: 100%;">
                    <q-icon name="error_outline" size="14px" class="q-mr-xs" />Last roll failed: {{ props.row.seed_roll_error }} Generate again to retry.
                </span>
                <template v-if="props.row.generated_seed">
                    __SEED_VALUE__
                </template>
                <template v-if="props.row.generated_seed && props.row.seed_dm_blocked && props.row.seed_dm_blocked.length">
                    <span class="st-pending" style="flex-basis: 100%;">
                        <q-icon name="notifications_off" size="14px" class="q-mr-xs" />Can't receive a Discord DM: {{ props.row.seed_dm_blocked.join(', ') }} — hand them the seed.
                    </span>
                    <span class="st-neutral italic-note" style="flex-basis: 100%;">
                        Shows who is unreachable, not whether a DM arrived.
                    </span>
                </template>
            </span>
        </div>'''

# Free-text comment. Only present when the row actually carries one.
_COMMENT_DETAIL = '''
        <div class="mgc-detail" v-if="props.row.comment">
            <span class="mgc-label">__LABEL__</span>
            <span class="mgc-detail-value">{{ props.row.comment }}</span>
        </div>'''


# --- Actions row: lifecycle button + Change Winner + Assign Stations + watch --
# "Change Winner" mirrors the desktop pencil, but labelled: an icon-only button
# whose meaning lives in a tooltip is unreadable on a touch screen.

# Present when this viewer has a lifecycle control to offer on this row, or the
# Finished chip to explain, or the watch toggle. Without the per-capability
# terms a crew coordinator — who can run nothing — got an empty bordered row on
# every card.
_ACTIONS_PRESENT = (
    "((__RUN__ || __CONFIRM__) && ['Scheduled', 'Checked In', 'Started', 'Finished'].includes(props.row.state))"
    " || (__IA__ && props.row.state === 'Finished')"
    " || (__RUN__ && !props.row.is_racetime && props.row.players && props.row.players.length)"
    " || __WATCH__ || __VOL__ || (__HARD__ && props.row._hard_offered)"
)

_ACTIONS = '''
        <div class="__ACTCLS__" v-if="__PRESENT__">
            <q-btn v-if="__RUN__ && props.row.state === 'Scheduled' && !props.row.is_racetime && props.row.players && props.row.players.length"
                   icon="chair" color="primary" size="md"
                   @click="$parent.$emit('seat', { key: props.row.id })">Check In</q-btn>
            <div v-else-if="__RUN__ && props.row.state === 'Scheduled' && !props.row.is_racetime" class="st-neutral italic-note">
                Awaiting players</div>
            <div v-else-if="__RUN__ && props.row.state === 'Scheduled' && props.row.is_racetime" class="st-neutral italic-note">
                Managed by racetime.gg</div>
            <q-btn v-else-if="__RUN__ && props.row.state === 'Checked In'" icon="play_arrow" color="primary" size="md"
                   @click="$parent.$emit('start', { key: props.row.id })">Start</q-btn>
            <q-btn v-else-if="__RUN__ && props.row.state === 'Started'" icon="sports_score" color="primary" size="md"
                   @click="$parent.$emit('finish', { key: props.row.id })">Finish</q-btn>
            <q-btn v-else-if="__CONFIRM__ && props.row.state === 'Finished' && props.row.has_result" icon="check_circle" color="primary" size="md"
                   @click="$parent.$emit('confirm', { key: props.row.id })">Confirm</q-btn>
            <!-- Mirrors the desktop State cell: Finished with nobody recorded is
                 not confirmable, so say so instead of offering a refusal. -->
            <span v-else-if="__CONFIRM__ && props.row.state === 'Finished'" class="wiz-chip wiz-chip--pending">
                <q-icon name="report_problem" size="14px" />No result recorded</span>
            <span v-else-if="__IA__ && props.row.state === 'Finished'" class="wiz-chip wiz-chip--pending">
                <q-icon name="flag" size="14px" />Awaiting confirmation</span>
            <q-btn v-if="__CONFIRM__ && props.row.state === 'Finished'"
                   :icon="props.row.has_result ? 'edit' : 'emoji_events'" color="primary" size="md" outline
                   @click="$parent.$emit('edit_result', { key: props.row.id })">{{ props.row.has_result ? 'Change Winner' : 'Record Winner' }}</q-btn>
            <q-btn v-if="__RUN__ && !props.row.is_racetime && props.row.players && props.row.players.length"
                   icon="chair" color="primary" size="md" outline
                   @click="$parent.$emit('assign_stations', { row: props.row })">Assign Stations</q-btn>
            <q-space />
            <q-btn v-if="__VOL__ && __VOLACT__ && props.row.players && props.row.players.some(p => p.discord_id == __DID__)"
                   :icon="props.row._stream_volunteer ? 'videocam' : 'videocam_off'"
                   :color="props.row._stream_volunteer ? 'primary' : 'grey'"
                   size="md" flat dense no-caps
                   :label="props.row._stream_volunteer ? 'Offered for stream' : 'Offer for stream'"
                   @click="$parent.$emit('toggle_stream_volunteer', props.row)" />
            <q-btn v-if="__HARD__ && props.row._hard_offered && !props.row._hard_locked && !props.row._hard_override
                         && props.row.players && props.row.players.some(p => p.discord_id == __DID__)"
                   icon="bolt"
                   :color="props.row._hard_opted_in ? 'primary' : 'grey'"
                   size="md" flat dense no-caps
                   :label="props.row._hard_agreed ? 'Playing ' + props.row._hard_name
                           : (props.row._hard_opted_in ? 'Opted in — waiting' : 'Harder settings')"
                   @click="$parent.$emit('open_hard_preset', props.row)" />
            <span v-else-if="__HARD__ && props.row._hard_offered && props.row._hard_override
                             && props.row.players && props.row.players.some(p => p.discord_id == __DID__)"
                  class="wiz-chip wiz-chip--candidate">
                <q-icon name="gavel" size="14px" />{{ props.row._hard_override_name }}</span>
            <span v-else-if="__HARD__ && props.row._hard_offered && props.row._hard_agreed && props.row._hard_locked
                             && props.row.players && props.row.players.some(p => p.discord_id == __DID__)"
                  class="wiz-chip wiz-chip--confirmed">
                <q-icon name="bolt" size="14px" />{{ props.row._hard_name }}</span>
            <q-btn v-if="__VOL__ && props.row._can_reschedule && props.row.state === 'Scheduled'
                         && props.row.players && props.row.players.some(p => p.discord_id == __DID__)"
                   icon="edit_calendar" color="grey" size="md" flat dense no-caps
                   label="Ask to change"
                   @click="$parent.$emit('request_reschedule', props.row)" />
            <span v-else-if="__VOL__ && props.row._reschedule_pending" class="wiz-chip wiz-chip--pending">
                <q-icon name="edit_calendar" size="14px" />Change requested</span>
            <q-btn v-if="__WATCH__" :icon="props.row._watching ? 'notifications' : 'notifications_none'"
                   :color="props.row._watching ? 'primary' : 'grey'" size="md" flat round
                   @click="$parent.$emit('toggle_watch', props.row)"
                   :aria-label="props.row._watching ? 'Stop watching this match' : 'Watch this match for Discord updates'">
                <q-tooltip>{{ props.row._watching ? 'Stop watching this match' : 'Watch this match for Discord updates' }}</q-tooltip>
            </q-btn>
        </div>'''


_CARD_OPEN = '''<div class="match-grid-card q-pa-md q-mb-sm" :class="[props.row._flash ? 'wiz-row-flash' : '', props.row._removed ? 'wiz-row-removed' : '']" :inert="props.row._removed ? '' : null" :data-wiz-row="props.row.id" style="width: 100%; box-sizing: border-box;">'''
_CARD_CLOSE = '''
    </div>'''


def render_grid_slot(table, columns, *, admin_controls: bool, access: MatchBoardAccess, discord_id,
                     has_edit: bool = True, actions_first: bool = False) -> None:
    """Register the purpose-built grid ``item`` slot on ``table``.

    Only fields that appear as (non-hidden) columns are rendered, preserving
    parity with the desktop table on each page. The server-side flags — the board
    kind (``admin_controls``) and one per capability in ``access`` — are baked
    into the template so client-side branches collapse to constants.
    ``has_edit`` mirrors the caller's edit callback: without one the caption id
    renders as plain text instead of a dead link. ``actions_first`` lifts the
    lifecycle row directly under the player names (the proctor board's order).
    """
    present = {c.get('name', '') for c in columns if not c.get('hidden')}
    labels = {c.get('name', ''): c.get('label', c.get('name', '')) for c in columns}

    ia = 'true' if admin_controls else 'false'
    did = f"'{discord_id}'" if discord_id else 'null'
    # The desktop's one "Your actions" cell stands for all four of the player's
    # own controls, so the card offers each of them wherever that column shows.
    mine = MY_ACTIONS_COLUMN in present
    watch_js = 'true' if 'watch' in present or mine else 'false'
    volunteer_js = 'true' if 'stream_volunteer' in present or mine else 'false'
    hard_js = 'true' if 'hard_preset' in present or mine else 'false'

    # Headline (scheduled time + optional state chip)
    headline = (
        '\n        <div class="mgc-headline">'
        # Mirrors the desktop scheduled_at cell's overdue emphasis (match_slots).
        '<span class="mgc-time" :class="props.row.is_overdue ? \'st-pending\' : \'\'">{{ props.row.scheduled_at }}'
        '<q-icon v-if="props.row.is_overdue" name="schedule" class="st-pending q-ml-xs" size="xs">'
        '<q-tooltip>Past its scheduled time and not checked in</q-tooltip>'
        '</q-icon></span>'
        + (_STATE_CHIP if 'state' in present else '')
        + '\n        </div>'
    )

    # Players line, then the dispute flag (only when the row carries one).
    players = _PLAYERS if 'players' in present else ''
    review = _REVIEW_DETAIL if 'state' in present else ''

    # Caption (tournament + #id edit link)
    caption_inner = ''
    if 'tournament' in present:
        caption_inner += (
            '<span v-if="props.row.tournament">{{ props.row.tournament }}'
            '<template v-if="props.row.set_label"> · {{ props.row.set_label }}</template></span>'
        )
        # Same bracket link as the desktop Tournament cell (see match_slots).
        caption_inner += (
            '<a v-if="props.row.bracket" href="#" class="mgc-bracket-link q-ml-sm"'
            ' @click.prevent="$parent.$emit(\'open_bracket\', props.row)">'
            '<q-icon name="account_tree" size="14px" class="q-mr-xs" />'
            '{{ props.row.bracket.name }}'
            '<template v-if="props.row.bracket.game"> · Game {{ props.row.bracket.game }}'
            '<template v-if="props.row.bracket.best_of > 1"> of {{ props.row.bracket.best_of }}</template></template>'
            '<template v-if="props.row.bracket.standing"> · {{ props.row.bracket.standing }}</template>'
            '</a>'
        )
    # The card's edit affordance mirrors the desktop first cell: a pencil where
    # the board declares an `edit` column, the plain number where it declares
    # `id` (the proctor's, who calls matches out by number).
    edit_js = access.edit_slot_js()
    if 'edit' in present and has_edit:
        caption_inner += (
            f'<q-btn v-if="{edit_js}" flat round dense icon="edit" color="primary" size="sm" class="q-ml-xs"'
            ' aria-label="Edit this match"'
            ' @click="$parent.$emit(\'edit_match\', { row: props.row })">'
            '<q-tooltip>Edit this match</q-tooltip></q-btn>'
        )
    if 'id' in present:
        if has_edit:
            caption_inner += (
                f'<a v-if="{edit_js}" href="#" class="mgc-id-link q-ml-sm"'
                ' @click="$parent.$emit(\'edit_match\', { row: props.row })">#{{ props.row.id }}</a>'
                '<span v-else class="q-ml-sm">#{{ props.row.id }}</span>'
            )
        else:
            caption_inner += '<span class="q-ml-sm">#{{ props.row.id }}</span>'
    caption = f'\n        <div class="mgc-caption">{caption_inner}</div>' if caption_inner else ''

    # Detail rows (order: commentators, trackers, broadcasters, stage, seed, comment)
    details = ''
    for role in ('commentators', 'trackers', 'broadcasters'):
        if role in present:
            details += (
                _CREW_DETAIL
                .replace('__WANTED__', crew_wanted_js(role))
                .replace('__KEY__', role)
                .replace('__SING__', role[:-1])
                .replace('__LABEL__', labels.get(role, role))
            )
    if 'stage' in present:
        details += _STREAM_DETAIL.replace('__LABEL__', labels.get('stage', 'Stage'))
    if 'generated_seed' in present:
        # "Generated Seed" (the desktop column label) would widen the shared label column.
        details += _SEED_DETAIL.replace('__LABEL__', 'Seed')
    details += _COMMENT_DETAIL.replace('__LABEL__', labels.get('comment', 'Comment'))
    # One grid for every detail row, so the label column is as wide as the
    # longest label actually shown rather than a fixed width that starves the
    # values on a phone.
    details = f'\n        <div class="mgc-details">{details}\n        </div>'

    # Actions (only worth emitting when an admin or the watch toggle is in play).
    # Hoisted above the caption/detail rows when the caller asked for it, with a
    # sibling class that flips the row's divider from top to bottom.
    actions = (_ACTIONS.replace('__PRESENT__', _ACTIONS_PRESENT)
               if (admin_controls or mine or 'watch' in present
                   or 'stream_volunteer' in present or 'hard_preset' in present)
               else '')
    act_cls = ('mgc-actions mgc-actions--first row items-center' if actions_first
               else 'mgc-actions row items-center')

    template = (
        _CARD_OPEN + headline + players + review + actions + caption + details + _CARD_CLOSE
        if actions_first else
        _CARD_OPEN + headline + players + review + caption + details + actions + _CARD_CLOSE
    )

    template = (
        template
        # Shared with the desktop seed cell so the two boards agree on when a
        # seed can still be rolled (match_slots.SEED_ROLLABLE).
        .replace('__ROLLABLE__', SEED_ROLLABLE)
        .replace('__SEED_VALUE__', seed_value_template('props.row.generated_seed'))
        .replace('__IA__', ia)
        .replace('__RUN__', access.run_js())
        .replace('__CONFIRM__', access.confirm_js())
        .replace('__CREW__', access.crew_js())
        .replace('__STREAM__', access.stream_js())
        # Shared with the desktop stage cell so both boards read the same row
        # keys for "which stage is this on" (match_slots.STAGE_VALUE_JS).
        .replace('__STAGEVAL__', STAGE_VALUE_JS)
        .replace('__WATCH__', watch_js)
        .replace('__VOL__', volunteer_js)
        .replace('__HARD__', hard_js)
        # Shared with the desktop cell so both boards agree on when an offer can
        # still be made (match_slots.STREAM_VOLUNTEER_ACTIONABLE).
        .replace('__VOLACT__', STREAM_VOLUNTEER_ACTIONABLE)
        .replace('__DID__', did)
        .replace('__ACTCLS__', act_cls)
    )

    table.add_slot('item', template)
