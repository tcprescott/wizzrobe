"""The "finish your profile" banners at the top of every framed page.

Drawn by :meth:`theme.base.BaseLayout.render` for a signed-in member of the
community in scope. Each is a ``danger`` :func:`theme.banner.banner` for one
unmet rule from Admin → Settings → Members, with a button that opens the Profile
tab scrolled to the field that fixes it and, if the rule allows, a Dismiss that
hides it for good in this community.
"""

import logging

from nicegui import ui

from application.services import ProfileNudgeService
from application.services.profile_nudge_service import ProfileNudge, ProfileSection
from application.tenant_context import get_current_tenant_id, tenant_scope
from models import User
from theme.banner import banner
from theme.timer_teardown import PageTimer

logger = logging.getLogger(__name__)

__all__ = ['focus_profile_section', 'profile_fix_path', 'render_profile_nudge_banner', 'section_anchor']


def section_anchor(section: ProfileSection) -> str:
    """The DOM id of the Profile tab card that fixes ``section``'s items."""
    return f'profile-{section.value}'


def profile_fix_path(nudge: ProfileNudge) -> str:
    return f'/home/profile?focus={nudge.section.value}'


def focus_profile_section(focus: str | None) -> None:
    """Scroll the Profile tab to the card ``focus`` names and outline it.

    Called at the end of the tab's build. Deferred to a timer because the card
    has to be in the browser's DOM, and a lazily built tab's markup isn't until
    the client has it. An unknown value is ignored: the tab still opens.
    """
    try:
        section = ProfileSection(focus)
    except ValueError:
        return
    anchor = section_anchor(section)
    PageTimer(0.3, lambda: ui.run_javascript(
        f"const el = document.getElementById('{anchor}');"
        "if (el) { el.scrollIntoView({block: 'start', behavior: 'smooth'});"
        " el.classList.add('wiz-deep-link-target');"
        " setTimeout(() => el.classList.remove('wiz-deep-link-target'), 2500); }"
    ), once=True)


async def render_profile_nudge_banner(user: User | None) -> None:
    """Draw the banner, or nothing when the viewer owes nothing."""
    if user is None:
        return
    service = ProfileNudgeService()
    try:
        nudges = await service.pending_for(user)
    except Exception:
        # Chrome, not content: a failed lookup must not take the page down.
        logger.warning('Profile nudge lookup failed', exc_info=True)
        return
    if not nudges:
        return

    tenant_id = get_current_tenant_id()
    stack = ui.column().classes('w-full gap-2 q-mb-md') \
        .props('role=region aria-label="Recommended profile items"')

    async def dismiss(nudge: ProfileNudge, row: ui.element) -> None:
        try:
            with tenant_scope(tenant_id):
                await service.dismiss(user, nudge.check.value)
        except ValueError as e:
            ui.notify(str(e), color='warning')
            return
        row.delete()
        if not stack.default_slot.children:
            stack.delete()

    with stack:
        for nudge in nudges:
            with banner(nudge.prompt, title=nudge.note, tone='danger') as actions:
                path = profile_fix_path(nudge)
                ui.button(nudge.action_label, on_click=lambda p=path: ui.navigate.to(p)) \
                    .props('unelevated dense no-caps color=negative')
                if nudge.dismissible:
                    root = actions.parent_slot.parent if actions.parent_slot else actions
                    ui.button(
                        'Dismiss',
                        on_click=lambda n=nudge, r=root: dismiss(n, r),
                    ).props('flat dense no-caps') \
                        .tooltip('Hide this reminder. You can still do it from your profile.')
