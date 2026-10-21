

from dataclasses import dataclass
from typing import Callable

from nicegui import app, ui

from application.services import (
    BracketService,
    ChallongeService,
    FeatureFlagService,
    MatchHardPresetService,
    MatchRescheduleService,
    MatchService,
    RoundScheduleService,
    TournamentService,
    bracket_sources,
    get_user_from_session,
)
from application.services.match.match_reads import (
    RELATION_CREW,
    RELATION_PLAYER,
    RELATION_WATCHER,
)
from application.utils.app_links import HOME_PLAYER, HOME_SCHEDULE, home_url
from application.utils.match_labels import challonge_round_label, entrant_name
from application.utils.timezone import format_local_display
from models import FeatureFlag, RescheduleRequestKind, RescheduleRequestStatus
from pages.home_tabs.player_asks import render_opponent_requests
from pages.home_tabs.race_rooms_section import render_race_rooms
from theme.dialog.bracket_schedule_dialog import BracketScheduleDialog
from theme.dialog.bracket_source_schedule_dialog import BracketSourceScheduleDialog
from theme.dialog.challonge_schedule_dialog import ChallongeScheduleDialog
from theme.dialog.hard_preset_dialog import HardPresetDialog
from theme.dialog.match_dialog import UserMatchDialog
from theme.dialog.match_sheet import MatchSheet
from theme.dialog.reschedule_request_dialog import RescheduleRequestDialog
from theme.links import link_button
from theme.nav_badges import set_nav_count
from theme.notify import notify_error
from theme.section import section_panel
from theme.steady import steady_refreshable
from theme.tables.match import MatchTableView
from theme.tables.match_access import MatchBoardAccess
from theme.tables.match_player_run_sheet import (
    PLAYER_MENU_COLUMN,
    PLAYER_RUN_SHEET_COLUMNS,
    decorate_player_row,
)
from theme.tables.preferences import TableKeys


def _next_game_number(bracket_match, best_of: int) -> int:
    """The slot the service would allocate next — for labelling only.

    ``list_open_matches_for_user`` only returns matchups with a slot free, so the
    fallback is unreachable; it keeps the label sane rather than raising in a
    render pass if that ever changes.
    """
    taken = {g.game_number for g in bracket_match.games}
    return next((n for n in range(1, best_of + 1) if n not in taken), best_of)


def _notify_stale_schedule_link() -> None:
    """Say why a "Pick a time" button did nothing.

    A DM outlives the matchup it points at — the opponent may have booked it,
    staff may have scheduled it, the stage may have finished. Landing on a page
    where the row simply is not there reads as a broken link, so the reason is
    said out loud.
    """
    ui.notify(
        'That matchup is no longer waiting to be scheduled.', color='warning',
    )


def _report_stale_deep_link(deep_link: dict) -> None:
    """The same notice for the case where the viewer has no open matchups at all."""
    stale = [deep_link.pop(key, None) for key in ('matchup_id', 'challonge_id', 'startgg')]
    if any(v is not None for v in stale):
        _notify_stale_schedule_link()


def _booking_caption(tournaments) -> str:
    """The Waiting-on-you card's one line on what booking does.

    Booking is final, so the copy never promises a confirm step. The opponent's
    "ask staff to move it" route is mentioned only when every listed tournament
    takes reschedule requests; one that turned them off gives no such route.
    """
    caption = 'Pick a time and it’s booked; your opponent gets a message'
    if tournaments and all(getattr(t, 'allow_reschedule_requests', False) for t in tournaments):
        return caption + ' and can ask staff to move it.'
    return caption + '.'


async def _waiting_on_you(user) -> tuple[list, list, list]:
    """The player's bracket matchups with no time yet: Challonge, native, and
    every other linked bracket site (start.gg) through the shared bracket sources.

    Each source only where the community runs it: the services refuse a
    listing without their feature, and My Schedule itself isn't flag-gated.
    Challonge also needs its credentials configured.
    """
    live = await FeatureFlagService().enabled_flags()
    challonge_rows: list = []
    if FeatureFlag.CHALLONGE in live and ChallongeService.is_configured():
        challonge_rows = await ChallongeService().list_unscheduled_matches_for_user(user)
    matchups: list = []
    if FeatureFlag.BRACKETS in live:
        matchups = await BracketService().list_open_matches_for_user(user.id)
    external = await bracket_sources.list_bookable_for_user(user)
    return challonge_rows, matchups, external


def _bookable_count(challonge_rows, matchups, external=()) -> int:
    """How many of them the player can book right now.

    A Challonge row whose opponent isn't matched to an account is listed (so the
    player learns why) but isn't counted: the count asks for an action, and that
    row has none yet. Native matchups are only listed when both are linked.
    """
    linked = sum(
        1 for cm in challonge_rows
        if cm.participant1 is not None and cm.participant2 is not None
        and cm.participant1.user_id is not None and cm.participant2.user_id is not None
    )
    return linked + len(matchups) + len(external)


async def count_waiting_on_you(user) -> int:
    """The number on the My Schedule nav item: matches waiting on this player to book."""
    if user is None:
        return 0
    return _bookable_count(*await _waiting_on_you(user))


@dataclass
class _Pick:
    """One matchup waiting on the player, before it is grouped by tournament."""
    tournament_id: int
    tournament_name: str
    source: str | None
    opponent_name: str
    round_label: str
    on_click: Callable
    blocked: str | None = None
    window_text: str | None = None


def _render_picks(picks: list[_Pick]) -> None:
    """The Waiting-on-you matchups, one group per tournament.

    The tournament and its bracket-site tag are said once in the group's
    header: during a group stage a player has several matchups in the same
    tournament, and repeating its name on each row cut it to "Wizzrobe Dev
    Tourna…" on a phone. A row that can't be booked yet shows why instead of a
    greyed-out button, because a tooltip never shows on a phone.
    """
    groups: dict[tuple, list[_Pick]] = {}
    for pick in picks:
        groups.setdefault((pick.tournament_id, pick.source), []).append(pick)
    with ui.element('div').classes('wiz-pick-groups'):
        for rows in groups.values():
            with ui.element('div').classes('wiz-pick-group'):
                with ui.row().classes('wiz-pick-group__head items-center no-wrap gap-2'):
                    ui.label(rows[0].tournament_name).classes('col min-w-0')
                    if rows[0].source:
                        ui.label(rows[0].source).classes('wiz-chip wiz-chip--neutral')
                for pick in rows:
                    with ui.element('div').classes('wiz-pick-row'):
                        with ui.row().classes('items-center no-wrap gap-3 full-width'):
                            with ui.column().classes('col min-w-0 gap-0'):
                                ui.label(f'vs {pick.opponent_name}').classes('wiz-pick-row__opponent')
                                if pick.round_label:
                                    ui.label(pick.round_label).classes('text-caption text-grey-7')
                                if pick.window_text and not pick.blocked:
                                    with ui.row().classes('wiz-pick-row__window no-wrap items-start gap-1'):
                                        ui.icon('schedule', size='16px')
                                        ui.label(pick.window_text)
                            if not pick.blocked:
                                ui.button('Pick a time', on_click=pick.on_click) \
                                    .props('color=primary outline no-caps')
                        if pick.blocked:
                            with ui.row().classes('wiz-pick-row__note no-wrap items-start gap-2'):
                                ui.icon('info_outline', size='16px')
                                ui.label(pick.blocked)


def _notify_stale_hard_preset_link() -> None:
    """Say why a "Choose your settings" button did nothing.

    The DM outlives the choice it offers: the seed may have rolled, staff may
    have set the match themselves, or the roster may have changed. A page that
    opens no dialog reads as a broken app, so the reason is said aloud.
    """
    ui.notify(
        'The settings for that match are already decided.', color='warning',
    )


def _notify_missing_hard_preset_match() -> None:
    """The match a "Choose your settings" button named is gone, or never was."""
    ui.notify(
        "That match isn't on the schedule any more, so there's nothing to choose.",
        color='warning',
    )


def _notify_no_hard_preset_choice() -> None:
    """The match exists but offers this viewer no settings choice to make."""
    ui.notify(
        "That match doesn't offer you a choice of settings.", color='warning',
    )


def _notify_stale_reschedule_link() -> None:
    """Say why an "Ask again" button did nothing.

    A DM outlives the match it points at: staff may have moved it anyway, it may
    have been played, or the tournament may have stopped taking requests. Landing
    on a page with no dialog reads as a broken link, so the reason is said aloud.
    """
    ui.notify(
        'That match can no longer be changed.', color='warning',
    )


def _show_one_match_chip() -> None:
    """The board is narrowed to the DM's match. Say so, and offer the way out.

    A one-row board with no explanation reads as a board that lost rows, and a
    DM outlives the stage call it was sent about.
    """
    with ui.row().classes('items-center gap-2 q-mb-sm'):
        with ui.element('span').classes('wiz-chip wiz-chip--pending'):
            ui.icon('filter_alt', size='14px')
            ui.label('Showing one match only')
        link_button('Show all my matches', home_url(HOME_PLAYER), icon='clear', new_tab=False) \
            .props('flat dense color=primary')


def _explain_other_match(relation: str) -> None:
    """``?match=`` named a match the viewer isn't playing. Say what it is to them.

    Filtering the board to it anyway showed "No matches to show yet", which is
    what a commentator following a stage DM used to get. The board stays whole;
    crew are left to ``my_crew_tab``, which outlines and scrolls to their slot, and a
    watcher is pointed at the one board that lists a match they neither play nor crew.
    """
    if relation == RELATION_CREW:
        # The crew section owns this one: it finds the card (past or upcoming),
        # outlines it and scrolls to it, and says so if the match is over.
        return
    if relation == RELATION_WATCHER:
        with ui.row().classes('items-center gap-2 q-mb-sm'):
            with ui.element('span').classes('wiz-chip wiz-chip--neutral'):
                ui.icon('visibility', size='14px')
                ui.label("You're watching that match, not playing in it")
            link_button('Find it on the schedule', home_url(HOME_SCHEDULE), icon='event', new_tab=False) \
                .props('flat dense color=primary')
        return
    ui.notify(
        "That match isn't one of yours, so here's your whole schedule.",
        color='warning',
    )


# What each status means to the person who asked, rather than the machine key.
_REQUEST_STATUS = {
    RescheduleRequestStatus.PENDING: ('Waiting on staff', 'warning'),
    RescheduleRequestStatus.APPROVED: ('Approved', 'positive'),
    RescheduleRequestStatus.DECLINED: ('Declined', 'negative'),
    RescheduleRequestStatus.WITHDRAWN: ('You withdrew this', 'grey'),
    # Named from the reader's side: "superseded" is our word, not theirs.
    RescheduleRequestStatus.SUPERSEDED: ('Settled by another request', 'grey'),
}


def _render_request_card(row, *, on_withdraw) -> None:
    """One request, its status, and staff's reply if they left one."""
    label, colour = _REQUEST_STATUS.get(row.status, (row.status, 'grey'))
    cancel = row.kind is RescheduleRequestKind.CANCEL
    with ui.column().classes('full-width gap-1 q-my-sm'):
        with ui.row().classes('items-center gap-2 full-width'):
            ui.label(row.match.tournament.name).classes('text-bold')
            ui.badge(label, color=colour)
        ui.label(
            'Asked to call it off' if cancel
            else f'Asked to move it to {format_local_display(row.proposed_at)}'
            if row.proposed_at else 'Asked to move it, staff to pick a time'
        ).classes('text-caption text-grey-7')
        ui.label(f'Your reason: {row.reason}').classes('text-caption text-grey-7')
        if row.decision_note:
            ui.label(f'Staff said: {row.decision_note}')
        if row.status is RescheduleRequestStatus.PENDING:
            if row.opponent_agreed_at:
                ui.label('Your opponent agreed.').classes('text-caption text-positive')

            async def withdraw(_=None, request_id=row.id):
                from application.services import MatchRescheduleService

                actor = await get_user_from_session(app.storage.user)
                try:
                    await MatchRescheduleService().withdraw(request_id, actor)
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                ui.notify('Withdrawn.', color='positive')
                on_withdraw()

            ui.button('Withdraw', icon='undo', on_click=withdraw) \
                .props('flat dense color=grey size=sm')
    ui.separator()


def _round_label(bracket_match, best_of: int, number: int) -> str:
    """'Round 2' / 'Losers round 1', plus the series position for a best-of-N."""
    rnd = bracket_match.round
    base = f'Losers round {abs(rnd)}' if rnd < 0 else f'Round {rnd}'
    return f'{base} — game {number} of {best_of}' if best_of > 1 else base


async def render_player_dashboard(
    schedule: int | None = None, reschedule: int | None = None,
    match: int | None = None, hard: int | None = None,
    agree: int | None = None, book: int | None = None, comments: int | None = None,
    startgg: int | None = None,
):
    """The player's own schedule.

    ``schedule`` is a bracket matchup id arriving from the "matchup ready to
    schedule" DM's button (``?schedule=<id>`` on ``/home/player``). Its dialog is
    opened once the tab has rendered, so the notification's call to action ends
    at the date/time picker instead of at a page the reader then has to search.
    ``book`` does the same for a Challonge matchup, ``startgg`` for a start.gg one.

    ``reschedule`` is a match id doing the same for the declined-request DM:
    its "Ask again" button lands on the request form for that match, because
    asking again with a different time is the actual next step after a refusal.

    ``match`` narrows the board to one match, for the stage DMs' button. It
    stays a filter rather than a dialog because "which stage am I on" is
    answered by the row itself. A match the viewer is not playing (crew and
    watchers get their own links, but an old DM or a forwarded one can still
    land here) leaves the board whole and says what the match is to them.

    ``agree`` is a reschedule request id from the opponent DM's button, opening
    that request with its Agree button.

    ``hard`` is a match id from the harder-settings DM, opening that match's
    opt-in dialog. A dialog rather than a filter, because unlike the stage the
    answer is not on the row: the row holds a small toggle whose whole meaning
    is the explanation the dialog carries.
    """
    # Always set: home applies the membership gate before any tab is built, so a
    # signed-out visitor gets the join page and never reaches this section.
    discord_id = app.storage.user.get('discord_id')
    # Resolved once for the help icons below: three of them read from the event
    # handbook, which filters by role, and each would otherwise look the viewer
    # up for itself.
    viewer = await get_user_from_session(app.storage.user)
    match_service = MatchService()
    reschedule_service = MatchRescheduleService()
    hard_preset_service = MatchHardPresetService()
    bracket_service = BracketService()

    panel = await section_panel(
        'Your matches',
        icon='sports_esports',
        subtitle='What you are playing, and anything still waiting on a time.',
        help_topics=[
            ('player-schedule', 'Your matches'),
            ('bracket-schedule', 'Picking a time for a bracket match'),
            'stream-volunteer',
            'reschedule-request',
            ('check-in', 'Checking in'),
            ('player-room', 'In the race room'),
            ('player-stage', 'Playing on a stage'),
        ],
        user=viewer,
    )
    with panel.body:
        board_match_ids = None
        # The staff-comment DM: the same one-match board, with the sheet open.
        match = match or comments
        if match:
            relation = await match_service.viewer_relation(int(match), viewer)
            if relation == RELATION_PLAYER:
                board_match_ids = [int(match)]
                _show_one_match_chip()
            else:
                _explain_other_match(relation)

        # The deep-linked matchup's opener, captured during the render below so
        # `?schedule=<id>` (native) or `?book=<id>` (Challonge) can fire the same
        # handler the button does. Per-client (a local, never module level) and
        # cleared once used, so a later refresh of the section does not re-open
        # the dialog under the reader.
        deep_link = {'matchup_id': schedule, 'challonge_id': book, 'startgg': startgg}

        # Everything waiting on the player to pick a time, in one card: native
        # bracket matchups (the *only* scheduling route in a bracket-run
        # tournament, since those turn off manual match requests) and Challonge
        # ones. Where a matchup came from is a tag on its row, not a second card
        # with the same title.
        @steady_refreshable
        async def waiting_section():
            user = await get_user_from_session(app.storage.user)
            if user is None:
                return
            challonge_rows, matchups, external = await _waiting_on_you(user)
            set_nav_count('My Schedule', _bookable_count(challonge_rows, matchups, external))
            if not challonge_rows and not matchups and not external:
                _report_stale_deep_link(deep_link)
                return

            async def after():
                # The board first: rebuilding the section deletes the dialog
                # this runs from.
                await table_view.refresh()
                waiting_section.refresh()

            openers: dict = {}
            challonge_openers: dict = {}
            external_openers: dict = {}
            bookable = _bookable_count(challonge_rows, matchups, external)
            with ui.card().classes('wiz-subcard'):
                with ui.row().classes('items-center no-wrap gap-2'):
                    ui.label('Waiting on you to pick a time').classes('wiz-subcard__title')
                    if bookable:
                        ui.badge(str(bookable), color=None).classes('wiz-nav-count')
                ui.label(_booking_caption(
                    [cm.tournament for cm in challonge_rows]
                    + [bm.bracket.tournament for bm in matchups]
                    + [x.tournament for x in external],
                )).classes('text-caption text-grey-7')
                picks: list[_Pick] = []
                round_schedule = RoundScheduleService()
                for cm in challonge_rows:
                    me_is_p1 = cm.participant1 is not None and cm.participant1.user_id == user.id
                    opponent = cm.participant2 if me_is_p1 else cm.participant1
                    opponent_name = entrant_name(opponent)
                    opponent_linked = opponent is not None and opponent.user_id is not None

                    async def do_challonge(_=None, m=cm, oname=opponent_name):
                        actor = await get_user_from_session(app.storage.user)
                        await ChallongeScheduleDialog(
                            m, actor=actor, opponent_name=oname, on_submit=after,
                        ).open()

                    if opponent_linked:
                        challonge_openers[cm.id] = do_challonge
                    picks.append(_Pick(
                        cm.tournament.id, cm.tournament.name, 'Challonge', opponent_name,
                        challonge_round_label(cm.round), do_challonge,
                        blocked=None if opponent_linked else (
                            f"Can't book yet: {opponent_name} isn't matched to a "
                            "Wizzrobe account."
                        ),
                        window_text=await round_schedule.describe_for_challonge_match(cm),
                    ))

                for x in external:
                    async def do_external(_=None, item=x):
                        actor = await get_user_from_session(app.storage.user)
                        await BracketSourceScheduleDialog(item, actor, on_submit=after).open()

                    external_openers[(x.source, x.key)] = do_external
                    picks.append(_Pick(
                        x.tournament.id, x.tournament.name, x.source_label,
                        x.opponent_name, x.round_label, do_external,
                        window_text=x.window_text,
                    ))

                for bm in matchups:
                    me_is_e1 = bm.entry1.entrant.user_id == user.id
                    opponent = bm.entry2 if me_is_e1 else bm.entry1
                    opponent_name = opponent.entrant.display_name
                    best_of = bracket_service.resolve_best_of(bm.bracket, bm)
                    number = _next_game_number(bm, best_of)

                    async def do_schedule(_=None, m=bm, oname=opponent_name,
                                          n=number, bo=best_of):
                        actor = await get_user_from_session(app.storage.user)
                        await BracketScheduleDialog(
                            m.id, actor,
                            opponent_name=oname,
                            game_number=n,
                            best_of=bo,
                            opponent_can_ask=bool(
                                m.bracket.tournament.allow_reschedule_requests
                            ),
                            tournament_name=m.bracket.tournament.name,
                            round_label=_round_label(m, bo, n),
                            tournament_id=m.bracket.tournament_id,
                            player_ids=[
                                m.entry1.entrant.user_id,
                                m.entry2.entrant.user_id,
                            ],
                            on_submit=after,
                        ).open()

                    openers[bm.id] = do_schedule
                    picks.append(_Pick(
                        bm.bracket.tournament_id, bm.bracket.tournament.name, None,
                        opponent_name, _round_label(bm, best_of, number), do_schedule,
                        window_text=await round_schedule.describe_for_bracket_match(bm),
                    ))

                _render_picks(picks)

            # The openers only hold this viewer's own open matchups — the
            # listings are already scoped to them — so an id from a forwarded
            # link cannot open someone else's dialog; it falls through to the
            # stale notice.
            startgg_openers = {k: v for (src, k), v in external_openers.items() if src == 'startgg'}
            for key, table in (
                ('matchup_id', openers), ('challonge_id', challonge_openers), ('startgg', startgg_openers),
            ):
                wanted = deep_link.pop(key, None)
                if wanted is None:
                    continue
                opener = table.get(int(wanted))
                if opener is not None:
                    await opener()
                else:
                    _notify_stale_schedule_link()

        # The player run sheet Event > Schedule uses, plus a ⋮ per line for
        # what only a player of that match can do. The details sheet a tapped
        # line opens holds the rest (seed, stations, who's confirmed).
        columns = [*PLAYER_RUN_SHEET_COLUMNS, PLAYER_MENU_COLUMN]
        sheet = MatchSheet(None, access=MatchBoardAccess(),
                           decorate=lambda row: decorate_player_row(row, discord_id))

        async def submit_match():
            dialog = UserMatchDialog(discord_id=discord_id)
            await dialog.open()

        async def get_query():
            return await match_service.get_matches_for_player(discord_id)

        # Request Match only where a request can actually be made. The dialog's
        # own dead end — "your tournaments are scheduled from their bracket" —
        # is the right message once you are in it, but a button that exists only
        # to explain that it does nothing is a button that should not be there.
        # Same list the dialog offers, so the two cannot disagree.
        can_request = bool(
            viewer is not None
            and await TournamentService().list_player_requestable(viewer)
        )

        @steady_refreshable
        async def requests_section():
            """This viewer's own reschedule requests, and what became of them.

            Cards rather than a table: three or four rows of prose, one of which
            is staff's reply, read badly in cells and would drag the column
            preferences machinery along for no gain.

            Without this a player who asked has no way to learn whether anyone
            answered — the same loop ``FeedbackService.list_mine`` closes.
            """
            if viewer is None:
                return
            rows = await reschedule_service.list_mine(viewer)
            if not rows:
                return
            with ui.card().classes('wiz-subcard'):
                ui.label('Your change requests').classes('wiz-subcard__title')
                for row in rows:
                    _render_request_card(row, on_withdraw=requests_section.refresh)

        async def _open_hard_preset(match_id: int) -> None:
            """Open the harder-settings choice for one match, from its DM."""
            if viewer is None:
                _notify_stale_hard_preset_link()
                return
            match = await match_service.get_by_id(match_id)
            if match is None:
                _notify_missing_hard_preset_match()
                return
            states = await hard_preset_service.board_states(viewer, [match_id])
            state = states.get(match_id)
            if state is None:
                _notify_no_hard_preset_choice()
                return

            async def after():
                await table_view.refresh()

            await HardPresetDialog(match, viewer, state, on_change=after).open()

        async def _open_reschedule(match_id: int) -> None:
            """Open the request form for one match, from the declined DM's button."""
            match = await match_service.get_by_id(match_id)
            if match is None or viewer is None:
                _notify_stale_reschedule_link()
                return
            if not await reschedule_service.can_request(match, viewer):
                _notify_stale_reschedule_link()
                return

            async def after():
                requests_section.refresh()
                await table_view.refresh()

            await RescheduleRequestDialog(match, viewer, on_submit=after).open()

        # Render order is what the reader owes the section, not what the section
        # holds. A matchup with no time on it is the only thing here that is
        # waiting on *them*, and it used to sit below a twelve-row board and a
        # filter card — far enough down that the "matchup ready" DM landed on a
        # page whose call to action was off-screen. Then the board, then the
        # requests they have already made and are only tracking. An open race room
        # means the match is close, so it leads.
        await render_race_rooms(viewer)
        # Agreeing changes nothing on the board, so nothing there refreshes.
        await render_opponent_requests(viewer, agree_request_id=agree)
        await waiting_section()

        table_view = MatchTableView(
            columns=columns,
            get_query=get_query,
            admin_controls=False,
            table_key=TableKeys.HOME_PLAYER_MATCHES,
            submit_match_callback=submit_match if can_request else None,
            player_discord_id=discord_id,
            storage_key='player_dashboard',
            match_ids=board_match_ids,
            on_rows_changed=sheet.sync,
            layout='player_run_sheet',
            on_open_match=sheet.open,
        )
        sheet.view = table_view
        if comments and board_match_ids:
            sheet.open_when_loaded(int(comments))

        await requests_section()
        if reschedule is not None:
            await _open_reschedule(int(reschedule))
        if hard is not None:
            await _open_hard_preset(int(hard))
        # No initial refresh here: MatchTableView._initial_load owns it, and runs
        # after the stored filters are restored rather than racing them.

