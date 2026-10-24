"""Dialogs for event check-in: linking a registrant, adding a walk-up, event setup.

Presentation only. Every action goes through :class:`CheckInService`, which
enforces who may do what; these dialogs only decide what to offer. All three
open as full-screen sheets on a phone (``form_dialog``), because the desk runs
on volunteers' phones and the keyboard would otherwise cover half the form.
"""

from typing import Awaitable, Callable, List, Optional

from nicegui import ui

from application.services import CheckInService, SystemConfigService
from application.services.check_in_rules import LANYARD_LABELS, comp_label
from models import CheckInEntrant, CheckInEvent, CheckInEventStatus, CheckInTierLanyard, Role, User
from theme.connection import REQUIRES_SOCKET_CLASS
from theme.dialog._helpers import dialog_actions, form_dialog, submit_on_enter
from theme.notify import notify_error
from theme.pickers import CHIP_PROPS

OnDone = Callable[[], Awaitable[None]]

_PROVIDER_LABELS = {
    'discord': 'Discord', 'twitch': 'Twitch', 'gplus': 'Google', 'facebook': 'Facebook',
    'twitter': 'Twitter', 'battlenet': 'Battle.net', 'youtube': 'YouTube',
}

_STATUS_OPTIONS = {
    CheckInEventStatus.DRAFT.value: 'Draft (not on the desk yet)',
    CheckInEventStatus.OPEN.value: 'Open (desk live, syncing)',
    CheckInEventStatus.CLOSED.value: 'Closed',
}


def provider_label(provider: Optional[str]) -> str:
    return _PROVIDER_LABELS.get(provider or '', (provider or 'unknown').title())


def _person_line(user: User) -> str:
    extra = [n for n in (user.username if user.display_name else None, user.twitch_username) if n]
    return f"{user.preferred_name} ({', '.join(extra)})" if extra else user.preferred_name


def _member_results(container: ui.column, users: List[User], on_pick: Callable[[User], Awaitable[None]],
                    empty_text: str) -> None:
    container.clear()
    with container:
        if not users:
            ui.label(empty_text).classes('text-caption text-grey-7')
            return
        for user in users:
            ui.button(
                _person_line(user), icon='person',
                on_click=lambda _, u=user: on_pick(u),
            ).props('flat no-caps align=left').classes(f'w-full {REQUIRES_SOCKET_CLASS}')


async def open_link_dialog(
    service: CheckInService, actor: User, entrant: CheckInEntrant, on_done: OnDone,
    then_check_in: Optional[OnDone] = None,
) -> None:
    """Pick the Wizzrobe account a registrant belongs to.

    With ``then_check_in`` this is the desk's check-in prompt for an unlinked
    buyer: linking (or skipping) runs ``then_check_in`` in place of ``on_done``,
    and Cancel records nothing.
    """
    suggestions = await service.suggest_users(entrant)
    title = f'Check in {entrant.display_name}' if then_check_in else f'Link {entrant.display_name}'

    with form_dialog(title) as dialog:
        async def pick(user: User) -> None:
            try:
                await service.link(actor, entrant.id, user.id)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            dialog.close()
            ui.notify(f'Linked to {user.preferred_name}.', color='positive')
            await (then_check_in or on_done)()

        async def skip() -> None:
            dialog.close()
            if then_check_in:
                await then_check_in()

        with ui.column().classes('q-pa-md gap-3 full-width'):
            if then_check_in:
                ui.label(
                    "Their badge isn't linked to a Wizzrobe account yet. Ask them for their "
                    "Discord or Twitch name and pick their account, or skip if they don't have one."
                ).classes('text-body2')
            if entrant.matcherino_user_id:
                ui.label(
                    f'Matcherino: {entrant.display_name} · signed in with '
                    f'{provider_label(entrant.auth_provider)}'
                ).classes('text-caption text-grey-7')
            ui.label('Suggestions').classes('text-subtitle2')
            suggested = ui.column().classes('w-full gap-0')
            _member_results(suggested, suggestions, pick, 'No one in this community looks like a match.')

            ui.label('Search members').classes('text-subtitle2 q-mt-sm')
            results = ui.column().classes('w-full gap-0')

            async def search(e) -> None:
                users = await service.search_members(entrant.event, e.value or '', for_link=True)
                _member_results(
                    results, users, pick,
                    'Type at least two letters.' if len(e.value or '') < 2 else 'No members match.',
                )

            box = ui.input(placeholder='Name, username or Twitch').props(
                'outlined dense clearable debounce=300 autofocus').classes('w-full')
            with box.add_slot('prepend'):
                ui.icon('search')
            box.on_value_change(search)
        with dialog_actions().classes('justify-end'):
            ui.button('Cancel', on_click=dialog.close).props('flat')
            if then_check_in:
                ui.button('Skip, just check in', icon='how_to_reg', on_click=skip) \
                    .props('color=primary no-caps').classes(REQUIRES_SOCKET_CLASS)
    dialog.open()


async def open_walk_up_dialog(
    service: CheckInService, actor: User, event: CheckInEvent, on_done: OnDone,
) -> None:
    """Add someone who didn't buy a badge on Matcherino. Staff only."""
    chosen: dict = {'user': None}

    with form_dialog('Add walk-up', card_classes='dialog-card dialog-card--sm') as dialog:
        with ui.column().classes('q-pa-md gap-3 full-width'):
            # url-state: exempt — a form choice inside a dialog
            mode = ui.toggle({'member': 'A member', 'name': 'Just a name'}, value='member') \
                .props('no-caps spread unelevated').classes('wiz-segmented w-full')
            member_box = ui.column().classes('w-full gap-1')
            with member_box:
                picked = ui.label('No member picked yet.').classes('text-caption text-grey-7')
                results = ui.column().classes('w-full gap-0')

                async def pick(user: User) -> None:
                    chosen['user'] = user
                    picked.text = f'Picked: {user.preferred_name}'
                    picked.classes(replace='text-body2 text-positive')

                async def search(e) -> None:
                    users = await service.search_members(event, e.value or '')
                    _member_results(
                        results, users, pick,
                        'Type at least two letters.' if len(e.value or '') < 2 else 'No members match.',
                    )

                box = ui.input(placeholder='Search members').props(
                    'outlined dense clearable debounce=300').classes('w-full')
                with box.add_slot('prepend'):
                    ui.icon('search')
                box.on_value_change(search)
            name_input = ui.input('Their name').props('outlined dense').classes('w-full')
            name_input.bind_visibility_from(mode, 'value', backward=lambda v: v == 'name')
            member_box.bind_visibility_from(mode, 'value', backward=lambda v: v == 'member')
            check_now = ui.checkbox('Check them in now', value=True)

        async def submit() -> None:
            try:
                if mode.value == 'member':
                    if chosen['user'] is None:
                        ui.notify('Pick a member first.', color='warning')
                        return
                    entrant = await service.add_walk_up(
                        actor, event.id, user_id=chosen['user'].id, check_in=check_now.value)
                else:
                    entrant = await service.add_walk_up(
                        actor, event.id, name=name_input.value, check_in=check_now.value)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            dialog.close()
            ui.notify(
                f'{entrant.display_name} added' + (' and checked in.' if check_now.value else '.'),
                color='positive',
            )
            await on_done()

        with dialog_actions().classes('justify-end'):
            ui.button('Cancel', on_click=dialog.close).props('flat')
            ui.button('Add', icon='person_add', on_click=submit).props('color=primary') \
                .classes(REQUIRES_SOCKET_CLASS)
        submit_on_enter(dialog, submit)
    dialog.open()


async def open_event_dialog(
    service: CheckInService, actor: User, on_done: OnDone, event: Optional[CheckInEvent] = None,
) -> None:
    """Create or edit a check-in event. Staff only."""
    title = 'Edit check-in event' if event else 'New check-in event'
    with form_dialog(title) as dialog:
        with ui.column().classes('q-pa-md gap-3 full-width'):
            name = ui.input('Event name', value=event.name if event else '').props('outlined dense') \
                .classes('w-full')
            with ui.row().classes('w-full items-start no-wrap gap-2'):
                venue = ui.number(
                    'Matcherino venue ID',
                    value=event.matcherino_venue_id if event else None,
                    format='%d', min=1,
                ).props('outlined dense clearable').classes('col')
                lookup_btn = ui.button('Look up', icon='travel_explore').props('flat no-caps') \
                    .classes(REQUIRES_SOCKET_CLASS)
            found = ui.label(
                'The number in the link to the event\'s badge page, matcherino.com/events/<number>. '
                "That's the venue, not the tournaments bounty. Leave it empty for a walk-up-only event."
            ).classes('text-caption text-grey-7')

            async def look_up() -> None:
                if not venue.value:
                    ui.notify('Enter a venue ID first.', color='warning')
                    return
                try:
                    preview = await service.preview_venue(actor, int(venue.value))
                except (ValueError, PermissionError) as e:
                    found.text = str(e)
                    found.classes(replace='text-caption text-warning')
                    return
                badge_types = ', '.join(t.title for t in preview.tiers) or 'none yet'
                found.text = f'Found: {preview.venue.title}. Badges: {badge_types}.'
                found.classes(replace='text-caption text-positive')
                if not (name.value or '').strip():
                    name.value = preview.venue.title

            lookup_btn.on_click(look_up)
            status = ui.select(
                _STATUS_OPTIONS, label='Status',
                value=(event.status.value if event else CheckInEventStatus.DRAFT.value),
            ).props('outlined dense').classes('w-full')
            interval = ui.number(
                'Sync with Matcherino every (minutes)',
                value=event.sync_interval_minutes if event else 5, min=1, max=120, format='%d',
            ).props('outlined dense').classes('w-full')

            ui.label('Comps').classes('text-subtitle2 q-mt-sm')
            comp_roles = ui.select(
                {role.value: comp_label(role.value) for role in Role.tenant_grantable()},
                label='Comp everyone with these roles', multiple=True,
                value=list(event.comp_roles) if event else [],
            ).props(CHIP_PROPS).props('outlined dense clearable').classes('w-full')
            tiers = await SystemConfigService.get_volunteer_comp_tiers()
            threshold = f'{tiers[0]:g}' if tiers else None
            comp_volunteers = ui.switch(
                f'Comp volunteers scheduled for {threshold}+ hours' if threshold
                else 'Comp volunteers (no comp tier set in Admin → Settings → Volunteers)',
                value=event.comp_volunteers if event else False,
            )
            ui.label(
                'Comped people show up on the desk without buying a badge. '
                'Volunteer hours are published shifts in the event window.'
            ).classes('text-caption text-grey-7')

            lanyard_selects: dict = {}
            badge_types = await service.tiers_for(event) if event else []
            if badge_types:
                ui.label('Lanyards').classes('text-subtitle2 q-mt-sm')
                ui.label(
                    'Which lanyard each badge type gets. Comped staff and anyone with a volunteer '
                    'shift get Staff or Volunteer whatever they bought.'
                ).classes('text-caption text-grey-7')
                options = {value.value: LANYARD_LABELS[value.value] for value in CheckInTierLanyard}
                for tier in badge_types:
                    lanyard_selects[tier.id] = (tier, ui.select(
                        options, label=tier.title, value=tier.lanyard.value,
                    ).props('outlined dense').classes('w-full'))

        async def submit() -> None:
            venue_id = int(venue.value) if venue.value else None
            try:
                if event is None:
                    await service.create_event(
                        actor, name.value, venue_id=venue_id,
                        status=CheckInEventStatus(status.value),
                        sync_interval_minutes=int(interval.value or 5),
                        comp_roles=comp_roles.value or [], comp_volunteers=bool(comp_volunteers.value),
                    )
                else:
                    await service.update_event(
                        actor, event.id, name.value, venue_id,
                        CheckInEventStatus(status.value), int(interval.value or 5),
                        comp_roles=comp_roles.value or [], comp_volunteers=bool(comp_volunteers.value),
                    )
                    for tier, select in lanyard_selects.values():
                        if select.value != tier.lanyard.value:
                            await service.set_tier_lanyard(actor, tier.id, select.value)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            dialog.close()
            ui.notify('Saved.', color='positive')
            await on_done()

        with dialog_actions().classes('justify-end'):
            ui.button('Cancel', on_click=dialog.close).props('flat')
            ui.button('Save', icon='save', on_click=submit).props('color=primary') \
                .classes(REQUIRES_SOCKET_CLASS)
        submit_on_enter(dialog, submit)
    dialog.open()
