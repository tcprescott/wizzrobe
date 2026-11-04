"""
Preset Library Service - Business Logic Layer

The public preset library: every preset has a ``space/preset`` name, anyone
signed in can browse, read and roll a listed one, and a community's staff copy
one into their own list before a tournament can use it. A tournament only ever
points at its own community's presets, so copying is what keeps someone else's
later edit from changing what a match rolls; the copy remembers its original
and offers an update instead.

A community preset is listed only when its staff say so. Staff of the owning
community also see its unlisted presets, flagged, so they can list them.

Rolls use the roller's current community's randomizer keys, never the owner's,
and are limited per person; preset managers roll without a limit.
"""

import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional, Set

import yaml

from application.errors import NotFoundError
from application.repositories import PresetLibraryRepository, PresetRepository, PresetSpaceRepository
from application.services.admin_presets_service import PASTE_FIELDS
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.builtin_preset_service import BuiltinPresetService
from application.services.randomizer_credential_service import RandomizerCredentialService
from application.services.seedgen_service import SeedGenerationService
from application.tenant_context import require_tenant_id
from models import Preset, PresetSpace, PresetSpaceKind, User

ROLLS_PER_WINDOW = 10
ROLL_WINDOW_SECONDS = 600

_rolls: Dict[int, Deque[float]] = defaultdict(deque)


def _take_roll_slot(user_id: int) -> bool:
    now = time.monotonic()
    hits = _rolls[user_id]
    while hits and hits[0] < now - ROLL_WINDOW_SECONDS:
        hits.popleft()
    if len(hits) >= ROLLS_PER_WINDOW:
        return False
    hits.append(now)
    return True


def reset_roll_limits() -> None:
    _rolls.clear()


def _space(preset: Preset) -> PresetSpace:
    space = preset.space
    if space is None:
        raise NotFoundError("Preset not found")
    return space


def _reference(preset: Preset) -> str:
    return f"{_space(preset).slug}/{preset.name}"


@dataclass(frozen=True)
class LibraryPreset:
    """One preset as the library shows it."""

    preset: Preset
    space_slug: str
    space_kind: PresetSpaceKind
    space_name: str
    mine: bool
    changed_since_copy: bool
    rollable: bool
    can_manage: bool
    can_edit: bool

    @property
    def randomizer_name(self) -> str:
        return SeedGenerationService.RANDOMIZER_NAMES.get(self.preset.randomizer, self.preset.randomizer)

    @property
    def reference(self) -> str:
        return f"{self.space_slug}/{self.preset.name}"


@dataclass(frozen=True)
class LibraryRandomizer:
    slug: str
    name: str
    available: bool
    paste_key: Optional[str]
    paste_label: Optional[str]
    paste_hint: Optional[str]


MAX_WEIGHTSET_BYTES = 64 * 1024


class _NoAliasLoader(yaml.SafeLoader):
    """SafeLoader without anchors and aliases, which let a small paste expand into a huge tree."""

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(yaml.AliasEvent):
            raise yaml.YAMLError('aliases are not allowed')
        return super().compose_node(parent, index)


@dataclass(frozen=True)
class MysteryCheck:
    settings: Dict[str, Any]
    odds: list


@dataclass(frozen=True)
class LibraryRoll:
    preset_id: int
    randomizer: str
    seed_url: str
    code: Optional[List[str]]
    roll_count: int
    instructions: Optional[str] = None


class PresetLibraryService:
    """Browse, roll and copy presets across every space."""

    def __init__(self) -> None:
        self.repository = PresetLibraryRepository()
        self.audit_service = AuditService()

    async def list_presets(self, actor: User, *, randomizer: Optional[str] = None) -> List[LibraryPreset]:
        """Every preset the actor may see: all listed ones, plus their own community's if they manage it."""
        tenant_id = require_tenant_id()
        await BuiltinPresetService().ensure_synced()
        manager = await AuthService.can_manage_presets(actor)
        rows = await self.repository.list_library(
            include_unlisted_of_tenant=tenant_id if manager else None, randomizer=randomizer,
        )
        rollable = await self._rollable()
        editable = await self._editable_spaces(actor)
        return [self._view(p, tenant_id, rollable, manager, editable) for p in rows]

    async def get_preset(self, actor: User, preset_id: int) -> LibraryPreset:
        tenant_id = require_tenant_id()
        preset = await self._visible(actor, preset_id)
        manager = await AuthService.can_manage_presets(actor)
        return self._view(preset, tenant_id, await self._rollable(), manager, await self._editable_spaces(actor))

    async def randomizers(self, actor: User) -> List[LibraryRandomizer]:
        """Every randomizer a preset can be written for, and how its settings are entered."""
        rollable = await self._rollable()
        out = []
        for slug in SeedGenerationService.AVAILABLE_RANDOMIZERS:
            field = PASTE_FIELDS.get(slug)
            out.append(LibraryRandomizer(
                slug=slug,
                name=SeedGenerationService.randomizer_name(slug),
                available=slug in rollable,
                paste_key=field[0] if field else None,
                paste_label=field[1] if field else None,
                paste_hint=field[3] if field else None,
            ))
        return sorted(out, key=lambda r: r.name.lower())

    @staticmethod
    def check_mystery(text: str) -> MysteryCheck:
        """A weightset pasted as YAML, checked by rolling it a few times, with its odds; ``ValueError`` if it won't roll."""
        if len(text.encode()) > MAX_WEIGHTSET_BYTES:
            raise ValueError('That weightset is too big. Keep it under 64 KB.')
        try:
            weights = yaml.load(text, Loader=_NoAliasLoader)
        except yaml.YAMLError as e:
            mark = getattr(e, 'problem_mark', None)
            where = f' near line {mark.line + 1}' if mark is not None else ''
            raise ValueError(f"That isn't valid YAML{where}.") from None
        if isinstance(weights, dict):
            weights.pop('description', None)
        weights = SeedGenerationService.normalize_preset_settings('alttprmystery', weights)
        return MysteryCheck(settings=weights, odds=SeedGenerationService.mystery_odds(weights))

    @staticmethod
    def describe_mystery(preset: Preset) -> Optional[MysteryCheck]:
        """A mystery preset's odds per setting, or ``None`` for any other preset."""
        if preset.randomizer != 'alttprmystery':
            return None
        return MysteryCheck(settings=preset.settings, odds=SeedGenerationService.mystery_odds(preset.settings))

    async def visible_preset(self, actor: User, preset_id: int) -> Preset:
        """A preset the actor may see in the library, or not-found."""
        return await self._visible(actor, preset_id)

    async def roll(self, actor: User, preset_id: int) -> LibraryRoll:
        """Roll one seed from a library preset with this community's randomizer keys."""
        preset = await self._visible(actor, preset_id)
        if preset.randomizer not in SeedGenerationService.AVAILABLE_RANDOMIZERS:
            raise ValueError(f"Wizzrobe can't roll {preset.randomizer} seeds")
        if preset.randomizer not in await self._rollable():
            name = SeedGenerationService.RANDOMIZER_NAMES.get(preset.randomizer, preset.randomizer)
            raise ValueError(f"This community hasn't added the key {name} needs, so it can't roll these seeds")
        if not await AuthService.can_manage_presets(actor) and not _take_roll_slot(actor.id):
            raise ValueError(
                f"You can roll {ROLLS_PER_WINDOW} seeds every 10 minutes. Try again in a few minutes."
            )
        call = await SeedGenerationService().generate_seed_call(
            preset.randomizer, preset, surface='preset_library',
        )
        await self.repository.count_roll(preset.id)
        await self.audit_service.write_log(
            actor, AuditActions.PRESET_ROLLED,
            {'preset_id': preset.id, 'reference': _reference(preset),
             'randomizer': preset.randomizer, 'seed_url': call.value.url},
        )
        return LibraryRoll(
            preset_id=preset.id,
            randomizer=preset.randomizer,
            seed_url=call.value.url,
            code=list(call.value.code) if call.value.code else None,
            roll_count=preset.roll_count + 1,
            instructions=SeedGenerationService.seed_instructions(preset.randomizer, call.value.settings),
        )

    async def copy_to_community(self, actor: User, preset_id: int, *, name: Optional[str] = None) -> Preset:
        """Copy a library preset into this community's list, remembering where it came from."""
        await AuthService.ensure(
            await AuthService.can_manage_presets(actor), "Cannot manage presets"
        )
        original = await self._visible(actor, preset_id)
        if original.tenant_id == require_tenant_id():
            raise ValueError("That preset is already in this community")
        new_name = (name if name is not None else original.name).strip()
        if not new_name:
            raise ValueError("Preset name is required")
        if len(new_name) > 255:
            raise ValueError("Preset name is too long")
        if await self.repository.find_in_community(new_name, original.randomizer) is not None:
            raise ValueError(f"This community already has a {original.randomizer} preset named {new_name}")
        copy = await self.repository.create_copy(original, new_name, datetime.now(timezone.utc))
        await self.audit_service.write_log(
            actor, AuditActions.PRESET_COPIED,
            {'preset_id': copy.id, 'name': copy.name, 'randomizer': copy.randomizer,
             'from': _reference(original), 'from_id': original.id},
        )
        return copy

    async def update_copy(self, actor: User, preset_id: int) -> Preset:
        """Bring a copied preset up to date with its original; the only way a copy changes upstream.

        A community copy needs that community's preset managers; a copy in a
        personal space needs its owner or a co-editor.
        """
        copy = await self.repository.get(preset_id)
        if copy is None or copy.space is None:
            raise NotFoundError("Preset not found")
        if copy.space.kind == PresetSpaceKind.USER:
            if copy.space.id not in await self._editable_spaces(actor):
                raise NotFoundError("Preset not found")
        else:
            await AuthService.ensure(
                await AuthService.can_manage_presets(actor), "Cannot manage presets"
            )
            if copy.tenant_id != require_tenant_id():
                raise NotFoundError("Preset not found")
        original = copy.copied_from
        if original is None:
            raise ValueError("This preset isn't a copy, or its original was deleted")
        if not original.listed:
            raise ValueError("The original is no longer in the library")
        if original.randomizer != copy.randomizer:
            raise ValueError("The original now rolls a different game, so it can't update this copy")
        await PresetRepository.update(
            copy, settings=original.settings, description=original.description,
            copied_at=datetime.now(timezone.utc),
        )
        await self.audit_service.write_log(
            actor, AuditActions.PRESET_COPY_UPDATED,
            {'preset_id': copy.id, 'name': copy.name,
             'from': _reference(original), 'from_id': original.id},
        )
        return copy

    @staticmethod
    async def _editable_spaces(actor: User) -> Set[int]:
        return {s.id for s in await PresetSpaceRepository.editable_by(actor.id)}

    @staticmethod
    async def _rollable() -> set:
        configured = await RandomizerCredentialService().configured_randomizers()
        return set(SeedGenerationService.available_randomizers(configured))

    async def _visible(self, actor: User, preset_id: int) -> Preset:
        preset = await self.repository.get(preset_id)
        tenant_id = require_tenant_id()
        if preset is None or not self._can_see(
            preset, tenant_id, preset.tenant_id == tenant_id and await AuthService.can_manage_presets(actor),
        ):
            raise NotFoundError("Preset not found")
        return preset

    @staticmethod
    def _can_see(preset: Preset, tenant_id: int, manages_owner: bool) -> bool:
        if preset.space_id is None:
            return False
        return bool(preset.listed) or (manages_owner and preset.tenant_id == tenant_id)

    @staticmethod
    def _view(
        preset: Preset, tenant_id: int, rollable: set, manager: bool, editable: Set[int],
    ) -> LibraryPreset:
        space = _space(preset)
        if space.tenant is not None:
            space_name = space.tenant.name
        elif space.owner is not None:
            space_name = space.owner.preferred_name
        else:
            space_name = 'Wizzrobe'
        original = preset.copied_from
        changed = bool(
            original is not None and preset.copied_at is not None
            and original.updated_at is not None and original.updated_at > preset.copied_at
            and original.settings != preset.settings
        )
        return LibraryPreset(
            preset=preset,
            space_slug=space.slug,
            space_kind=space.kind,
            space_name=space_name,
            mine=preset.tenant_id == tenant_id,
            changed_since_copy=changed,
            rollable=preset.randomizer in rollable,
            can_manage=manager,
            can_edit=space.id in editable,
        )
