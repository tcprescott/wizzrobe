"""Settings → Discord: the Discord roles Wizzrobe hands out (layout B, the checklist).

Every rule the sync can run is already listed, grouped as Players, Badges and
Tournaments, each with a Discord role picker; a blank picker means the rule is
off. Choosing a role saves it at once. Roles the bot can't hand out stay in the
picker, greyed, with the reason and the fix, and a saved rule that breaks later
shows the same reason under its line.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from nicegui import app, ui

from application.services import (
    DiscordRoleSyncService,
    TournamentService,
    get_user_from_session,
)
from application.services.discord.discord_role_sync_service import (
    NO_DISCORD,
    RoleSyncOverview,
    RoleSyncResult,
    rule_key,
)
from application.utils.timezone import format_local_display
from models import CheckInTierLanyard, DiscordRoleRuleKind
from theme.banner import BannerTone, banner
from theme.busy import busy_click
from theme.dialog._helpers import form_dialog
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.menu import menu_action
from theme.notify import notify_error
from theme.page_header import page_header
from theme.pickers import ColorSelect
from theme.steady import steady_refreshable
from theme.tables.admin_crud import refresh_button, wire_tab_refresh

TAB_LABEL = 'Discord Roles from Wizzrobe'

_BADGES = (
    (CheckInTierLanyard.VIP, 'VIP badge'),
    (CheckInTierLanyard.BASE, 'Base badge'),
    (CheckInTierLanyard.DAY_PASS, 'Day pass'),
)

_NO_ROLE = 0


def _ago(when: datetime) -> str:
    minutes = int((datetime.now(timezone.utc) - when).total_seconds() // 60)
    if minutes < 1:
        return 'just now'
    if minutes < 60:
        return f'{minutes} min ago'
    return format_local_display(when)


def _hex(color: int) -> Optional[str]:
    return f'#{color:06x}' if color else None


def _plural(count: int, word: str, many: Optional[str] = None) -> str:
    return f"{count} {word if count == 1 else (many or word + 's')}"


async def admin_discord_role_sync_page() -> None:
    service = DiscordRoleSyncService()
    show_all = {'value': True}

    async def actor():
        return await get_user_from_session(app.storage.user)

    def show_result(result: RoleSyncResult) -> None:
        if result.error:
            ui.notify(result.error, color='warning')
            return
        ui.notify(
            f'Synced: {result.added} added, {result.removed} removed, {result.in_place} in place.',
            color='positive',
        )

    async def sync_now() -> None:
        try:
            result = await service.sync_now(await actor())
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        show_result(result)
        await body.refresh()

    async def clear_all() -> None:
        try:
            result = await service.clear_all(await actor())
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        show_result(result)
        await body.refresh()

    def confirm_clear_all() -> None:
        ConfirmationDialog(
            message='Turn off every rule and take back every Discord role Wizzrobe gave? '
                    'Roles a moderator handed out by hand stay put.',
            on_confirm=clear_all, confirm_text='Remove roles',
            title='Remove every role Wizzrobe gave?', busy_text='Removing roles…',
        ).open()

    def show_unreachable(people: list) -> None:
        with form_dialog("People who can't get roles") as dialog:
            with ui.column().classes('q-pa-md gap-1 full-width'):
                for reason, heading, hint in (
                    (NO_DISCORD, 'No Discord account linked',
                     'They signed in another way. Linking Discord on their profile fixes it.'),
                    (None, 'Not in the Discord server',
                     'Their Discord account is linked but not in this server.'),
                ):
                    group = [p for p in people if (p['reason'] == NO_DISCORD) == (reason == NO_DISCORD)]
                    if not group:
                        continue
                    ui.label(f'{heading} ({len(group)})').classes('subsection-title q-mt-sm')
                    ui.label(hint).classes('text-caption text-grey')
                    for person in group:
                        ui.label(str(person['name'])).classes('text-body2').style('overflow-wrap: anywhere')
        dialog.open()

    people_holder: Dict[str, list] = {'value': []}

    with ui.column().classes('page-container-narrow'):
        page_header(
            TAB_LABEL,
            'Wizzrobe gives these roles in your Discord server and keeps them up to date. '
            'Pick a role on a line to turn it on, or "No role" to turn it off. Wizzrobe '
            'only ever takes back roles it gave, so roles a moderator hands out by hand stay put.',
        )
        with ui.row().classes('full-width items-center no-wrap'):
            ui.button('Sync now', icon='sync', on_click=busy_click('Syncing…', sync_now)).props('color=primary')
            with ui.button('Actions').props('flat color=primary icon-right=expand_more'):
                with ui.menu():
                    menu_action('person_off', "People who can't get roles",
                                lambda: show_unreachable(people_holder['value']))
                    ui.separator()
                    menu_action('remove_moderator', 'Remove every role Wizzrobe gave…',
                                confirm_clear_all, negative=True)
            ui.space()

            async def reload() -> None:
                await body.refresh()

            refresh_button(reload)

        @steady_refreshable
        async def body() -> None:
            try:
                overview = await service.overview(await actor())
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            tournaments = await TournamentService().get_all_tournaments()
            people_holder['value'] = list(overview.state.unreachable or []) if overview.state else []
            _status(overview, people_holder['value'], show_unreachable)
            if overview.roles_error:
                with banner(overview.roles_error, title="Couldn't read the server's roles", tone='warning'):
                    pass

            _group_title('Players')
            await _rule_line(service, overview, actor, 'Enrolled in any tournament',
                             'Active tournaments only', DiscordRoleRuleKind.ANY_TOURNAMENT)
            await _rule_line(service, overview, actor, 'In a match right now',
                             'Added when the match starts, taken back when it finishes',
                             DiscordRoleRuleKind.LIVE_MATCH)

            if overview.badges_available or any(k.startswith('badge:') for k in overview.rules):
                _group_title('Badges')
                for lanyard, label in _BADGES:
                    await _rule_line(service, overview, actor, label,
                                     'Given when the badge is bought, taken back on a refund',
                                     DiscordRoleRuleKind.BADGE, lanyard=lanyard)

            with ui.row().classes('w-full items-center q-mt-md'):
                ui.label('Tournaments').classes('subsection-title')
                ui.space()
                only = ui.checkbox('Only ones with a role', value=not show_all['value'])

                async def toggle(e) -> None:
                    show_all['value'] = not e.value
                    await body.refresh()

                only.on_value_change(toggle)
            shown = [
                t for t in sorted(tournaments, key=lambda t: t.name.lower())
                if (t.is_active and show_all['value'])
                or rule_key(DiscordRoleRuleKind.TOURNAMENT, t.id) in overview.rules
            ]
            if not shown:
                ui.label('No tournaments have a role yet.').classes('text-caption text-grey')
            for tournament in shown:
                await _rule_line(
                    service, overview, actor, tournament.name,
                    None if tournament.is_active else 'Inactive tournament',
                    DiscordRoleRuleKind.TOURNAMENT, tournament_id=tournament.id,
                )

        await body()
        wire_tab_refresh(TAB_LABEL, body.refresh)


def _group_title(text: str) -> None:
    ui.label(text).classes('subsection-title q-mt-md')


def _status(overview: RoleSyncOverview, people: list, show_unreachable) -> None:
    state = overview.state
    broken = [rule for rule in overview.rules.values() if rule.problem]
    if state is None or state.last_synced_at is None:
        message = 'Not synced yet. Pick a role below and Wizzrobe syncs within a few seconds.'
        if state is not None and state.last_error:
            with banner(state.last_error, title="The last sync didn't finish", tone='danger'):
                pass
            return
        with banner(message, tone='info'):
            pass
        return
    parts = [f'Last synced {_ago(state.last_synced_at)}. {_plural(state.in_place, "role")} in place.']
    if broken:
        parts.append(f'{_plural(len(broken), "rule needs", "rules need")} fixing, shown in red below.')
    if state.last_error:
        with banner(' '.join(parts), title=state.last_error, tone='danger'):
            pass
        return
    no_discord = sum(1 for p in people if p['reason'] == NO_DISCORD)
    not_in_server = len(people) - no_discord
    tone: BannerTone = 'warning' if broken or people else 'ok'
    if people:
        reasons = []
        if no_discord:
            reasons.append(f"{no_discord} {'has' if no_discord == 1 else 'have'} no Discord linked")
        if not_in_server:
            reasons.append(f"{not_in_server} {'isn' if not_in_server == 1 else 'aren'}'t in the server")
        parts.append(f"{_plural(len(people), 'person', 'people')} can't get roles: {', '.join(reasons)}.")
    with banner(' '.join(parts), tone=tone):
        if people:
            ui.button('See who', on_click=lambda: show_unreachable(people)).props('flat dense color=primary')


async def _rule_line(
    service: DiscordRoleSyncService,
    overview: RoleSyncOverview,
    actor,
    label: str,
    caption: Optional[str],
    kind: DiscordRoleRuleKind,
    *,
    tournament_id: Optional[int] = None,
    lanyard: Optional[CheckInTierLanyard] = None,
) -> None:
    rule = overview.rules.get(rule_key(kind, tournament_id, lanyard))
    options: Dict[int, str] = {_NO_ROLE: 'No role'}
    extras: Dict[int, Dict[str, Any]] = {}
    for role in overview.roles:
        options[role.id] = role.name
        extra: Dict[str, Any] = {}
        if _hex(role.color):
            extra['color'] = _hex(role.color)
        if role.problem and (rule is None or rule.discord_role_id != role.id):
            extra.update(disable=True, caption=role.problem)
        if extra:
            extras[role.id] = extra
    if rule is not None and rule.discord_role_id not in options:
        options[rule.discord_role_id] = rule.discord_role_name

    with ui.element('div').classes('row items-center q-col-gutter-sm w-full q-py-xs'):
        with ui.column().classes('col-12 col-sm gap-0'):
            ui.label(label).classes('text-body2').style('overflow-wrap: anywhere')
            if caption:
                ui.label(caption).classes('text-caption text-grey')
        with ui.element('div').classes('col-12 col-sm-5'):
            select = ColorSelect(
                options, label='Discord role', extras=extras,
                value=rule.discord_role_id if rule else _NO_ROLE,
            ).props('outlined dense').classes('w-full')
            if overview.roles_error:
                select.disable()
        with ui.element('div').classes('col-12 col-sm-2 text-right'):
            if rule is not None and rule.holding is not None and not rule.problem:
                ui.label(f'{rule.holding} now').classes('text-caption text-grey')
    if rule is not None and rule.problem:
        with ui.row().classes('items-start no-wrap gap-1 q-pb-xs'):
            ui.icon('error', color='negative', size='xs')
            ui.label(rule.problem).classes('text-caption text-negative')

    previous = {'value': select.value}

    async def changed(e) -> None:
        if e.value == previous['value']:
            return
        role_id = None if e.value == _NO_ROLE else int(e.value)
        try:
            await service.set_rule(
                await actor(), kind, role_id, tournament_id=tournament_id, lanyard=lanyard,
            )
        except (ValueError, PermissionError) as error:
            notify_error(error)
            select.set_value(previous['value'])
            return
        previous['value'] = e.value
        if role_id is None:
            ui.notify(f'{label}: role turned off. Wizzrobe takes it back on the next sync.', color='positive')
        else:
            ui.notify(f'{label}: saved. Wizzrobe hands it out within a few seconds.', color='positive')

    select.on_value_change(changed)
