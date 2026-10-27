"""Feature-flag registry — the catalog of per-tenant feature flags.

A feature flag exists only when a feature is deliberately gated behind one; this
is **not** a per-feature switch for everything the app does. Flags default OFF
and are governed two-tier:

* a **super-admin** makes a flag *available* to a tenant on ``/platform``; then
* that tenant's **STAFF** *enable* it for their community (Admin → Settings → Features).

Effective state = ``available AND enabled`` (see ``FeatureFlagService``).

This module is pure metadata — a peer of ``application/events`` and
``application/tenant_context`` (import-safe from every layer, including
repositories and pages). The stable enum keys live in
:class:`models.enums.FeatureFlag`; the human copy and grouping live here so the
UI and the migration/seed have one source of truth.

**Adding a flag:** add a member to :class:`~models.enums.FeatureFlag` and a
:class:`FeatureFlagSpec` here. Mark ``established=True`` only for a feature that
is *already in live use* when you gate it — the migration backfills such flags
as available+enabled for existing tenants so gating them does not make them
vanish. New/unreleased features leave it False and ship dark.
"""

import functools
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Tuple

from models import FeatureFlag


@dataclass(frozen=True)
class FeatureFlagSpec:
    flag: FeatureFlag
    label: str
    description: str
    category: str
    established: bool = False
    #: Modules that must enforce the flag at the service layer, repo-relative
    #: (e.g. ``'application/services/bracket_service.py'``) or a package prefix
    #: (``'application/services/volunteer/'``) when the subsystem spans files.
    #: The ``check_feature_flag_gating`` hook asserts each one actually guards the
    #: flag, so a new gated subsystem cannot ship with UI-only hiding.
    service_modules: Tuple[str, ...] = field(default_factory=tuple)


def requires_feature(flag: FeatureFlag) -> Callable:
    """Refuse to run a service method unless ``flag`` is live for this tenant.

    The service-layer half of feature gating. Entry surfaces (page decorator,
    ``require_feature`` on routers, admin-tab conditions) *hide* a disabled
    feature; this makes the owning service *refuse* it, so a caller that has no
    gate — a newly added page, a Discord interaction handler, a worker, a REST
    router someone forgot to mount behind ``require_feature`` — cannot reach
    functionality the community has not enabled. Both halves are required; see
    docs/features/feature-flags.md.

    Raises :class:`~application.errors.FeatureDisabledError` (a ``NotFoundError``,
    so REST answers 404 and UI ``except ValueError`` shows a notification).

    Placement: the service's **public entry methods** — every mutation, plus the
    top-level reads that return the feature's data. Not internal helpers, and
    never inside a transaction; the check is an authorization-style gate at the
    service boundary, and it reads a per-request cache so repeated guards in one
    request cost a single pass.

    Tolerates no tenant in scope by raising, like every other gate: a flag is
    never live without a tenant.
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Imported lazily: this module is pure metadata that repositories and
            # pages import freely, while FeatureFlagService pulls in services.
            from application.services.feature_flag_service import FeatureFlagService

            await FeatureFlagService().ensure_enabled(flag)
            return await func(*args, **kwargs)

        wrapper.__wizzrobe_feature__ = flag  # what the guardrail hook reads
        return wrapper
    return decorator


# Declaration order is preserved (dict is ordered) and drives UI ordering.
FEATURE_FLAG_REGISTRY: Dict[FeatureFlag, FeatureFlagSpec] = {
    spec.flag: spec
    for spec in (
        FeatureFlagSpec(
            FeatureFlag.ASYNC_QUALIFIERS,
            'Async Qualifiers',
            'Self-paced permalink-pool qualifiers with their own leaderboard, '
            'run review, and optional live races.',
            'Online tournaments',
            service_modules=('application/services/async_qualifier/',),
        ),
        FeatureFlagSpec(
            FeatureFlag.RACETIME_ROOMS,
            'Racetime Rooms',
            'Automated racetime.gg race rooms driven from scheduled matches '
            '(open, attach seed, finish, record results).',
            'Online tournaments',
            # The auto-open worker deliberately has no check of its own: every
            # room it opens goes through RaceRoomService.auto_open_if_eligible,
            # which enforces the flag. Declaring the worker too would only invite
            # a redundant second read.
            service_modules=('application/services/race_room_service.py',),
        ),
        FeatureFlagSpec(
            FeatureFlag.SPEEDGAMING_ETL,
            'SpeedGaming Schedule Sync',
            "One-way import of the SpeedGaming schedule into this community's "
            'matches.',
            'Online tournaments',
            service_modules=('application/services/speedgaming_sync_service.py',
                             'application/services/speedgaming_sync_worker.py'),
        ),
        FeatureFlagSpec(
            FeatureFlag.CHALLONGE,
            'Challonge Integration',
            'Connect a Challonge account to mirror brackets, schedule from '
            'bracket matches, and push results.',
            'Community',
            established=True,
            service_modules=('application/services/challonge_service.py',
                             'application/services/_challonge_participants.py',
                             'application/services/_challonge_stages.py',
                             'application/services/_challonge_link.py',
                             'application/services/_challonge_results.py',
                             'application/services/_challonge_auto_sync.py'),
        ),
        FeatureFlagSpec(
            FeatureFlag.EQUIPMENT,
            'Equipment Lending',
            'Track lending assets with checkout/check-in, loan history, and QR '
            'codes.',
            'Community',
            established=True,
            service_modules=('application/services/equipment_service.py',),
        ),
        FeatureFlagSpec(
            FeatureFlag.VOLUNTEERS,
            'Volunteer Scheduling',
            'Volunteer positions, shifts, availability, the '
            'auto-scheduler, and reminders.',
            'Community',
            established=True,
            service_modules=('application/services/volunteer/',),
        ),
        FeatureFlagSpec(
            FeatureFlag.TRIFORCE_TEXTS,
            'Triforce Texts',
            'Player-submitted triforce texts with admin moderation '
            '(ALTTPR-specific).',
            'Community',
            established=True,
            service_modules=('application/services/triforce_text_service.py',),
        ),
        FeatureFlagSpec(
            FeatureFlag.BRACKETS,
            'Native Brackets',
            'Generate and run tournament brackets natively (single/double '
            'elimination, Swiss, round robin, and multi-stage chains) without '
            'the Challonge API.',
            'Online tournaments',
            service_modules=('application/services/bracket_service.py',),
        ),
        FeatureFlagSpec(
            FeatureFlag.FEEDBACK,
            'In-app Feedback',
            'A feedback form in the sidebar, a staff review queue, and each '
            "person's own list of what they sent and whether it has been read.",
            'Community',
            # Already in live use, so it backfills available+enabled rather than
            # disappearing for communities currently collecting feedback
            # (migration 51).
            established=True,
            service_modules=('application/services/feedback_service.py',),
        ),
        FeatureFlagSpec(
            FeatureFlag.EVENT_INFO,
            'Event Information',
            "A public handbook for this community's own event — what's on, "
            'attending, and who to ask — separate from the app-mechanics help.',
            'Community',
            service_modules=('application/services/event_info_service.py',),
        ),
        FeatureFlagSpec(
            FeatureFlag.ITEM_TRACKER,
            'Item Tracker',
            'Approved trackers mark what an ALTTPR runner collects on a board, '
            'and a transparent page shows it on stream after a delay you set.',
            'Operations',
            service_modules=('application/services/item_tracker_service.py',),
        ),
        FeatureFlagSpec(
            FeatureFlag.PAYOUTS,
            'Prize Payouts',
            "Prize pool, placement splits and Matcherino handles for a "
            "tournament's winners, with an export for the payout run.",
            'Operations',
            service_modules=('application/services/payout_service.py',
                             'application/services/sglive_pool_worker.py'),
        ),
        FeatureFlagSpec(
            FeatureFlag.ADA_ACCOMMODATIONS,
            'ADA Accommodations',
            'Members can ask for ADA accommodation from their profile; staff '
            'track each request, its status and their own notes.',
            'Community',
            service_modules=('application/services/accommodation_service.py',),
        ),
        FeatureFlagSpec(
            FeatureFlag.EMERGENCY_CONTACTS,
            'Emergency Contacts',
            'Members can add an emergency contact on their profile; staff can '
            'open it when something happens, and every look is logged.',
            'Community',
            service_modules=('application/services/emergency_contact_service.py',),
        ),
        FeatureFlagSpec(
            FeatureFlag.NOTIFICATION_CHANNELS,
            'Email and Telegram Notifications',
            'Members can also get their notifications by email or Telegram, '
            'confirmed from their profile. Uses the platform SendGrid and '
            'Telegram bot accounts.',
            'Community',
            service_modules=('application/services/notification_channel_service.py',),
        ),
        FeatureFlagSpec(
            FeatureFlag.STARTGG,
            'start.gg Brackets',
            'Link a start.gg event to show its pools and bracket in Wizzrobe and '
            'let players book their matches. Results are recorded on start.gg; each '
            'tournament can opt in to reporting confirmed results back.',
            'Community',
            service_modules=(
                'application/services/startgg_service.py',
                'application/services/startgg_connection_service.py',
            ),
        ),
        FeatureFlagSpec(
            FeatureFlag.DISCORD_ROLE_SYNC,
            'Discord Roles from Wizzrobe',
            "Hand out roles in the community's Discord server to players, "
            "people in a match right now, each tournament's entrants and badge "
            'holders. Only roles Wizzrobe gave are ever taken back.',
            'Community',
            service_modules=('application/services/discord/discord_role_sync_service.py',),
        ),
        FeatureFlagSpec(
            FeatureFlag.PERSONAL_WEBHOOKS,
            'Personal Webhooks',
            'Members can send what happens to them, or anything else they can see, '
            'to a Discord channel or their own app from their profile. Three per '
            'member; staff-only details are never sent.',
            'Community',
            service_modules=('application/services/personal_webhook_service.py',),
        ),
        FeatureFlagSpec(
            FeatureFlag.STREAM_LAYOUTS,
            'Stream Layouts',
            'Design broadcast overlays for each event: upload the art, place '
            'player names, round, commentators and more on it, and load each '
            "stage's overlay in OBS on top of the video feeds.",
            'Operations',
            service_modules=('application/services/stream_layout_service.py',),
        ),
        FeatureFlagSpec(
            FeatureFlag.COMMUNITY_EVENTS,
            'Meetups',
            'Members submit the meetups, panels and side events they are running '
            'during the event; staff approve them, and anyone can subscribe for a '
            'Discord reminder before one starts.',
            'Community',
            service_modules=('application/services/community_event_service.py',),
        ),
        FeatureFlagSpec(
            FeatureFlag.EVENT_CHECK_IN,
            'Event Check-in',
            'A phone-friendly check-in desk for in-person events, with the '
            "roster pulled from the event's Matcherino registrations.",
            'Operations',
            service_modules=('application/services/check_in_service.py',
                             'application/services/check_in_desk_service.py',
                             'application/services/check_in_sync_worker.py',
                             'application/services/matcherino_login_service.py'),
        ),
        FeatureFlagSpec(
            FeatureFlag.THIRD_PARTY_APPS,
            'Third-party Apps',
            'Members can sign in to outside apps with their Wizzrobe account and '
            'let them use the API on their behalf, limited to what they approve.',
            'Community',
            service_modules=('application/services/oauth_grant_service.py',),
        ),
        FeatureFlagSpec(
            FeatureFlag.SURVEYS,
            'Surveys',
            'Send a short survey after an event by Discord DM. Each person only '
            'gets the questions about what they did: the tournaments they played, '
            'the positions they worked, and the rest.',
            'Community',
            service_modules=('application/services/survey/',),
        ),
    )
}


def all_specs() -> List[FeatureFlagSpec]:
    """Every known flag spec, in declaration order."""
    return list(FEATURE_FLAG_REGISTRY.values())


def spec_for(flag: FeatureFlag) -> FeatureFlagSpec:
    return FEATURE_FLAG_REGISTRY[flag]


def established_flags() -> List[FeatureFlag]:
    """Flags for features already in live use — backfilled on existing tenants."""
    return [spec.flag for spec in all_specs() if spec.established]
