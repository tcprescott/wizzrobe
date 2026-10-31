"""Dev seed for prize payouts.

Two states, because they are the two an admin actually sits in: last season's
event is paid out in full, and the running one has a pool with nobody placed
yet. The finished split reproduces SGL's ALTTPR 2025 arithmetic verbatim —
50/30/10/10 of a $1000 pool plus a $100 bonus, with **two** third places — so the
tie path and the ``$550 from a $1000 pool`` sum both have a fixture.

One of the four winners deliberately has no Matcherino handle, which is what
makes the export's missing-handle line visible in a running app.
"""

from datetime import datetime, timezone
from decimal import Decimal

from models import Tenant, Tournament, TournamentPayout, TournamentPlayers, User

_ARCHIVED_SPLIT = [
    (1, Decimal('50.00'), 0),
    (2, Decimal('30.00'), 1),
    (3, Decimal('10.00'), 2),
    (3, Decimal('10.00'), 3),
]

_FIXED_SPLIT = [
    (1, Decimal('400.00'), 0),
    (2, Decimal('200.00'), 1),
    (3, Decimal('135.00'), 3),
]

# player_three has no handle on purpose: it keeps the export's missing-handle branch covered.
MATCHERINO_HANDLES = {
    'player_one': 'jemgold#100234',
    'player_two': 'blueshell#204871',
    'player_four': 'ridgeline#309552',
}


async def seed_matcherino_handles(users: dict[str, User]) -> None:
    """Give a few fixtures a Matcherino handle. Global, like the column."""
    for username, handle in MATCHERINO_HANDLES.items():
        user = users.get(username)
        if user is not None and user.matcherino_username != handle:
            user.matcherino_username = handle
            await user.save()
    print('  matcherino handles ok (global; player_three has none on purpose)')


async def seed_payouts_for_tenant(
    tenant: Tenant, players: list[User],
) -> None:
    """Seed one paid-out split and one drafting pool. Idempotent."""
    archived = await Tournament.get_or_none(
        name='Wizzrobe Cup — Last Season', tenant=tenant,
    )
    if archived is not None:
        archived.prize_pool = Decimal('1000.00')
        archived.prize_bonus = Decimal('100.00')
        await archived.save()
        for place, percentage, index in _ARCHIVED_SPLIT:
            entrant = players[index]
            await TournamentPlayers.get_or_create(
                tournament=archived, user=entrant, tenant=tenant,
            )
            await TournamentPayout.get_or_create(
                tournament=archived, place=place, entrant=entrant, tenant=tenant,
                defaults={'percentage': percentage},
            )

    active = await Tournament.get_or_none(
        name='Wizzrobe Dev Tournament', tenant=tenant,
    )
    if active is not None and active.prize_pool is None:
        active.prize_pool = Decimal('500.00')
        await active.save()
    if active is not None and active.sglive_slug is None:
        active.sglive_slug = 'wwr'
        active.prize_pool_synced_at = datetime.now(timezone.utc)
        await active.save()

    # Fixed dollars: no two-decimal percentage reproduces $400/$200/$135 of $735 to the cent.
    fixed = await Tournament.get_or_none(name='Wizzrobe Archive Cup', tenant=tenant)
    if fixed is not None:
        if fixed.prize_pool is None:
            fixed.prize_pool = Decimal('735.00')
            await fixed.save()
        for place, amount, index in _FIXED_SPLIT:
            entrant = players[index]
            await TournamentPlayers.get_or_create(
                tournament=fixed, user=entrant, tenant=tenant,
            )
            await TournamentPayout.get_or_create(
                tournament=fixed, place=place, entrant=entrant, tenant=tenant,
                defaults={'amount': amount},
            )

    print(f'    [{tenant.slug}] payouts ok (one paid out, one in fixed dollars, one still drafting, one synced from sglive)')
