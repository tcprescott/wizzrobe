"""The scope checklist both consent screens show.

One list, two groups: what the connection may *see* and what it may *do as
you*. Outside apps (``/oauth/consent``) and MCP clients (``/oauth/mcp/consent``)
render the same rows so a person only ever learns one card.
"""

from typing import Callable, Dict, Iterable, Set

from nicegui import ui

from application.api_scopes import CONSENT_WORDING, STAFF_SCOPES, WRITE_SCOPES, ApiScope

SCOPE_DETAIL: Dict[ApiScope, str] = {
    ApiScope.PROFILE: 'Name, pronouns and when you said you are free',
    ApiScope.SCHEDULE_READ: 'Anything a member of this community can already see',
    ApiScope.STAFF_READ: 'Reports, audit log, member list, equipment and check-in',
    ApiScope.PROFILE_WRITE: "Pronouns, when you're free, and how Wizzrobe messages you",
    ApiScope.PLAY: 'Acknowledge and confirm your matches, ask to reschedule, sign up for tournaments',
    ApiScope.CREW: 'Commentary, tracking and other crew signups',
    ApiScope.VOLUNTEER: 'Your own shifts and checking in for them',
    ApiScope.STAFF_WRITE: 'Tournaments, matches, volunteers and equipment, as your role allows',
}

_EVERY_COMMUNITY_DETAIL: Dict[ApiScope, str] = {
    ApiScope.SCHEDULE_READ: 'Anything a member can already see, in each community you belong to',
}


def default_ticks(scopes: Iterable[ApiScope]) -> Set[ApiScope]:
    """What starts ticked: every read an app may ask for, never a change.

    Staff reads start unticked too: a hurried click through gives an app what
    any member could see, and nothing a role adds.
    """
    return {s for s in scopes if s not in WRITE_SCOPES and s not in STAFF_SCOPES}


def scope_label(scope: ApiScope, *, every_community: bool = False) -> str:
    wording = CONSENT_WORDING[scope]
    return wording.removesuffix(' in this community') if every_community else wording


def render_scope_checklist(
    offered: Iterable[ApiScope],
    ticked: Set[ApiScope],
    on_toggle: Callable[[ApiScope, bool], None],
    *,
    every_community: bool = False,
) -> None:
    """The two groups of checkboxes, in declaration order. ``ticked`` is read,
    never written: ``on_toggle`` owns the state."""
    offered = list(offered)
    groups = [
        ('Let it see', [s for s in offered if s not in WRITE_SCOPES]),
        ('Let it act as you', [s for s in offered if s in WRITE_SCOPES]),
    ]
    details = {**SCOPE_DETAIL, **(_EVERY_COMMUNITY_DETAIL if every_community else {})}
    for title, scopes in groups:
        if not scopes:
            continue
        ui.label(title).classes('consent-grants-title')
        with ui.column().classes('consent-grants'):
            for scope in scopes:
                label = scope_label(scope, every_community=every_community)
                with ui.row().classes('consent-scope no-wrap'):
                    ui.checkbox(
                        value=scope in ticked,
                        on_change=lambda e, s=scope: on_toggle(s, bool(e.value)),
                    ).props(f'dense aria-label="{label}"')
                    with ui.column().classes('gap-0'):
                        with ui.row().classes('items-center gap-1'):
                            ui.label(label)
                            if scope in STAFF_SCOPES:
                                ui.badge('Staff').props('color=warning outline')
                        ui.label(details[scope]).classes('consent-note')
