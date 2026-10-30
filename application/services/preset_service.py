"""
Preset Service - Business Logic Layer

Tenant-authored seed-rolling presets: a named ``randomizer`` + ``settings`` blob
that seed generation resolves instead of opening a hard-coded ``presets/*`` file.
All mutations are gated by :meth:`AuthService.can_manage_presets` and audited.

A community fills that list three ways: authoring a preset by hand,
``import_builtins`` (the committed ``presets/`` files — the same settings the
legacy hard-coded paths used, so a fresh tenant has usable starting rows), or
``import_remote_presets``, which pulls the randomizer's *own* published presets
over its API. The third is the one that keeps up: DK64R's season settings change
upstream, and nobody has to transcribe a settings blob to race them.
"""

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import yaml

from application.errors import require_found
from application.repositories import PresetRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.seedgen_service import RemotePreset, SeedGenerationService
from models import Preset, User

_BUILTINS_DIR = 'presets'


@dataclass(frozen=True)
class PresetTestRoll:
    """A diagnostic roll of one preset: the seed, and whether the preset drove it."""

    randomizer: str
    seed_url: str
    used_preset_settings: bool


class PresetService:
    """CRUD + built-in import for tenant-scoped seed-rolling presets."""

    def __init__(self) -> None:
        self.repository = PresetRepository()
        self.audit_service = AuditService()

    async def list_presets(self, actor: Optional[User]) -> List[Preset]:
        await AuthService.ensure(
            await AuthService.can_manage_presets(actor), "Cannot manage presets"
        )
        return await self.repository.list_all()

    async def usage(self, actor: Optional[User]) -> Dict[int, List[str]]:
        """Names of the tournaments rolling from each preset, keyed by preset id.

        Deleting a preset detaches those tournaments (SET_NULL), so the page
        names them before anyone confirms a delete.
        """
        await AuthService.ensure(
            await AuthService.can_manage_presets(actor), "Cannot manage presets"
        )
        usage: Dict[int, List[str]] = {}
        for row in await self.repository.tournaments_using_presets():
            for preset_id in {row['preset_id'], row['hard_preset_id']} - {None}:
                usage.setdefault(preset_id, []).append(row['name'])
        return usage

    async def list_by_randomizer(self, randomizer: str) -> List[Preset]:
        """Presets for one randomizer — used to populate the tournament select
        (read-only; no management gate)."""
        return await self.repository.list_by_randomizer(randomizer)

    async def list_selectable(self) -> List[Preset]:
        """All of the tenant's presets, for populating a tournament's preset
        select (read-only; no management gate — anyone editing a tournament may
        pick a preset)."""
        return await self.repository.list_all()

    async def get_preset(self, actor: Optional[User], preset_id: int) -> Preset:
        await AuthService.ensure(
            await AuthService.can_manage_presets(actor), "Cannot manage presets"
        )
        return await self._require(preset_id)

    async def create_preset(
        self,
        actor: Optional[User],
        *,
        name: str,
        randomizer: str,
        settings: Dict[str, Any],
        description: Optional[str] = None,
    ) -> Preset:
        await AuthService.ensure(
            await AuthService.can_manage_presets(actor), "Cannot manage presets"
        )
        name = (name or '').strip()
        randomizer = (randomizer or '').strip()
        self._validate(name, randomizer, settings)
        settings = SeedGenerationService.normalize_preset_settings(randomizer, settings)
        if await self.repository.get_by_natural_key(randomizer, name) is not None:
            raise ValueError(f"A '{randomizer}' preset named '{name}' already exists")
        preset = await self.repository.create(
            name=name,
            randomizer=randomizer,
            settings=settings,
            description=(description or '').strip() or None,
        )
        await self.audit_service.write_log(
            actor,
            AuditActions.PRESET_CREATED,
            {'preset_id': preset.id, 'name': name, 'randomizer': randomizer},
        )
        return preset

    async def update_preset(
        self,
        actor: Optional[User],
        preset_id: int,
        *,
        name: Optional[str] = None,
        randomizer: Optional[str] = None,
        settings: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None,
    ) -> Preset:
        await AuthService.ensure(
            await AuthService.can_manage_presets(actor), "Cannot manage presets"
        )
        preset = await self._require(preset_id)
        new_name = preset.name if name is None else (name or '').strip()
        new_randomizer = preset.randomizer if randomizer is None else (randomizer or '').strip()
        new_settings = preset.settings if settings is None else settings
        self._validate(new_name, new_randomizer, new_settings)
        # A pure rename skips re-validation, so rows saved before settings were checked stay renameable.
        if settings is not None or new_randomizer != preset.randomizer:
            new_settings = SeedGenerationService.normalize_preset_settings(new_randomizer, new_settings)
        if (new_randomizer, new_name) != (preset.randomizer, preset.name):
            existing = await self.repository.get_by_natural_key(new_randomizer, new_name)
            if existing is not None and existing.id != preset.id:
                raise ValueError(f"A '{new_randomizer}' preset named '{new_name}' already exists")
        changes: Dict[str, Any] = {
            'name': new_name,
            'randomizer': new_randomizer,
            'settings': new_settings,
        }
        if description is not None:
            changes['description'] = (description or '').strip() or None
        preset = await self.repository.update(preset, **changes)
        await self.audit_service.write_log(
            actor,
            AuditActions.PRESET_UPDATED,
            {'preset_id': preset.id, 'name': new_name, 'randomizer': new_randomizer},
        )
        return preset

    async def delete_preset(self, actor: Optional[User], preset_id: int) -> None:
        await AuthService.ensure(
            await AuthService.can_manage_presets(actor), "Cannot manage presets"
        )
        preset = await self._require(preset_id)
        await self.audit_service.write_log(
            actor,
            AuditActions.PRESET_DELETED,
            {'preset_id': preset.id, 'name': preset.name, 'randomizer': preset.randomizer},
        )
        await self.repository.delete(preset)

    async def test_roll(self, actor: Optional[User], preset_id: int) -> PresetTestRoll:
        """Roll one seed from a preset so staff can check the randomizer works.

        Goes through the same provider envelope a match roll does, but records no
        ``GeneratedSeeds`` row, so test seeds never mix with match seeds. Task-queue
        backends (DK64R) block until their seed is ready, which can take minutes.
        Both outcomes are audited; a failure re-raises for the caller to show.
        """
        if actor is None:
            raise PermissionError("Cannot manage presets")
        await AuthService.ensure(
            await AuthService.can_manage_presets(actor), "Cannot manage presets"
        )
        preset = await self._require(preset_id)
        if preset.randomizer not in SeedGenerationService.AVAILABLE_RANDOMIZERS:
            raise ValueError(f"Unknown randomizer: {preset.randomizer}")
        details: Dict[str, Any] = {
            'preset_id': preset.id, 'name': preset.name, 'randomizer': preset.randomizer,
        }
        try:
            call = await SeedGenerationService().generate_seed_call(
                preset.randomizer, preset, surface='preset_test',
            )
        except ValueError as e:
            await self.audit_service.write_log(
                actor, AuditActions.PRESET_TEST_ROLLED, {**details, 'ok': False, 'error': str(e)},
            )
            raise
        await self.audit_service.write_log(
            actor, AuditActions.PRESET_TEST_ROLLED,
            {**details, 'ok': True, 'seed_url': call.value.url},
        )
        return PresetTestRoll(
            randomizer=preset.randomizer,
            seed_url=call.value.url,
            used_preset_settings=preset.randomizer in SeedGenerationService.PRESET_AWARE_RANDOMIZERS,
        )

    async def import_builtins(self, actor: Optional[User]) -> List[Preset]:
        """Import the committed ``presets/`` files as starting rows.

        Idempotent: presets already present (by ``randomizer``/``name``) are
        skipped, so re-running only fills gaps. Returns the presets created.
        """
        await AuthService.ensure(
            await AuthService.can_manage_presets(actor), "Cannot manage presets"
        )
        discovered = await asyncio.to_thread(self._load_builtins)
        created: List[Preset] = []
        for entry in discovered:
            if await self.repository.get_by_natural_key(entry['randomizer'], entry['name']) is not None:
                continue
            preset = await self.repository.create(
                name=entry['name'],
                randomizer=entry['randomizer'],
                settings=SeedGenerationService.normalize_preset_settings(
                    entry['randomizer'], entry['settings'],
                ),
                description=entry['description'],
            )
            created.append(preset)
        if created:
            await self.audit_service.write_log(
                actor,
                AuditActions.PRESET_IMPORTED,
                {'count': len(created),
                 'presets': [{'randomizer': p.randomizer, 'name': p.name} for p in created]},
            )
        return created

    async def list_remote_presets(
        self, actor: User, randomizer: str, *, branch: Optional[str] = None,
    ) -> List[RemotePreset]:
        """The presets the randomizer's own API publishes, for a picker to render.

        Nothing is written — this is the catalogue a community browses before
        choosing which upstream presets to keep. Reaching the upstream needs this
        tenant's credential, so a missing key raises ``MissingCredentialError``
        naming it.
        """
        await AuthService.ensure(
            await AuthService.can_manage_presets(actor), "Cannot manage presets"
        )
        return await SeedGenerationService().list_remote_presets(randomizer, branch=branch)

    async def import_remote_presets(
        self,
        actor: User,
        randomizer: str,
        names: List[str],
        *,
        branch: Optional[str] = None,
    ) -> List[Preset]:
        """Save chosen upstream presets as this tenant's own ``Preset`` rows.

        The settings blob is re-fetched from the randomizer rather than taken
        from the caller: the picker's job is to name presets, not to hand this
        service a payload that seed rolls will trust.

        Idempotent like :meth:`import_builtins` — a name already present for this
        randomizer is skipped rather than overwritten, so re-importing after the
        upstream adds a preset only fills the gap and never silently rewrites
        settings a tournament is already pointing at.
        """
        await AuthService.ensure(
            await AuthService.can_manage_presets(actor), "Cannot manage presets"
        )
        wanted = {(n or '').strip() for n in names} - {''}
        if not wanted:
            raise ValueError("Select at least one preset to import")

        catalogue = await SeedGenerationService().list_remote_presets(
            randomizer, branch=branch,
        )
        by_name = {entry.name: entry for entry in catalogue}
        missing = sorted(wanted - set(by_name))
        if missing:
            raise ValueError(
                f"{randomizer} no longer offers: {', '.join(missing)}"
            )

        created: List[Preset] = []
        for name in sorted(wanted):
            entry = by_name[name]
            self._validate(name, randomizer, entry.settings)
            if await self.repository.get_by_natural_key(randomizer, name) is not None:
                continue
            created.append(await self.repository.create(
                name=name,
                randomizer=randomizer,
                settings=SeedGenerationService.normalize_preset_settings(randomizer, entry.settings),
                description=entry.description,
            ))

        if created:
            await self.audit_service.write_log(
                actor,
                AuditActions.PRESET_IMPORTED,
                {'source': randomizer, 'branch': branch, 'count': len(created),
                 'presets': [p.name for p in created]},
            )
        return created

    async def _require(self, preset_id: int) -> Preset:
        return require_found(await self.repository.get_by_id(preset_id), "Preset")

    @staticmethod
    def _validate(name: str, randomizer: str, settings: Any) -> None:
        if not name:
            raise ValueError("Preset name is required")
        if len(name) > 255:
            raise ValueError("Preset name must be 255 characters or fewer")
        if not randomizer:
            raise ValueError("Preset randomizer is required")
        if randomizer not in SeedGenerationService.AVAILABLE_RANDOMIZERS:
            raise ValueError(f"Unknown randomizer: {randomizer}")
        if not isinstance(settings, dict):
            raise ValueError("Preset settings must be a JSON object")

    @staticmethod
    def _load_builtins() -> List[Dict[str, Any]]:
        """Parse every ``presets/<randomizer>/<name>.(yaml|yml|json)`` file.

        For ALTTPR-style files the settings live under a top-level ``settings``
        key (with a sibling ``description``); other backends store the payload at
        the top level. A ``<name>.txt`` beside the file supplies the description
        when the file has none, so a settings file copied verbatim from upstream
        (and, for the JSON backends, sent upstream verbatim) never needs editing
        to carry one. Blocking file IO — call via ``asyncio.to_thread``.
        """
        entries: List[Dict[str, Any]] = []
        if not os.path.isdir(_BUILTINS_DIR):
            return entries
        for randomizer in sorted(os.listdir(_BUILTINS_DIR)):
            sub = os.path.join(_BUILTINS_DIR, randomizer)
            if not os.path.isdir(sub):
                continue
            if randomizer not in SeedGenerationService.AVAILABLE_RANDOMIZERS:
                continue
            for filename in sorted(os.listdir(sub)):
                stem, ext = os.path.splitext(filename)
                if ext.lower() not in ('.yaml', '.yml', '.json'):
                    continue
                with open(os.path.join(sub, filename), 'r', encoding='utf-8') as f:
                    parsed = json.load(f) if ext.lower() == '.json' else yaml.safe_load(f)
                if not isinstance(parsed, dict):
                    continue
                if isinstance(parsed.get('settings'), dict):
                    settings = parsed['settings']
                    description = parsed.get('description')
                else:
                    settings = parsed
                    description = None
                sidecar = os.path.join(sub, f'{stem}.txt')
                if not description and os.path.isfile(sidecar):
                    with open(sidecar, 'r', encoding='utf-8') as f:
                        description = f.read().strip() or None
                entries.append({
                    'randomizer': randomizer,
                    'name': stem,
                    'settings': settings,
                    'description': description,
                })
        return entries
