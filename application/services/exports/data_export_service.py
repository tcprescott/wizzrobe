"""
Data Export Service - Business Logic Layer

Every export a community's staff take out of Wizzrobe goes through here, so they
all share one list (Reports → Exports, ``GET /api/exports``, the MCP export
tools) and one set of limits.

Exports that read the database are **queued**: a request writes a row and
returns at once, and ``data_export_worker`` builds one export at a time across
the whole app, pausing between tournaments, so a big export can't crowd out
live traffic. A table's "Export CSV" button only saves rows already on the
screen, so it downloads at once and is just recorded (``record_table_csv``).

Finished files are kept for :data:`RETENTION` and then deleted.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from application.errors import require_found
from application.event_context import event_scope
from application.repositories import (
    DataExportRepository,
    TenantRepository,
    TournamentExportRepository,
)
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.exports import tournament_export_builder as builder
from application.services.feature_flag_service import FeatureFlagService
from application.services.tournament_service import TournamentService
from application.tenant_context import require_tenant_id
from application.timezone_context import current_timezone_name, hour_cycle_scope, tz_scope
from application.utils.timezone import format_local_date
from models import DataExport, DataExportKind, DataExportStatus, FeatureFlag, Tournament, User

logger = logging.getLogger(__name__)

#: How long a finished export stays downloadable before the worker deletes it.
RETENTION = timedelta(days=3)
#: Background exports one person may have waiting or building at once.
MAX_PENDING_PER_PERSON = 1
MAX_PER_HOUR = 10
#: Background exports a community may have waiting or building at once.
MAX_PENDING_PER_COMMUNITY = 5
#: Breathing room between tournaments while a big export builds.
PAUSE_BETWEEN_TOURNAMENTS = 0.25
#: A RUNNING export older than this was cut off by a restart.
INTERRUPTED_AFTER = timedelta(minutes=15)

INTERRUPTED_ERROR = 'The server restarted while this export was building. Request it again.'
_GENERIC_ERROR = 'Something went wrong building this export. Try again, or tell the Wizzrobe team if it keeps failing.'

_ZIP = 'application/zip'
_JSON = 'application/json'
_CSV = 'text/csv'


@dataclass(frozen=True)
class ExportFile:
    filename: str
    content: bytes
    media_type: str


class DataExportService:
    """Request, build, list and download exports."""

    def __init__(self) -> None:
        self.repository = DataExportRepository()
        self.audit_service = AuditService()

    @staticmethod
    async def exportable_tournaments(actor: Optional[User]) -> List[Tournament]:
        """Every tournament for staff; a tournament admin's own otherwise."""
        tournaments = await TournamentService().get_all_tournaments()
        if await AuthService.is_staff(actor):
            return tournaments
        admin_ids, _ = await AuthService.tournament_scope(actor)
        return [t for t in tournaments if t.id in admin_ids]

    @staticmethod
    async def can_export(actor: Optional[User]) -> bool:
        """Whether Reports → Exports has anything for ``actor``."""
        if await AuthService.is_staff(actor):
            return True
        admin_ids, _ = await AuthService.tournament_scope(actor)
        return bool(admin_ids)

    async def tournament_overview(self, actor: User) -> List[Dict]:
        """The tournaments ``actor`` may export, with counts for the picker: three queries."""
        tournaments = await self.exportable_tournaments(actor)
        ids = [t.id for t in tournaments]
        if not ids:
            return []
        players = await TournamentExportRepository.player_counts(ids)
        matches = await TournamentExportRepository.match_counts(ids)
        native = await TournamentExportRepository.with_native_bracket(ids)
        rows = []
        for t in tournaments:
            total, finished = matches.get(t.id, (0, 0))
            rows.append({
                'id': t.id, 'name': t.name, 'active': t.is_active, 'test': t.is_test,
                'players': players.get(t.id, 0), 'matches': total, 'finished': finished,
                'bracket_site': builder.bracket_site(t, t.id in native),
            })
        return rows

    async def request_tournaments(
        self, actor: User, tournament_ids: Optional[List[int]] = None,
    ) -> DataExport:
        """Queue a participants + matches export. ``None`` means every tournament ``actor`` may export."""
        allowed = await self.exportable_tournaments(actor)
        await AuthService.ensure(
            bool(allowed) or await AuthService.is_staff(actor),
            'Only staff and tournament admins can export tournaments.',
        )
        allowed_ids = {t.id for t in allowed}
        if tournament_ids is None:
            chosen = [t for t in allowed]
        else:
            wanted = list(dict.fromkeys(int(i) for i in tournament_ids))
            refused = [i for i in wanted if i not in allowed_ids]
            if refused:
                raise PermissionError(
                    "You can only export tournaments you run here. "
                    f"Not allowed: {', '.join(str(i) for i in refused)}."
                )
            chosen = [t for t in allowed if t.id in set(wanted)]
        if not chosen:
            raise ValueError('Pick at least one tournament to export.')
        if len(chosen) == 1:
            title = chosen[0].name
        elif len(chosen) == len(allowed):
            title = f'All {len(chosen)} tournaments'
        else:
            title = f'{len(chosen)} tournaments'
        return await self._queue(
            actor, DataExportKind.TOURNAMENTS, title,
            {'tournament_ids': [t.id for t in chosen]}, total=len(chosen),
        )

    async def request_volunteers(self, actor: User, start: datetime, end: datetime) -> DataExport:
        """Queue the volunteer data ZIP for ``[start, end)``, rendered on the requester's clock."""
        if not await FeatureFlagService().is_enabled(FeatureFlag.VOLUNTEERS):
            raise ValueError('Volunteer scheduling is off for this community.')
        await AuthService.ensure(
            await AuthService.can_manage_volunteers(actor),
            'Only volunteer coordinators can export volunteer data.',
        )
        if end <= start:
            raise ValueError('The export window must end after it starts.')
        last_day = end - timedelta(seconds=1)
        title = f'Volunteer data, {format_local_date(start)} to {format_local_date(last_day)}'
        return await self._queue(
            actor, DataExportKind.VOLUNTEERS, title,
            {'start': start.isoformat(), 'end': end.isoformat(), 'timezone': current_timezone_name()},
            total=1,
        )

    async def record_table_csv(
        self, actor: User, title: str, filename: str, content: bytes,
    ) -> DataExport:
        """Record a table CSV that already downloaded, so it shows in the exports list."""
        return await self.repository.create_ready(
            requested_by_id=actor.id, kind=DataExportKind.TABLE_CSV,
            title=title[:255], params={}, filename=filename, media_type=_CSV,
            content=content, size_bytes=len(content),
            progress_done=1, progress_total=1,
        )

    async def _queue(
        self, actor: User, kind: DataExportKind, title: str, params: Dict, *, total: int,
    ) -> DataExport:
        if await self.repository.count_pending_for_requester(actor.id) >= MAX_PENDING_PER_PERSON:
            raise ValueError('You already have an export building. Wait for it to finish, then try again.')
        since = datetime.now(timezone.utc) - timedelta(hours=1)
        if await self.repository.count_queued_since(actor.id, since) >= MAX_PER_HOUR:
            raise ValueError(f'You can request {MAX_PER_HOUR} exports an hour. Try again later.')
        if await self.repository.count_pending_in_tenant() >= MAX_PENDING_PER_COMMUNITY:
            raise ValueError('Several exports are already building for this community. Try again in a few minutes.')
        return await self.repository.create_queued(
            requested_by_id=actor.id, kind=kind, title=title[:255],
            params=params, progress_total=total,
        )

    async def list_mine(self, actor: User, limit: int = 20) -> List[DataExport]:
        return await self.repository.list_for_requester(actor.id, limit=limit)

    async def get_mine(self, actor: User, export_id: int) -> DataExport:
        """One of ``actor``'s exports, without its files. Someone else's reads as not found."""
        export = await self.repository.summary(export_id)
        if export is not None and export.requested_by_id != actor.id:  # type: ignore[attr-defined]
            export = None
        return require_found(export, 'Export')

    async def download(self, actor: User, export_id: int, fmt: str = 'file') -> ExportFile:
        """The finished file (``fmt='file'``) or its JSON copy (``fmt='json'``)."""
        await self.get_mine(actor, export_id)
        export = require_found(await self.repository.get_by_id(export_id), 'Export')
        if export.status != DataExportStatus.READY:
            raise ValueError('This export is not ready yet.')
        if fmt == 'json':
            if export.json_content is None:
                raise ValueError('This export has no JSON copy.')
            stem = (export.filename or 'export').rsplit('.', 1)[0]
            return ExportFile(f'{stem}.json', bytes(export.json_content), _JSON)
        return ExportFile(export.filename or 'export', bytes(export.content or b''), export.media_type or _ZIP)

    @staticmethod
    def expires_at(export: DataExport) -> datetime:
        return export.created_at + RETENTION

    async def build(self, export: DataExport) -> None:
        """Build a claimed export inside its own tenant scope. Never raises.

        It reads the event it was asked for in, archived ones included (last
        year's results are what people export), so the worker binds that event
        here rather than through ``for_each_tenant_scoped``, which skips them.
        """
        try:
            actor = await self._requester(export)
            with event_scope(export.event_id):
                if export.kind == DataExportKind.TOURNAMENTS:
                    files = await self._build_tournaments(export, actor)
                elif export.kind == DataExportKind.VOLUNTEERS:
                    files = await self._build_volunteers(export, actor)
                else:
                    raise ValueError(f'Exports of kind {export.kind.value} are not built in the background.')
        except (ValueError, PermissionError) as exc:
            await self.repository.finish(export.id, status=DataExportStatus.FAILED, error=str(exc)[:500])
            return
        except Exception:
            logger.exception('data export %s (%s) failed', export.id, export.kind.value)
            await self.repository.finish(export.id, status=DataExportStatus.FAILED, error=_GENERIC_ERROR)
            return
        content, json_content, filename, media_type = files
        await self.repository.finish(
            export.id, status=DataExportStatus.READY, content=content, json_content=json_content,
            filename=filename, media_type=media_type,
            size_bytes=len(content) + len(json_content or b''),
            progress_done=export.progress_total,
        )

    @staticmethod
    async def _requester(export: DataExport) -> User:
        from application.services.user_service import UserService

        if export.requested_by_id is None:  # type: ignore[attr-defined]
            raise ValueError('The person who asked for this export no longer has an account.')
        return require_found(await UserService().get_user_by_id(export.requested_by_id), 'User')  # type: ignore[attr-defined]

    async def _build_tournaments(self, export: DataExport, actor: User):
        allowed = {t.id for t in await self.exportable_tournaments(actor)}
        ids = [i for i in export.params.get('tournament_ids', []) if i in allowed]
        if not ids:
            raise ValueError('You can no longer export any of these tournaments.')
        tournaments = await TournamentExportRepository.tournaments(ids)
        sections = []
        for done, tournament in enumerate(tournaments, start=1):
            sections.append(await builder.build_tournament(tournament))
            await self.repository.set_progress(export.id, done, len(tournaments))
            await asyncio.sleep(PAUSE_BETWEEN_TOURNAMENTS)
        tenant = await TenantRepository.get_by_id(require_tenant_id())
        slug = tenant.slug if tenant else 'community'
        doc = builder.document(slug, tenant.name if tenant else '', sections)
        zip_bytes, json_bytes = builder.zip_bundle(doc)
        await self.audit_service.write_log(
            actor, AuditActions.TOURNAMENT_DATA_EXPORTED,
            {
                'export_id': export.id,
                'tournament_ids': [s['id'] for s in sections],
                'participants': sum(len(s['participants']) for s in sections),
                'matches': sum(len(s['matches']) for s in sections),
            },
        )
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')
        return zip_bytes, json_bytes, f'{slug}-tournaments-{stamp}.zip', _ZIP

    async def _build_volunteers(self, export: DataExport, actor: User):
        from application.services.volunteer.volunteer_export_service import VolunteerExportService

        start = datetime.fromisoformat(export.params['start'])
        end = datetime.fromisoformat(export.params['end'])
        # A CSV can be forwarded, so it's on the default 12-hour clock whoever started it.
        with tz_scope(export.params.get('timezone')), hour_cycle_scope(True):
            bundle = await VolunteerExportService().build(actor, start, end)
            content = bundle.to_zip_bytes()
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')
        return content, None, f'volunteer-data-{stamp}.zip', _ZIP

