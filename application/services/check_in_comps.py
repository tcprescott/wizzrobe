"""Complimentary badges for event check-in.

An event's comp rules say who gets in without buying a badge: holders of the
roles in ``CheckInEvent.comp_roles``, and, with ``comp_volunteers`` on,
volunteers whose published shifts in the event window reach the community's
lowest volunteer comp tier (``SystemConfigService.get_volunteer_comp_tiers``,
8 hours for SGL). The hours are *scheduled*: at check-in most shifts haven't
happened yet, and it is the same total the volunteer roster shows.

:meth:`CheckInService.sync_event` works out who is comped once per sync
(:func:`comped_users`), lets the badge sync treat a comped buyer as holding a
badge, then :func:`apply_comps` reconciles the roster: the comp reasons on every
row, a ``COMP`` row for each comped person not already on it, and withdrawal of
a ``COMP`` row whose person stopped qualifying.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List

from application.repositories import CheckInEntrantRepository, UserRepository, UserRoleRepository
from application.services.check_in_rules import VOLUNTEER_COMP
from application.services.feature_flag_service import FeatureFlagService
from application.services.system_config_service import SystemConfigService
from application.services.user_service import UserService
from application.services.volunteer.volunteer_hours import VolunteerHoursService
from models import CheckInEntrant, CheckInEntrantSource, CheckInEvent, CheckInLinkMethod, FeatureFlag, Role


def has_comp_rules(event: CheckInEvent) -> bool:
    return bool(event.comp_roles) or event.comp_volunteers


@dataclass
class CompResult:
    added: int = 0
    withdrawn: int = 0
    rejoined: int = 0


async def comped_users(event: CheckInEvent) -> Dict[int, List[str]]:
    """Who the event comps, by user id, with why: role values in ``Role``
    order, then ``'volunteer'``."""
    reasons: Dict[int, List[str]] = {}
    for role in Role.tenant_grantable():
        if role.value not in (event.comp_roles or []):
            continue
        for user_id in await UserRoleRepository.user_ids_with_roles([role]):
            reasons.setdefault(user_id, []).append(role.value)
    if event.comp_volunteers and await FeatureFlagService().is_enabled(FeatureFlag.VOLUNTEERS):
        tiers = await SystemConfigService.get_volunteer_comp_tiers()
        if tiers:
            system = await UserService().get_system_user()
            for summary in await VolunteerHoursService().roster(actor=system):
                if summary.hours >= tiers[0]:
                    reasons.setdefault(summary.user_id, []).append(VOLUNTEER_COMP)
    return reasons


async def apply_comps(
    repo: CheckInEntrantRepository,
    event: CheckInEvent,
    comped: Dict[int, List[str]],
    now: datetime,
) -> CompResult:
    """Bring every row's comp reasons, and the ``COMP`` rows, in line with ``comped``.

    A comped person already on the roster (a buyer, a walk-up) keeps their row
    and gains the reasons. The badge sync has already kept a comped buyer from
    being withdrawn, so only ``COMP`` rows are withdrawn or restored here.
    """
    result = CompResult()
    rows = await repo.rows_by_user_id(event)
    missing = [user_id for user_id in comped if user_id not in rows]
    users = await UserRepository.get_by_ids(missing)
    for user_id in missing:
        user = users.get(user_id)
        if user is None or user.is_system or not user.is_active:
            continue
        await repo.create(
            event=event, source=CheckInEntrantSource.COMP, display_name=user.preferred_name[:255],
            user=user, link_method=CheckInLinkMethod.COMP, comp_reasons=comped[user_id],
        )
        result.added += 1

    for user_id, row in rows.items():
        reasons = comped.get(user_id)
        changes: List[str] = []
        if (row.comp_reasons or None) != reasons:
            row.comp_reasons = reasons
            changes.append('comp_reasons')
        if row.source == CheckInEntrantSource.COMP:
            if reasons and row.withdrawn_at is not None:
                row.withdrawn_at = None  # type: ignore[assignment]
                changes.append('withdrawn_at')
                result.rejoined += 1
            elif not reasons and row.withdrawn_at is None:
                row.withdrawn_at = now
                changes.append('withdrawn_at')
                result.withdrawn += 1
        if changes:
            await repo.save(row, [*changes, 'updated_at'])
    return result


def absorb_comp_row(comp_row: CheckInEntrant, row: CheckInEntrant) -> List[str]:
    """Fold a ``COMP`` row into the row that is about to take its account.

    Someone comped first and matched to a badge purchase later must stay one
    person on the roster: the purchase row inherits the comp and any check-in
    already recorded. Returns the fields changed on ``row``; the caller deletes
    ``comp_row`` before saving ``row``, since both can't hold the account.
    """
    changes: List[str] = []
    if comp_row.comp_reasons and row.comp_reasons != comp_row.comp_reasons:
        row.comp_reasons = comp_row.comp_reasons
        changes.append('comp_reasons')
    if comp_row.checked_in_at is not None and row.checked_in_at is None:
        row.checked_in_at = comp_row.checked_in_at
        row.checked_in_by_id = comp_row.checked_in_by_id
        changes += ['checked_in_at', 'checked_in_by_id']
    return changes
