#!/usr/bin/env python3
"""start.gg mirror fixtures for the dev seed.

Links its own tournament to the canned mock event (MOCK_STARTGG's shape: a
finished round-robin Group Prelim feeding a four-player Final Bracket) and
mirrors it through ``StartggService`` so every start.gg model gets rows the way
a real sync writes them: StartggEntrant, StartggPhase, StartggPool, StartggSet.
player_one and player_two share an open
semifinal, so My Schedule shows a start.gg row to book. They map through the
start.gg accounts linked on their profiles; Player Three and Four map only by
name, so the Entrants dialog has suggestions to confirm. The community is
signed in to start.gg as the mock account, so Settings → Integrations shows
the access check. Must run inside the
tenant's ``tenant_scope``.
Idempotent: re-running re-syncs the same mock event.
"""
from datetime import datetime

from application.repositories import StartggRepository
from application.services.startgg_service import StartggService
from application.utils.mocks.mock_startgg import (
    MOCK_CONNECTED_USER_ID,
    MOCK_LINKED_ACCOUNTS,
    mock_event_full,
)
from models import StartggConnection, Tenant, Tournament, User

_SLUG = 'tournament/wizzrobe-dev/event/ootr-singles'


async def seed_startgg_for_tenant(
    tenant: Tenant, users: dict[str, User], staff: User, now_utc: datetime,
) -> None:
    for username, account in MOCK_LINKED_ACCOUNTS.items():
        user = users[username]
        if user.startgg_user_id is None:
            user.startgg_user_id = account
            user.startgg_username = username.replace('_', '')
            user.startgg_linked_at = now_utc
            await user.save(update_fields=['startgg_user_id', 'startgg_username', 'startgg_linked_at'])

    tournament, _ = await Tournament.get_or_create(
        name='OoTR Singles (start.gg)', tenant=tenant,
        defaults={
            'tournament_format': 'Round-robin pools, then single elimination',
            'seed_generator': 'ootr',
            'average_match_duration': 120,
            'max_match_duration': 180,
            'allow_player_match_requests': False,
        },
    )
    full = mock_event_full(_SLUG)
    if tournament.startgg_event_id != full['event']['event_id']:
        await StartggRepository.set_event_link(
            tournament, full['event']['event_id'], _SLUG, full['event']['name'],
        )
    await StartggConnection.get_or_create(tenant=tenant, defaults={
        'access_token': 'mock-access', 'refresh_token': 'mock-refresh',
        'scopes': 'user.identity tournament.reporter',
        'startgg_user_id': MOCK_CONNECTED_USER_ID, 'startgg_username': 'MockGGOne', 'connected_by': staff,
    })
    # The mirror itself, exactly as a sync writes it. Not audited: no one
    # pressed anything.
    await StartggService()._apply(tournament, full, staff, audit=False)
    print(f'    [{tenant.slug}] start.gg mirror ok')
