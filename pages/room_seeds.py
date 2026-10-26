"""The tournament room's seeds board — the one page a token opens.

A room PC is a shared machine in a public room; nobody signs in on it, and it
still has to show the seed for the next match. So this route authorizes on an
unlisted :class:`~models.RoomToken` instead of a session, and shows exactly one
thing: matches whose seed is rolled and which haven't finished yet.

``GET /t/<slug>/room/<token>/seeds`` — **inside** the tenant prefix on purpose.
The middleware there has already resolved the community and bound the display
clock, so the token only has to authorize; outside the prefix this page would
have to resolve a tenant from the token itself and enter ``tenant_scope`` by
hand, and every scoped read would raise until it did.

**Chromeless.** No header, drawer or bottom nav — the board is the whole page,
because a machine nobody signs in on has nowhere to navigate to and no session
to sign out of. A floating dark-mode toggle is the one control that survives.

**Read-only.** Rolling and re-rolling stay signed-in staff work: an anonymous
token that could spend randomizer API calls and replace the seed for a match
about to be played is not a trade worth making for a machine sitting in a room
the public walks through.
"""

from typing import Optional

from nicegui import app, ui
from starlette.responses import RedirectResponse

from application.services import RoomTokenService, get_user_from_session
from application.services.match.match_display_service import is_room_seed_row
from middleware.auth import public_page
from middleware.event_routing import page_request, row_event_redirect
from theme.base import BaseLayout
from theme.page_header import page_header
from theme.tables.match import MatchTableView
from theme.tables.match_slots import SEED_SLOT_READONLY, state_readonly_slot

COLUMNS = [
    {'name': 'scheduled_at', 'label': 'Scheduled At', 'field': 'scheduled_at', 'sortable': True},
    {'name': 'players', 'label': 'Players', 'field': 'players'},
    {'name': 'tournament', 'label': 'Tournament', 'field': 'tournament', 'sortable': True},
    {'name': 'stage', 'label': 'Stage', 'field': 'stage', 'sortable': True},
    {'name': 'state', 'label': 'State', 'field': 'state', 'sortable': True},
    {'name': 'generated_seed', 'label': 'Seed', 'field': 'generated_seed'},
]


def create() -> None:
    @public_page('/room/{token}/seeds', telemetry_path='/room/seeds', scope='row')
    async def room_seeds(token: str) -> Optional[RedirectResponse]:
        ui.page_title('Wizzrobe — Room Seeds')

        # An unknown, revoked, malformed or wrong-community token renders the
        # same page a route that never existed would — same copy, same 404 —
        # so guessing at a URL tells you nothing about which tokens are live.
        # The copy ("the link's out of date") is also the true one for the
        # staff member at a room PC whose token was just revoked.
        room_token = await RoomTokenService().resolve(token)
        if room_token is None:
            from theme.error_page import render_not_found
            await render_not_found(
                user=await get_user_from_session(app.storage.user),
            )
            return None
        redirect = await row_event_redirect(page_request(), room_token.event_id)
        if redirect is not None:
            return redirect

        # Anonymous by construction, and chromeless: a room PC has no session to
        # sign out of and nowhere else to go, so the layout contributes the
        # palette and page scripts without a header, drawer or bottom nav.
        await BaseLayout(user=None, chromeless=True).render()

        with ui.column().classes('page-container'):
            page_header('Seeds')
            ui.label(
                'Every match with a rolled seed, from the ones in progress to the '
                'ones still to play. Updates on its own — leave it open.'
            ).classes('text-muted')

            ui.separator().classes('separator-spacing')

            MatchTableView(
                columns=COLUMNS,
                get_query=None,
                admin_controls=False,
                extra_slots={
                    'body-cell-state': state_readonly_slot(scheduled_detailed=False),
                    'body-cell-generated_seed': SEED_SLOT_READONLY,
                },
                row_filter=is_room_seed_row,
                default_state_filter=['Scheduled', 'Checked In', 'Started'],
                # Renamed when Started joined the default: a room PC that had
                # already saved the old two-state filter under the old key would
                # have kept hiding in-progress seeds.
                storage_key='room_seeds_v2',
                # table-prefs: exempt — an anonymous kiosk has no user row to key
                # a saved layout to, and the columns here are fixed. Rearranging
                # them would need a general anonymous-preferences path, not a
                # second persistence backend for one page.
                table_key=None,
            )
