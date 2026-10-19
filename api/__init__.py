"""Wizzrobe REST API package.

Exposes a single ``router`` (aggregating every domain sub-router) that
``main.py`` mounts under ``/api``. Every endpoint is authenticated with a
personal bearer token; see :mod:`api.dependencies`.
"""

from fastapi import APIRouter, Depends

from api.dependencies import require_feature, tenant_context_scope
from api.rate_limit import rate_limit
from api.routers import (
    async_qualifier_live_races,
    async_qualifiers,
    audit,
    brackets,
    check_in,
    crew,
    discord_events,
    discord_role_mappings,
    equipment,
    feedback,
    health,
    match_actions,
    matches,
    memberships,
    notification_settings,
    notifications,
    payouts,
    platform,
    player_availability,
    presets,
    race_room_profiles,
    race_rooms,
    racetime_bots,
    reports,
    reschedule_requests,
    seeds,
    sent_messages,
    service_health,
    speedgaming,
    stage_actions,
    stages,
    system_config,
    telemetry,
    tokens,
    tournament_actions,
    tournaments,
    triforce,
    users,
    version,
    volunteers,
    web_push,
    webhooks,
)
from models import FeatureFlag

router = APIRouter(dependencies=[Depends(rate_limit), Depends(tenant_context_scope)])
# Unauthenticated liveness probe and build version (no auth dependency on these routers).
router.include_router(health.router)
router.include_router(version.router)
router.include_router(matches.router)
router.include_router(match_actions.router)
router.include_router(crew.router)
# Literal ``/reschedule-requests/mine`` before the ``/{request_id}`` routes in
# the same module, which FastAPI resolves in declaration order.
router.include_router(reschedule_requests.router)
router.include_router(tournaments.router)
router.include_router(tournament_actions.router)
router.include_router(stages.router)
router.include_router(stage_actions.router)
router.include_router(users.router)
router.include_router(memberships.router)
# The literal ``/users/me/availability`` router must be included before the one
# carrying ``/users/{user_id}/availability``, or ``me`` matches the int path
# param and 422s instead of falling through.
router.include_router(player_availability.router)
router.include_router(player_availability.other_players_router)
router.include_router(
    triforce.router,
    dependencies=[Depends(require_feature(FeatureFlag.TRIFORCE_TEXTS))],
)
router.include_router(
    payouts.router,
    dependencies=[Depends(require_feature(FeatureFlag.PAYOUTS))],
)
router.include_router(notifications.router)
router.include_router(audit.router)
router.include_router(sent_messages.router)
router.include_router(system_config.router)
router.include_router(notification_settings.router)
router.include_router(tokens.router)
router.include_router(
    volunteers.router,
    dependencies=[Depends(require_feature(FeatureFlag.VOLUNTEERS))],
)
router.include_router(discord_role_mappings.router)
router.include_router(reports.router)
router.include_router(telemetry.router)
router.include_router(
    equipment.router,
    dependencies=[Depends(require_feature(FeatureFlag.EQUIPMENT))],
)
router.include_router(
    feedback.router,
    dependencies=[Depends(require_feature(FeatureFlag.FEEDBACK))],
)
# Also no auth dependency: the service worker that calls it has no session to
# present, and proves ownership of the subscription instead (see the module).
router.include_router(web_push.router)
router.include_router(webhooks.router)

# --- online-tournament feature routers ---
# Feature-gated per tenant (mirrors the web UI's per-tenant flags). ``presets``,
# ``seeds``, ``racetime_bots`` (platform infra), ``discord_events`` (ungated),
# and ``service_health`` stay open; the rest 404 when the tenant lacks the flag.
router.include_router(presets.router)
router.include_router(
    race_room_profiles.router,
    dependencies=[Depends(require_feature(FeatureFlag.RACETIME_ROOMS))],
)
router.include_router(racetime_bots.router)
router.include_router(platform.router)
router.include_router(
    race_rooms.router,
    dependencies=[Depends(require_feature(FeatureFlag.RACETIME_ROOMS))],
)
router.include_router(
    speedgaming.router,
    dependencies=[Depends(require_feature(FeatureFlag.SPEEDGAMING_ETL))],
)
router.include_router(discord_events.router)
router.include_router(
    brackets.router,
    dependencies=[Depends(require_feature(FeatureFlag.BRACKETS))],
)
router.include_router(service_health.router)
router.include_router(
    check_in.router,
    dependencies=[Depends(require_feature(FeatureFlag.EVENT_CHECK_IN))],
)
router.include_router(seeds.router)
# Live-races MUST be registered before the async-qualifiers router: its literal
# ``/async-qualifiers/live-races`` prefix would otherwise be shadowed by the
# ``/async-qualifiers/{qualifier_id}`` int path param (a non-int segment 422s
# rather than falling through), so the more specific router is included first.
router.include_router(
    async_qualifier_live_races.router,
    dependencies=[Depends(require_feature(FeatureFlag.ASYNC_QUALIFIERS))],
)
router.include_router(
    async_qualifiers.router,
    dependencies=[Depends(require_feature(FeatureFlag.ASYNC_QUALIFIERS))],
)

__all__ = ['router']
