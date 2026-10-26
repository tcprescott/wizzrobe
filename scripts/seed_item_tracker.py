"""Dev seed for the ALTTP item tracker.

One ALTTPR match with a rolled seed (open, Ganon, 7/7, full keysanity so the
key row shows), player_three approved as its tracker, and
player_one's board a few minutes into a run: some items marked, Eastern Palace
beaten, a change in the log. sm_user (Broadcast Tech) and staff can open it too.
"""
import hashlib
from datetime import datetime, timedelta, timezone

from application.services.feature_flag_service import FeatureFlagService, reset_flag_cache
from application.tenant_context import tenant_scope
from models import (
    FeatureFlag,
    GeneratedSeeds,
    ItemTrackerBoard,
    ItemTrackerChange,
    Match,
    MatchPlayers,
    Tenant,
    Tournament,
    Tracker,
    User,
)

MATCH_TITLE = 'Tracker Demo: Player One vs Player Two'
SETTINGS = {
    'mode': 'open',
    'goal': 'ganon',
    'crystals': {'tower': '7', 'ganon': '7'},
    'dungeon_items': 'full',
}
STATE = {
    'bow': 1, 'hookshot': 1, 'bombs': 1, 'fire_rod': 1, 'lamp': 1, 'boots': 1,
    'gloves': 1, 'sword': 2, 'shield': 1, 'bottles': 1, 'flute': 1,
    'ep.boss': 1, 'ep.chests': 3, 'ep.prize': 1, 'ep.big_key': 1,
    'dp.chests': 1, 'dp.keys': 1, 'mm.medallion': 2,
}


def overlay_token(tenant: Tenant, user_key: str) -> str:
    """Stable across fresh dev databases, so ``ui_a11y_ci.json`` can name the overlay."""
    return hashlib.sha256(f'seed-item-tracker:{tenant.slug}:{user_key}'.encode()).hexdigest()[:40]


async def seed_item_tracker_for_tenant(tenant: Tenant, users: dict[str, User]) -> None:
    with tenant_scope(tenant.id):
        reset_flag_cache()
        if not await FeatureFlagService().is_enabled(FeatureFlag.ITEM_TRACKER):
            print(f'    [{tenant.slug}] item tracker skipped (feature not live here)')
            return
    tournament, _ = await Tournament.get_or_create(
        name='ALTTPR Tracker Demo', tenant=tenant,
        defaults={'is_active': True, 'players_per_match': 2, 'seed_generator': 'alttpr'},
    )
    now = datetime.now(timezone.utc)
    match = await Match.get_or_none(title=MATCH_TITLE, tournament=tournament, tenant=tenant)
    if match is None:
        seed = await GeneratedSeeds.create(
            tenant=tenant, seed_url='https://alttpr.com/h/SeedTrackerDemo', randomizer='alttpr',
            settings_snapshot=SETTINGS, rolled_by=users['staff_user'],
        )
        match = await Match.create(
            title=MATCH_TITLE, tournament=tournament, tenant=tenant, generated_seed=seed,
            scheduled_at=now - timedelta(minutes=20), seated_at=now - timedelta(minutes=15),
            started_at=now - timedelta(minutes=10),
        )
    for key in ('player_one', 'player_two'):
        await MatchPlayers.get_or_create(match=match, user=users[key], tenant=tenant)
    await Tracker.get_or_create(
        match=match, user=users['player_three'], tenant=tenant,
        defaults={'approved': True, 'approved_by': users['staff_user'], 'acknowledged_at': now},
    )
    runner = users['player_one']
    board, created = await ItemTrackerBoard.get_or_create(
        match=match, runner=runner, tenant=tenant,
        defaults={'state': STATE, 'delay_seconds': 30, 'overlay_token': overlay_token(tenant, 'player_one')},
    )
    if created:
        await ItemTrackerChange.create(
            tenant=tenant, board=board, actor=users['player_three'], key='sword',
            before=1, after=2, created_at=now - timedelta(minutes=1),
        )
    await ItemTrackerBoard.get_or_create(
        match=match, runner=users['player_two'], tenant=tenant,
        defaults={'state': {'boots': 1, 'lamp': 1}, 'delay_seconds': 30,
                  'overlay_token': overlay_token(tenant, 'player_two')},
    )
    print(f'    [{tenant.slug}] item tracker ok (match {match.id})')
