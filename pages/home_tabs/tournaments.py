"""Tournaments — where a player signs up to compete.

Signing up used to be a checkbox in a card near the bottom of the Profile tab, a
page nobody has to visit to use the app. Players who never opened it never
entered the player pool, and the only sign anything was wrong came later, when
staff went to schedule a match and found the tournament empty.

So the opt-in gets its own tab and its own card per tournament: what the
tournament is, when it runs, who else is in, and one button. Sections are
ordered by what the reader can act on — signups that are open, then what they
are already in, then the ones that have not opened or have shut.

Everything the card claims comes from ``TournamentService.list_signup_cards``,
including whether the button should be there at all, so this page and the REST
route cannot disagree about who may sign up.
"""

from dataclasses import replace
from typing import Optional

from nicegui import app, background_tasks, context, ui

from application.content import parse_blocks
from application.services import (
    FeatureFlagService,
    TournamentService,
    get_user_from_session,
)
from application.services.tournament_service import TournamentSignupCard
from application.tenant_context import is_host_mode
from application.utils.app_links import HOME_TOURNAMENTS, home_url
from application.utils.environment import host_oauth_handoff_enabled
from application.utils.safe_url import http_url_or_empty
from application.utils.timezone import format_local_date, format_local_display
from application.utils.tournament_signup import SignupWindow
from models import FeatureFlag
from pages.home_tabs._link_section import open_sign_in_first_dialog
from pages.home_tabs.challonge_link_section import CONFIG as _CHALLONGE_LINK_SECTION
from pages.home_tabs.triforce_texts import open_triforce_dialog, supporting_tournament_ids
from theme.busy import busy_click
from theme.dialog.challonge_signup_dialog import open_challonge_signup_dialog
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.empty_state import empty_panel
from theme.help import help_icon
from theme.help.render import render_blocks
from theme.notify import notify_error
from theme.page_header import page_header
from theme.steady import steady_refreshable

# The Profile's Challonge link, returning to this tab instead of the Profile so
# the player finishes signing up where they started.
CHALLONGE_LINK = replace(
    _CHALLONGE_LINK_SECTION,
    link_route=f'{_CHALLONGE_LINK_SECTION.link_route}?next={HOME_TOURNAMENTS}',
)

# Section title, and the predicate that puts a card in it. Order is the reading
# order: what you can do something about, then what you are already in, then the
# rest. A section with no cards is not drawn.
_SECTIONS = (
    # "Open for signup" is the card's own answer, not a re-derivation of it:
    # reading the window alone would put a started Challonge bracket under a
    # heading whose whole promise is a button. A Challonge bracket the reader
    # can enter once they link counts, because the card's button does the link.
    ('Open for signup', 'how_to_reg', lambda c: c.can_sign_up or c.needs_challonge_link or c.signs_up_on_startgg),
    ('My tournaments', 'emoji_events', lambda c: c.enrolled),
    (
        'Opening soon',
        'schedule',
        lambda c: not c.enrolled and not c.can_sign_up and not c.needs_challonge_link
        and c.window is SignupWindow.UPCOMING,
    ),
    # Everything else you are not in: closed windows, and Challonge brackets
    # that have already started.
    (
        'Not taking signups',
        'lock',
        lambda c: not c.enrolled and not c.can_sign_up and not c.needs_challonge_link
        and not c.signs_up_on_startgg and c.window is not SignupWindow.UPCOMING,
    ),
)


# Names a card shows before "Show all": two or three rows on a phone, one or
# two on a desktop card. A 36-player field used to be a 36-row list.
_NAME_PREVIEW = 8


def _user_name(u) -> str:
    return u.display_name or u.username


def _people_heading(icon: str, text: str) -> None:
    with ui.row().classes('wiz-people-heading items-center no-wrap'):
        ui.icon(icon, size='16px').props('aria-hidden=true')
        ui.label(text)


def _name_chips(
    people: list[tuple[str, bool]], *, label: str, admin: bool = False,
    total: Optional[int] = None,
) -> None:
    """Names as wrapping pills: the viewer first with "(you)", the rest A to Z."""
    ordered = sorted(people, key=lambda p: (not p[1], p[0].lower()))
    box = ui.element('div').classes('wiz-name-chips').props(f'role=list aria-label="{label}"')
    with box:
        for i, (name, mine) in enumerate(ordered):
            classes = 'wiz-name-chip'
            if admin:
                classes += ' wiz-name-chip--admin'
            if mine:
                classes += ' wiz-name-chip--me'
            if i >= _NAME_PREVIEW:
                classes += ' wiz-name-chip--more'
            with ui.element('span').classes(classes).props('role=listitem'):
                ui.label(name).classes('wiz-name-chip__name')
                if mine:
                    ui.label('(you)').classes('wiz-you')
    if len(ordered) <= _NAME_PREVIEW:
        return
    show_all = f'Show all {total or len(ordered)}'

    def toggle() -> None:
        opening = 'is-open' not in box.classes
        if opening:
            box.classes(add='is-open')
        else:
            box.classes(remove='is-open')
        button.set_text('Show fewer' if opening else show_all)
        button.props(f'aria-expanded={str(opening).lower()}')

    button = ui.button(show_all, on_click=toggle) \
        .props('flat dense no-caps color=primary aria-expanded=false') \
        .classes('wiz-name-chips__toggle self-start')


def signup_status(card: TournamentSignupCard) -> tuple[str, str]:
    """``(chip class, label)`` for one tournament, from the reader's side of it.

    Enrolment wins over the window: "Signed up" is the fact that matters to
    someone already in, and telling them signups closed would read as though
    they had missed it.
    """
    if card.enrolled:
        return 'wiz-chip--ok', 'Signed up'
    if card.challonge_managed and card.roster_locked:
        return 'wiz-chip--cancelled', 'Bracket started'
    if card.window is SignupWindow.UPCOMING:
        return 'wiz-chip--candidate', 'Not open yet'
    if card.window is SignupWindow.CLOSED:
        return 'wiz-chip--cancelled', 'Signups closed'
    return 'wiz-chip--pending', 'Signups open'


def challonge_note(card: TournamentSignupCard) -> Optional[str]:
    """Which Challonge account the reader is, or will be, on the bracket as."""
    if not card.challonge_managed:
        return None
    if card.enrolled:
        return (
            "You're on the Challonge bracket. If you haven't yet, accept the invite "
            'Challonge emailed you to tie the entry to your account.'
        )
    if card.can_sign_up:
        return f"You'll join the Challonge bracket as {card.challonge_name}."
    if card.needs_challonge_link:
        return (
            'This bracket runs on Challonge, so link your Challonge account first. '
            "You'll come straight back here to finish signing up."
        )
    return None


def window_note(card: TournamentSignupCard) -> Optional[str]:
    """The one line under the title that says when the window moves next."""
    t = card.tournament
    if card.window is SignupWindow.UPCOMING and t.signups_open_at:
        return f'Signups open {format_local_display(t.signups_open_at)}'
    if card.window is SignupWindow.OPEN and t.signups_close_at:
        return f'Signups close {format_local_display(t.signups_close_at)}'
    if card.window is SignupWindow.CLOSED and t.signups_close_at:
        return f'Signups closed {format_local_display(t.signups_close_at)}'
    return None


def event_dates(card: TournamentSignupCard) -> Optional[str]:
    """When the tournament itself runs, when staff have said."""
    t = card.tournament
    if t.event_start_date and t.event_end_date:
        if t.event_start_date == t.event_end_date:
            return format_local_date(t.event_start_date)
        return f'{format_local_date(t.event_start_date)} – {format_local_date(t.event_end_date)}'
    if t.event_start_date:
        return f'From {format_local_date(t.event_start_date)}'
    return None


def _card_dom_id(tournament_id: int) -> str:
    return f'wiz-tournament-card-{tournament_id}'


async def tournaments_tab() -> None:
    user = await get_user_from_session(app.storage.user)
    if user is None:
        ui.label('You must be logged in to sign up for tournaments.').classes('text-error')
        return

    service = TournamentService()
    # Which cards get a Triforce Texts button. One query for the whole list
    # rather than a support check per card, and empty where the community has
    # the feature off, which is what removes the button.
    triforce_ids: set[int] = set()
    if FeatureFlag.TRIFORCE_TEXTS in await FeatureFlagService().enabled_flags():
        triforce_ids = await supporting_tournament_ids()

    with ui.column().classes('page-container'):
        with page_header(
            'Tournaments',
            "Sign up to join a tournament's player pool so staff can schedule your "
            'matches. Match alerts are set on your Profile.',
        ):
            await help_icon('tournament-signup')
            ui.space()

        # Both handlers run as detached background tasks, where the slot stack is
        # empty, so the client comes from the call site rather than being read in
        # here — ``context.client`` raises inside the task.
        def follow_card(tournament_id: int) -> None:
            """Bring a card back under the reader after it changed section.

            Signing up moves a card from "Open for signup" to "My tournaments",
            hundreds of pixels away, while the viewport stays put: the Withdraw
            button the player might want next was no longer where they looked.
            """
            ui.run_javascript(
                f"const el = document.getElementById('{_card_dom_id(tournament_id)}');"
                "if (el) { el.scrollIntoView({block: 'center', behavior: 'smooth'});"
                " el.classList.add('wiz-row-flash');"
                " setTimeout(() => el.classList.remove('wiz-row-flash'), 1700); }"
            )

        async def sign_up(tournament_id: int, client) -> None:
            with client:
                try:
                    tournament = await service.self_enroll(tournament_id, user)
                    ui.notify(f"You're signed up for {tournament.name}.", color='positive')
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    await cards.refresh()
                    return
                await cards.refresh()
                follow_card(tournament_id)

        async def withdraw(tournament_id: int, client) -> None:
            with client:
                try:
                    tournament = await service.self_withdraw(tournament_id, user)
                    ui.notify(f'You have withdrawn from {tournament.name}.', color='positive')
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    await cards.refresh()
                    return
                await cards.refresh()
                follow_card(tournament_id)

        def confirm_challonge_signup(card: TournamentSignupCard) -> None:
            client = context.client
            open_challonge_signup_dialog(
                card.tournament.name, card.challonge_name or '', card.entrant_count,
                on_confirm=lambda: sign_up(card.tournament.id, client),
            )

        def start_challonge_link() -> None:
            if is_host_mode() and not host_oauth_handoff_enabled():
                # A custom domain without the handoff can only link from the
                # main site, which the Profile's Connected accounts row explains.
                ui.navigate.to(home_url('profile'))
                return
            open_sign_in_first_dialog(
                CHALLONGE_LINK, CHALLONGE_LINK.sign_in_first_url or '',
            )

        def confirm_withdraw(tournament) -> None:
            client = context.client
            ConfirmationDialog(
                message=f'Withdraw from {tournament.name}? Once sign-ups close you’ll '
                        'need staff to put you back in.',
                on_confirm=lambda: withdraw(tournament.id, client),
                confirm_text='Withdraw', cancel_text='Stay in', title='Withdraw?',
                busy_text='Withdrawing…',
            ).open()

        def render_card(card: TournamentSignupCard) -> None:
            t = card.tournament
            chip_class, status = signup_status(card)
            with ui.card().classes('wiz-tournament-card full-width') \
                    .props(f'id={_card_dom_id(t.id)}'):
                with ui.row().classes('items-center gap-2 flex-wrap full-width'):
                    ui.label(t.name).classes('text-subtitle1 text-weight-medium')
                    with ui.element('span').classes(f'wiz-chip {chip_class}'):
                        ui.label(status)
                    if card.challonge_managed:
                        with ui.element('span').classes('wiz-chip wiz-chip--neutral') \
                                .tooltip('This bracket runs on Challonge'):
                            ui.label('Challonge')
                    ui.space()
                    dates = event_dates(card)
                    if dates:
                        with ui.element('span').classes('wiz-chip wiz-chip--neutral'):
                            ui.icon('event', size='14px')
                            ui.label(dates)

                note = window_note(card)
                if note:
                    ui.label(note).classes('text-caption text-grey-7')

                if t.description:
                    # Parsed to blocks and drawn element by element rather than
                    # through ui.markdown: this is staff-authored prose reaching a
                    # player's browser, so it renders as element content, never as
                    # markup. Same renderer the help and event-info pages use.
                    with ui.element('div').classes('wiz-help-prose'):
                        render_blocks(parse_blocks(t.description))

                if http_url_or_empty(t.rules_url):
                    ui.link('Rules', http_url_or_empty(t.rules_url), new_tab=True).classes('text-caption')

                if t.randomizer_notes:
                    with ui.expansion('Randomizer notes', icon='tune') \
                            .classes('w-full').props('dense'):
                        with ui.element('div').classes('wiz-help-prose'):
                            render_blocks(parse_blocks(t.randomizer_notes))

                if card.admins:
                    _people_heading('shield', 'Tournament admins')
                    _name_chips([(_user_name(u), user is not None and u.id == user.id) for u in card.admins],
                                label='Tournament admins', admin=True)

                _people_heading('group', f'{card.entrant_count} signed up')
                if not card.entrants and not card.unmapped_entrant_names:
                    ui.label('Nobody yet — be first.').classes('text-muted text-caption')
                else:
                    _name_chips(
                        [(_user_name(u), user is not None and u.id == user.id) for u in card.entrants]
                        + [(name, False) for name in card.unmapped_entrant_names],
                        label='Signed up', total=card.entrant_count,
                    )

                challonge_line = challonge_note(card)
                if challonge_line:
                    ui.label(challonge_line).classes('text-caption text-grey-7')

                if card.startgg_url:
                    ui.label(
                        "You're in through start.gg. Withdraw there if you need to drop out."
                        if card.enrolled else
                        'Sign-ups for this one happen on start.gg.'
                    ).classes('text-caption text-grey-7')

                with ui.row().classes('items-center gap-2'):
                    if card.startgg_url:
                        if card.signs_up_on_startgg:
                            ui.button(
                                'Sign up on start.gg', icon='open_in_new',
                                on_click=lambda _e, url=card.startgg_url: ui.navigate.to(url, new_tab=True),
                            ).props('color=primary no-caps')
                        ui.button(
                            'Bracket', icon='account_tree',
                            on_click=lambda _e, tid=t.id: ui.navigate.to(f'/tournament/{tid}/startgg'),
                        ).props('flat color=primary no-caps')
                    elif card.can_sign_up and card.challonge_managed:
                        ui.button(
                            'Sign up', icon='how_to_reg',
                            on_click=lambda _e, c=card: confirm_challonge_signup(c),
                        ).props('color=primary no-caps')
                    elif card.can_sign_up:
                        ui.button(
                            'Sign up', icon='how_to_reg',
                            on_click=busy_click('Signing up…', lambda _e, tid=t.id: sign_up(tid, context.client)),
                        ).props('color=primary no-caps')
                    elif card.needs_challonge_link:
                        ui.button(
                            'Link Challonge to sign up', icon='link',
                            on_click=start_challonge_link,
                        ).props('color=primary no-caps')
                    elif card.can_withdraw:
                        ui.button(
                            'Withdraw', icon='undo',
                            on_click=lambda _e, tour=t: confirm_withdraw(tour),
                        ).props('flat color=negative dense no-caps')
                    elif card.enrolled and card.challonge_managed and card.roster_locked:
                        ui.label(
                            'The bracket has started — ask staff if you need to drop out.'
                        ).classes('text-caption text-grey-7')
                    elif card.enrolled:
                        # Enrolled with the window shut — the only state that
                        # leaves an entrant no button. Saying nothing reads as a
                        # missing control, so the card says whose call it is.
                        ui.label(
                            'Signups have closed — ask staff if you need to drop out.'
                        ).classes('text-caption text-grey-7')

                    if t.id in triforce_ids:
                        ui.button(
                            'Triforce Texts', icon='auto_awesome',
                            on_click=lambda _e, tourney=t: background_tasks.create(
                                open_triforce_dialog(tourney, user, context.client)),
                        ).props('flat color=primary dense no-caps')

        @steady_refreshable
        async def cards() -> None:
            all_cards = await service.list_signup_cards(user)
            if not all_cards:
                empty_panel(
                    'No tournaments are running right now. Check back when staff '
                    'have set one up.', icon='emoji_events')
                return
            for title, icon, predicate in _SECTIONS:
                section = [c for c in all_cards if predicate(c)]
                if not section:
                    continue
                with ui.row().classes('items-center gap-2 q-mt-sm'):
                    ui.icon(icon, size='xs').classes('icon-primary')
                    ui.label(title).classes('subsection-title')
                with ui.element('div').classes('wiz-tile-grid wiz-tile-grid--md'):
                    for card in section:
                        render_card(card)

        await cards()
