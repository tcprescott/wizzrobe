"""
Personal Preset Service - Business Logic Layer

Every player can keep presets of their own, in a personal space named like a
SahasrahBot namespace (``tom/fastboots``). The space is made the first time
they save or copy a preset, its slug taken from their name; the owner can
rename it and add co-editors, who can add, edit and delete its presets but
can't rename the space or change who edits it.

Everything in a personal space shows in the library: there are no private
personal presets. A personal preset never belongs to a community, so no
tournament can point at one; staff copy it into their community first.

Players are untrusted, so names and slugs are short and plain, settings are
capped in size, each space holds a limited number of presets and co-editors,
and saves are rate-limited per person.
"""

import json
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional, Tuple

from application.errors import NotFoundError
from application.repositories import PresetLibraryRepository, PresetSpaceRepository, UserRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.preset_library_service import PresetLibraryService
from application.services.preset_service import PresetService
from application.services.seedgen_service import SeedGenerationService
from application.services.tenant_service import TenantService
from application.services.user_service import UserService
from application.tenant_context import require_tenant_id
from application.utils.clients import sahasrahbot_client as sahasrahbot
from models import Preset, PresetSpace, PresetSpaceKind, User

MAX_PRESETS_PER_SPACE = 50
MAX_EDITORS_PER_SPACE = 10
MAX_SETTINGS_BYTES = 64 * 1024
MAX_DESCRIPTION_CHARS = 2000
WRITES_PER_WINDOW = 30
WRITE_WINDOW_SECONDS = 600

_NAME_RE = re.compile(r'^[a-z0-9_-]{2,32}$')
_NAME_RULE = "2 to 32 lowercase letters, numbers, - or _"
_NAMESPACE_RE = re.compile(r'^[A-Za-z0-9_.-]{1,64}$')
RESERVED_SLUGS = frozenset({'wizzrobe', 'wizzrobe-presets'})
MAX_IMPORT_PER_CALL = 25

_writes: Dict[int, Deque[float]] = defaultdict(deque)


def _take_write_slot(user_id: int) -> bool:
    now = time.monotonic()
    hits = _writes[user_id]
    while hits and hits[0] < now - WRITE_WINDOW_SECONDS:
        hits.popleft()
    if len(hits) >= WRITES_PER_WINDOW:
        return False
    hits.append(now)
    return True


def reset_write_limits() -> None:
    _writes.clear()


def slugify(text: str) -> str:
    """A best-effort name in the allowed alphabet, for a default or a suggestion."""
    slug = re.sub(r'[^a-z0-9_-]+', '-', (text or '').lower()).strip('-_')
    return slug[:32].strip('-_')


@dataclass(frozen=True)
class SahasrahBotPreset:
    randomizer: str
    name: str
    exists: bool


@dataclass(frozen=True)
class ImportResult:
    randomizer: str
    name: str
    preset_id: Optional[int]
    problem: Optional[str]


@dataclass(frozen=True)
class SpaceView:
    """A personal space as its owner or a co-editor sees it."""

    space: PresetSpace
    owner_name: str
    is_owner: bool
    preset_count: int
    editors: List[User]


class PersonalPresetService:
    """Personal preset spaces: presets, slug and co-editors."""

    def __init__(self) -> None:
        self.spaces = PresetSpaceRepository()
        self.audit_service = AuditService()

    async def my_spaces(self, actor: User) -> List[SpaceView]:
        """The spaces the actor owns or co-edits, their own first. Empty until their first save."""
        return [await self._view(space, actor) for space in await self.spaces.editable_by(actor.id)]

    async def get_space(self, actor: User, space_id: int) -> SpaceView:
        return await self._view(await self._editable_space(actor, space_id), actor)

    async def rename_space(self, actor: User, space_id: int, slug: str) -> SpaceView:
        space = await self._owned_space(actor, space_id)
        slug = (slug or '').strip().lower()
        self._check_name(slug, 'Space name')
        if slug != space.slug:
            if slug in RESERVED_SLUGS or await self.spaces.slug_taken(slug, except_id=space.id):
                raise ValueError(f"{slug} is taken. Try another name.")
            old = space.slug
            self._throttle(actor)
            await self.spaces.rename(space, slug)
            await self.audit_service.write_log(
                actor, AuditActions.PRESET_SPACE_RENAMED, {'space_id': space.id, 'from': old, 'to': slug},
            )
        return await self._view(space, actor)

    async def add_editor(self, actor: User, space_id: int, user_id: int) -> SpaceView:
        space = await self._owned_space(actor, space_id)
        if user_id == actor.id:
            raise ValueError("You already edit your own presets")
        if not await TenantService.is_member(user_id, require_tenant_id()):
            raise NotFoundError("Member not found")
        user = await UserService().get_user_by_id(user_id)
        if user is None:
            raise NotFoundError("Member not found")
        if not await self.spaces.is_editor(space.id, user_id):
            if await self.spaces.count_editors(space.id) >= MAX_EDITORS_PER_SPACE:
                raise ValueError(f"A space can have {MAX_EDITORS_PER_SPACE} co-editors at most")
            self._throttle(actor)
            await self.spaces.add_editor(space.id, user_id)
            await self.audit_service.write_log(
                actor, AuditActions.PRESET_SPACE_EDITOR_ADDED,
                {'space_id': space.id, 'space': space.slug, 'user_id': user_id},
            )
        return await self._view(space, actor)

    async def remove_editor(self, actor: User, space_id: int, user_id: int) -> SpaceView:
        """The owner removes a co-editor, or a co-editor leaves."""
        space = await self._editable_space(actor, space_id)
        if space.owner_id != actor.id and user_id != actor.id:
            raise PermissionError("Only the owner can change who edits these presets")
        if await self.spaces.remove_editor(space.id, user_id):
            await self.audit_service.write_log(
                actor, AuditActions.PRESET_SPACE_EDITOR_REMOVED,
                {'space_id': space.id, 'space': space.slug, 'user_id': user_id},
            )
        return await self._view(space, actor)

    async def create_preset(
        self,
        actor: User,
        *,
        name: str,
        randomizer: str,
        settings: Dict[str, Any],
        description: Optional[str] = None,
        space_id: Optional[int] = None,
    ) -> Preset:
        """Save a new preset in a personal space; the actor's own, made now if needed, when none is named."""
        space = await self._target_space(actor, space_id)
        self._throttle(actor)
        preset = await self._add(space, (name or '').strip(), (randomizer or '').strip(), settings, description)
        await self.audit_service.write_log(
            actor, AuditActions.PRESET_CREATED,
            {'preset_id': preset.id, 'name': preset.name, 'randomizer': preset.randomizer, 'space': space.slug},
        )
        return preset

    async def sahasrahbot_presets(self, actor: User, namespace: str) -> List[SahasrahBotPreset]:
        """A SahasrahBot namespace's presets for the games Wizzrobe imports, marking ones you already have."""
        namespace = self._check_namespace(namespace)
        own = await self.spaces.owned_by(actor.id)
        found: List[SahasrahBotPreset] = []
        for randomizer in sahasrahbot.RANDOMIZERS:
            try:
                names = await sahasrahbot.namespace_presets(randomizer, namespace)
            except sahasrahbot.SahasrahBotAPIError as e:
                raise ValueError(str(e)) from e
            for name in sorted(names, key=str.lower):
                exists = own is not None and any([
                    await self.spaces.find_preset(own.id, r, slugify(name)) is not None
                    for r in self._landing_randomizers(randomizer)
                ])
                found.append(SahasrahBotPreset(randomizer, name, exists))
        return found

    @staticmethod
    def _landing_randomizers(randomizer: str) -> tuple:
        """Where an import of this game can land: SahasrahBot kept AVIANART presets under ALTTPR."""
        return (randomizer, 'avianart') if randomizer == 'alttpr' else (randomizer,)

    async def import_from_sahasrahbot(
        self, actor: User, namespace: str, picks: List[Tuple[str, str]], *, space_id: Optional[int] = None,
    ) -> List[ImportResult]:
        """Copy chosen presets from a SahasrahBot namespace into a personal space, one result each.

        A preset Wizzrobe can't roll, or whose name is taken, is reported and
        skipped; the rest still import.
        """
        namespace = self._check_namespace(namespace)
        picks = list(dict.fromkeys((r, n) for r, n in picks))
        if not picks:
            raise ValueError("Pick at least one preset to import")
        if len(picks) > MAX_IMPORT_PER_CALL:
            raise ValueError(f"Import up to {MAX_IMPORT_PER_CALL} presets at a time")
        if any(r not in sahasrahbot.RANDOMIZERS for r, _ in picks):
            raise ValueError("Wizzrobe imports ALTTPR, Super Metroid and SMZ3 presets from SahasrahBot")
        space = await self._target_space(actor, space_id)
        self._throttle(actor)
        results: List[ImportResult] = []
        for randomizer, original in picks:
            name = slugify(original)
            try:
                data = await sahasrahbot.fetch_preset(randomizer, namespace, original)
                mapped = sahasrahbot.to_wizzrobe(randomizer, data)
                preset = await self._add(space, name, mapped.randomizer, mapped.settings, mapped.description)
            except sahasrahbot.SahasrahBotAPIError as e:
                results.append(ImportResult(randomizer, original, None, str(e)))
            except ValueError as e:
                results.append(ImportResult(randomizer, original, None, str(e)))
            else:
                results.append(ImportResult(randomizer, original, preset.id, None))
        imported = [r for r in results if r.preset_id is not None]
        if imported:
            await self.audit_service.write_log(
                actor, AuditActions.PRESET_IMPORTED,
                {'source': 'sahasrahbot', 'namespace': namespace, 'space': space.slug, 'count': len(imported),
                 'presets': [f'{r.randomizer}/{r.name}' for r in imported]},
            )
        return results

    async def _add(
        self, space: PresetSpace, name: str, randomizer: str, settings: Any, description: Optional[str],
    ) -> Preset:
        self._check_name(name, 'Preset name')
        settings = self._check_settings(randomizer, settings)
        if await self.spaces.count_presets(space.id) >= MAX_PRESETS_PER_SPACE:
            raise ValueError(f"A space holds {MAX_PRESETS_PER_SPACE} presets at most. Delete one to make room.")
        if await self.spaces.find_preset(space.id, randomizer, name) is not None:
            raise ValueError(f"{space.slug} already has a {randomizer} preset named {name}")
        return await self.spaces.create_preset(
            space.id, name=name, randomizer=randomizer, settings=settings,
            description=self._check_description(description),
        )

    async def update_preset(
        self,
        actor: User,
        preset_id: int,
        *,
        name: Optional[str] = None,
        settings: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None,
    ) -> Preset:
        preset, space = await self._editable_preset(actor, preset_id)
        changes: Dict[str, Any] = {}
        if name is not None and name.strip() != preset.name:
            new_name = name.strip()
            self._check_name(new_name, 'Preset name')
            existing = await self.spaces.find_preset(space.id, preset.randomizer, new_name)
            if existing is not None and existing.id != preset.id:
                raise ValueError(f"{space.slug} already has a {preset.randomizer} preset named {new_name}")
            changes['name'] = new_name
        if settings is not None:
            changes['settings'] = self._check_settings(preset.randomizer, settings)
        if description is not None:
            changes['description'] = self._check_description(description)
        if changes:
            self._throttle(actor)
            await self.spaces.update_preset(preset, **changes)
            await self.audit_service.write_log(
                actor, AuditActions.PRESET_UPDATED,
                {'preset_id': preset.id, 'name': preset.name, 'randomizer': preset.randomizer,
                 'space': space.slug, 'fields': sorted(changes)},
            )
        return preset

    async def delete_preset(self, actor: User, preset_id: int) -> None:
        preset, space = await self._editable_preset(actor, preset_id)
        await self.audit_service.write_log(
            actor, AuditActions.PRESET_DELETED,
            {'preset_id': preset.id, 'name': preset.name, 'randomizer': preset.randomizer, 'space': space.slug},
        )
        await self.spaces.delete_preset(preset)

    async def copy_to_space(
        self, actor: User, preset_id: int, *, name: Optional[str] = None, space_id: Optional[int] = None,
    ) -> Preset:
        """Copy any library preset into a personal space, remembering where it came from."""
        original = await PresetLibraryService().visible_preset(actor, preset_id)
        space = await self._target_space(actor, space_id)
        if original.space_id == space.id:
            raise ValueError("That preset is already in this space")
        new_name = (name if name is not None else slugify(original.name)).strip()
        self._check_name(new_name, 'Preset name')
        if await self.spaces.count_presets(space.id) >= MAX_PRESETS_PER_SPACE:
            raise ValueError(f"A space holds {MAX_PRESETS_PER_SPACE} presets at most. Delete one to make room.")
        if await self.spaces.find_preset(space.id, original.randomizer, new_name) is not None:
            raise ValueError(f"{space.slug} already has a {original.randomizer} preset named {new_name}")
        self._throttle(actor)
        copy = await self.spaces.create_preset(
            space.id, name=new_name, randomizer=original.randomizer, settings=original.settings,
            description=original.description, copied_from_id=original.id,
            copied_at=datetime.now(timezone.utc),
        )
        original_space = original.space
        await self.audit_service.write_log(
            actor, AuditActions.PRESET_COPIED,
            {'preset_id': copy.id, 'name': copy.name, 'randomizer': copy.randomizer, 'space': space.slug,
             'from': f"{original_space.slug if original_space else '?'}/{original.name}", 'from_id': original.id},
        )
        return copy

    async def find_members(self, actor: User, query: str) -> List[User]:
        """Members of this community to offer as co-editors, by name; two letters at least."""
        query = (query or '').strip()
        if len(query) < 2:
            return []
        return [u for u in await UserRepository.search_members(query, require_tenant_id()) if u.id != actor.id]

    async def can_edit(self, actor: User, space: PresetSpace) -> bool:
        """True when the actor owns or co-edits a personal space (cross-tenant)."""
        if space.kind != PresetSpaceKind.USER:
            return False
        return space.owner_id == actor.id or await self.spaces.is_editor(space.id, actor.id)

    async def _target_space(self, actor: User, space_id: Optional[int]) -> PresetSpace:
        if space_id is not None:
            return await self._editable_space(actor, space_id)
        space = await self.spaces.owned_by(actor.id)
        if space is not None:
            return space
        return await self._create_space(actor)

    async def _create_space(self, actor: User) -> PresetSpace:
        base = slugify(actor.username) or slugify(actor.preferred_name)
        if len(base) < 2:
            base = f"player{actor.id}"
        slug = base
        n = 2
        while slug in RESERVED_SLUGS or await self.spaces.slug_taken(slug):
            suffix = f"-{n}"
            slug = f"{base[:32 - len(suffix)]}{suffix}"
            n += 1
        space = await self.spaces.create_personal(slug, actor.id)
        await self.audit_service.write_log(
            actor, AuditActions.PRESET_SPACE_CREATED, {'space_id': space.id, 'space': slug},
        )
        return space

    async def _editable_space(self, actor: User, space_id: int) -> PresetSpace:
        space = await self.spaces.get(space_id)
        if space is None or not await self.can_edit(actor, space):
            raise NotFoundError("Preset space not found")
        return space

    async def _owned_space(self, actor: User, space_id: int) -> PresetSpace:
        space = await self._editable_space(actor, space_id)
        if space.owner_id != actor.id:
            raise PermissionError("Only the owner can change this")
        return space

    async def _editable_preset(self, actor: User, preset_id: int) -> Tuple[Preset, PresetSpace]:
        preset = await PresetLibraryRepository.get(preset_id)
        space = preset.space if preset is not None else None
        if preset is None or space is None or not await self.can_edit(actor, space):
            raise NotFoundError("Preset not found")
        return preset, space

    async def _view(self, space: PresetSpace, actor: User) -> SpaceView:
        owner = space.owner
        return SpaceView(
            space=space,
            owner_name=owner.preferred_name if owner is not None else '',
            is_owner=space.owner_id == actor.id,
            preset_count=await self.spaces.count_presets(space.id),
            editors=[e.user for e in await self.spaces.editors(space.id)],
        )

    @staticmethod
    def _throttle(actor: User) -> None:
        if not _take_write_slot(actor.id):
            raise ValueError("You've made a lot of changes quickly. Try again in a few minutes.")

    @staticmethod
    def _check_namespace(namespace: str) -> str:
        namespace = (namespace or '').strip().strip('/')
        if not _NAMESPACE_RE.match(namespace):
            raise ValueError("Type the SahasrahBot namespace, the part before the slash in its preset names")
        return namespace

    @staticmethod
    def _check_name(name: str, what: str) -> None:
        if not _NAME_RE.match(name or ''):
            raise ValueError(f"{what} must be {_NAME_RULE}")

    @staticmethod
    def _check_description(description: Optional[str]) -> Optional[str]:
        text = (description or '').strip()
        if len(text) > MAX_DESCRIPTION_CHARS:
            raise ValueError(f"The note must be {MAX_DESCRIPTION_CHARS} characters or fewer")
        return text or None

    @staticmethod
    def _check_settings(randomizer: str, settings: Any) -> Dict[str, Any]:
        PresetService._validate('x', randomizer, settings)
        if len(json.dumps(settings)) > MAX_SETTINGS_BYTES:
            raise ValueError("Those settings are too big (64 KB at most)")
        return SeedGenerationService.normalize_preset_settings(randomizer, settings)
