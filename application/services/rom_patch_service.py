"""
ROM Patch Service - Business Logic Layer

ROM patches a community hosts for its players: staff upload a BPS, IPS or UPS
file under Admin → Patches and share its ``/patch/<token>`` link, where each
player's own ROM is patched in their browser. The server never sees a ROM.

Managing patches takes ``AuthService.can_manage_presets`` (staff or a preset
manager), the same people who look after a community's seeds. Reading one is
anyone with the link: the token is the secret, as for door seeds.
"""

import re
import secrets
from typing import List, Optional

from application.errors import NotFoundError, require_found
from application.repositories import RomPatchRepository, TournamentRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.utils.rom_patch_format import normalize_crc, read_patch
from application.utils.rom_patch_store import RomPatchStore
from models import RomPatch, Tournament, User

MAX_TITLE = 120
MAX_GAME = 120
MAX_DESCRIPTION = 2000
MAX_FILENAME = 200


def _text(value: Optional[str], label: str, limit: int, required: bool = True) -> Optional[str]:
    value = (value or '').strip()
    if not value:
        if required:
            raise ValueError(f'{label} is required.')
        return None
    if len(value) > limit:
        raise ValueError(f'{label} can be up to {limit} characters.')
    return value


def output_base(title: str) -> str:
    """A filename stem from a patch title: ``"SM: SGL practice hack"`` → ``SM_SGL_practice_hack``."""
    stem = re.sub(r'[^A-Za-z0-9]+', '_', title).strip('_')
    return stem[:80] or 'patched'


def _filename(value: Optional[str]) -> str:
    name = (value or '').replace('\\', '/').rsplit('/', 1)[-1].strip() or 'patch'
    return name[-MAX_FILENAME:]


class RomPatchService:
    def __init__(self, store: Optional[RomPatchStore] = None) -> None:
        self.store = store or RomPatchStore()
        self.audit_service = AuditService()

    @staticmethod
    async def _ensure_manager(actor: Optional[User]) -> User:
        await AuthService.ensure(
            actor is not None and await AuthService.can_manage_presets(actor),
            'You do not have permission to manage patches',
        )
        assert actor is not None
        return actor

    @staticmethod
    async def _tournament_id(tournament_id: Optional[int]) -> Optional[int]:
        if tournament_id is None:
            return None
        require_found(await TournamentRepository.get_by_id(tournament_id), 'Tournament')
        return tournament_id

    @staticmethod
    def _source_crc(fmt: str, from_file: Optional[str], typed: Optional[str]) -> Optional[str]:
        # BPS and UPS name their ROM themselves, and that always wins over a typed one.
        return from_file if fmt != 'ips' else normalize_crc(typed)

    async def list_patches(self, actor: Optional[User]) -> List[RomPatch]:
        await self._ensure_manager(actor)
        return await RomPatchRepository.list_all()

    async def _get(self, patch_id: int) -> RomPatch:
        return require_found(await RomPatchRepository.get_by_id(patch_id), 'Patch')

    async def create_patch(
        self,
        actor: Optional[User],
        *,
        title: str,
        game: str,
        filename: str,
        data: bytes,
        description: Optional[str] = None,
        tournament_id: Optional[int] = None,
        source_crc: Optional[str] = None,
    ) -> RomPatch:
        actor = await self._ensure_manager(actor)
        title_v = _text(title, 'Title', MAX_TITLE)
        game_v = _text(game, 'Game', MAX_GAME)
        description_v = _text(description, 'Description', MAX_DESCRIPTION, required=False)
        info = read_patch(data)
        crc = self._source_crc(info.format, info.source_crc, source_crc)
        tid = await self._tournament_id(tournament_id)
        token = secrets.token_urlsafe(16)
        await self.store.put(token, info.format, data)
        patch = await RomPatchRepository.create(
            token=token, title=title_v, game=game_v, description=description_v,
            format=info.format, filename=_filename(filename), size=len(data),
            source_crc=crc, tournament_id=tid, uploaded_by=actor,
        )
        await self.audit_service.write_log(
            actor, AuditActions.ROM_PATCH_CREATED,
            {'rom_patch_id': patch.id, 'title': patch.title, 'format': patch.format, 'source_crc': crc},
        )
        return patch

    async def update_patch(
        self,
        actor: Optional[User],
        patch_id: int,
        *,
        title: str,
        game: str,
        description: Optional[str] = None,
        tournament_id: Optional[int] = None,
        source_crc: Optional[str] = None,
    ) -> RomPatch:
        actor = await self._ensure_manager(actor)
        patch = await self._get(patch_id)
        fields = {
            'title': _text(title, 'Title', MAX_TITLE),
            'game': _text(game, 'Game', MAX_GAME),
            'description': _text(description, 'Description', MAX_DESCRIPTION, required=False),
            'tournament_id': await self._tournament_id(tournament_id),
        }
        if patch.format == 'ips':
            fields['source_crc'] = normalize_crc(source_crc)
        patch = await RomPatchRepository.update(patch, **fields)
        await self.audit_service.write_log(
            actor, AuditActions.ROM_PATCH_UPDATED, {'rom_patch_id': patch.id, 'title': patch.title},
        )
        return patch

    async def replace_file(
        self, actor: Optional[User], patch_id: int, *, filename: str, data: bytes,
        source_crc: Optional[str] = None,
    ) -> RomPatch:
        """A new file behind the same link, so a posted link keeps working."""
        actor = await self._ensure_manager(actor)
        patch = await self._get(patch_id)
        info = read_patch(data)
        old_format = patch.format
        crc = self._source_crc(info.format, info.source_crc, source_crc or patch.source_crc)
        await self.store.put(patch.token, info.format, data)
        patch = await RomPatchRepository.update(
            patch, format=info.format, filename=_filename(filename), size=len(data), source_crc=crc,
        )
        if old_format != info.format:
            await self.store.delete(patch.token, old_format)
        await self.audit_service.write_log(
            actor, AuditActions.ROM_PATCH_REPLACED,
            {'rom_patch_id': patch.id, 'format': patch.format, 'source_crc': crc},
        )
        return patch

    async def delete_patch(self, actor: Optional[User], patch_id: int) -> None:
        actor = await self._ensure_manager(actor)
        patch = await self._get(patch_id)
        details = {'rom_patch_id': patch.id, 'title': patch.title}
        await RomPatchRepository.delete(patch)
        await self.store.delete(patch.token, patch.format)
        await self.audit_service.write_log(actor, AuditActions.ROM_PATCH_DELETED, details)

    @staticmethod
    async def get_by_token(token: str) -> RomPatch:
        return require_found(await RomPatchRepository.get_by_token(token), 'Patch')

    async def patch_file(self, token: str) -> tuple[RomPatch, bytes]:
        patch = await self.get_by_token(token)
        return patch, require_found(await self.store.get(patch.token, patch.format), 'Patch file')

    async def tournament_choices(self, actor: Optional[User]) -> List[Tournament]:
        await self._ensure_manager(actor)
        return await TournamentRepository.get_all()

    async def get_for_manager(self, actor: Optional[User], patch_id: int) -> RomPatch:
        await self._ensure_manager(actor)
        return await self._get(patch_id)

    @staticmethod
    async def record_download(token: str) -> None:
        patch = await RomPatchRepository.get_by_token(token)
        if patch is None:
            raise NotFoundError('Patch not found.')
        await RomPatchRepository.count_download(patch.id)
