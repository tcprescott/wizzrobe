"""The event handbook's tournament list: one compact row per tournament.

Drawn in place of a ``:::tournaments`` line in a handbook article. The rows come
from :meth:`EventInfoService.list_tournaments`, so this module only lays them out:
name and format on the left, Rules and Bracket links, and a Sign up button while
the signup window is open. The same row stacks on a phone.
"""

from __future__ import annotations

from typing import Optional

from nicegui import ui

from application.services.event_info_service import HandbookTournament
from application.utils.app_links import HOME_TOURNAMENTS, home_url
from application.utils.clients.sglive_client import SGLIVE_URL
from application.utils.money import format_money
from application.utils.timezone import format_local_time, to_local
from application.utils.tournament_signup import SignupWindow

__all__ = ['render_tournament_list']


def _when(row: HandbookTournament, tz: Optional[str]) -> str:
    local = to_local(row.signups_close_at, tz)
    if local is None:
        return ''
    return local.strftime('%a %-d %b, %-I:%M %p')


def _status(row: HandbookTournament, tz: Optional[str]) -> str:
    if row.signups is SignupWindow.CLOSED:
        return 'sign-ups closed'
    if row.signups is SignupWindow.OPEN and row.signups_close_at is not None:
        return f'sign-ups close {_when(row, tz)}'
    return ''


def render_tournament_list(rows: list[HandbookTournament], *, tz: Optional[str] = None) -> None:
    """Draw ``rows`` into the current slot; ``tz`` is the clock close times read on."""
    if not rows:
        with ui.element('p').classes('wiz-help-p text-muted'):
            ui.label('No tournaments have been announced yet.').classes('wiz-help-inline')
        return
    signup_url = home_url(HOME_TOURNAMENTS)
    with ui.element('div').classes('wiz-event-tournaments'):
        for row in rows:
            with ui.element('div').classes('wiz-event-tournament'):
                with ui.element('div').classes('wiz-event-tournament__text'):
                    with ui.element('div').classes('wiz-event-tournament__title'):
                        ui.label(row.name).classes('wiz-event-tournament__name')
                        if row.prize_total is not None:
                            with ui.element('span').classes('wiz-chip wiz-chip--ok'):
                                ui.icon('payments').classes('text-sm')
                                ui.label(f'{format_money(row.prize_total)} prize pool')
                    meta = ' · '.join(part for part in (row.format, _status(row, tz)) if part)
                    if meta:
                        ui.label(meta).classes('wiz-event-tournament__meta')
                with ui.element('div').classes('wiz-event-tournament__actions'):
                    if row.rules_url:
                        rules = ui.link('Rules', row.rules_url, new_tab=True).classes('wiz-help-link')
                        rules.props['aria-label'] = f'{row.name} rules (opens in a new tab)'
                    if row.bracket_url:
                        external = not row.bracket_url.startswith('/')
                        bracket = ui.link('Bracket', row.bracket_url, new_tab=external) \
                            .classes('wiz-help-link')
                        bracket.props['aria-label'] = f'{row.name} bracket'
                    if row.signups is SignupWindow.OPEN:
                        button = ui.button('Sign up', on_click=lambda: ui.navigate.to(signup_url)) \
                            .props('unelevated no-caps color=primary') \
                            .classes('wiz-event-tournament__signup')
                        button.props['aria-label'] = f'Sign up for {row.name}'
    synced = [row.prize_synced_at for row in rows if row.prize_synced_at is not None]
    if synced:
        with ui.element('p').classes('wiz-event-tournaments__foot'):
            ui.label('Prize pools update from').classes('wiz-help-inline')
            site = ui.link('sglive.speedgaming.org', SGLIVE_URL, new_tab=True).classes('wiz-help-link')
            site.props['aria-label'] = 'sglive.speedgaming.org (opens in a new tab)'
            ui.label(
                f'every 15 minutes, last at {format_local_time(max(synced), tz)}. '
                "Pools include SpeedGaming's bonus."
            ).classes('wiz-help-inline')
