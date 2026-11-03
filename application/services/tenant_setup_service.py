"""Tenant Setup Service - Business Logic Layer

Whether a community has what it needs to run, derived on every read.

Nothing here is stored: a tenant that deletes its last tournament becomes
un-set-up again, and the checklist says so. Storing progress would invent a
second source of truth that drifts the first time someone deletes a tournament.

Read-only — it takes no ``actor`` and writes no audit row.
"""

from dataclasses import dataclass
from typing import List

from application.repositories.stage_repository import StageRepository
from application.repositories.tournament_repository import TournamentRepository
from application.repositories.user_role_repository import UserRoleRepository
from application.services.system_config_service import (
    KEY_EVENT_END_DATE,
    KEY_EVENT_START_DATE,
    SystemConfigService,
)
from models import Role


@dataclass(frozen=True)
class SetupStep:
    key: str
    label: str
    done: bool
    hint: str
    tab: str
    required: bool


class TenantSetupService:
    """The setup checklist for a community, derived from its rows."""

    async def status(self) -> List[SetupStep]:
        """The checklist for the tenant in scope."""
        has_staff = await UserRoleRepository.any_with_role(Role.STAFF)
        has_tournament = await TournamentRepository.any_exists()
        has_enrolment = await TournamentRepository.any_enrolment()
        has_stage = await StageRepository.any_exists()
        start = await SystemConfigService.get_date(KEY_EVENT_START_DATE)
        end = await SystemConfigService.get_date(KEY_EVENT_END_DATE)

        return [
            SetupStep(
                key='staff',
                label='Give the community a staff member',
                done=has_staff,
                hint='Staff configure the community and grant its other roles.',
                tab='Members',
                required=True,
            ),
            SetupStep(
                key='tournament',
                label='Create a tournament',
                done=has_tournament,
                hint='Every match belongs to a tournament, so nothing can be '
                     'scheduled until one exists.',
                tab='Tournaments',
                required=True,
            ),
            SetupStep(
                key='enrolment',
                label='Enrol players in it',
                done=has_enrolment,
                hint='A match is between enrolled players — enrol them from the '
                     'tournament before scheduling.',
                tab='Tournaments',
                required=True,
            ),
            SetupStep(
                key='stage',
                label='Add a stage',
                done=has_stage,
                hint='One per stage or capture station you run matches on. A '
                     'match schedules without one.',
                tab='Stages',
                required=False,
            ),
            SetupStep(
                key='event_window',
                label='Set the event window',
                done=bool(start and end),
                hint='Leave it blank to derive the window from scheduled match '
                     'times instead.',
                tab='Settings',
                required=False,
            ),
        ]

    @staticmethod
    async def status_for(tenant_id: int) -> List[SetupStep]:
        """The checklist for a named tenant — the ``/platform`` caller.

        Wraps ``tenant_scope`` because ``/platform`` has no ambient tenant; the
        derivation itself is :meth:`status`, so the two entry points can never
        disagree.
        """
        from application.event_context import event_scope
        from application.services.event_service import EventService
        from application.tenant_context import tenant_scope

        with tenant_scope(tenant_id), event_scope(await EventService.default_event_id(tenant_id)):
            return await TenantSetupService().status()

    @staticmethod
    def is_ready(steps: List[SetupStep]) -> bool:
        return all(s.done for s in steps if s.required)

    @staticmethod
    def outstanding(steps: List[SetupStep]) -> List[SetupStep]:
        return [s for s in steps if s.required and not s.done]
