"""Async qualifier — the pool and permalink management surface.

What a qualifier admin does before anyone runs: name the pools, decide which
preset each rolls from, and fill them with permalinks (pasted, or rolled from the
preset). Every method here is admin-gated through the qualifier that owns the
pool, so the gate is one hop away from the row being edited rather than passed in.

Mixed into :class:`AsyncQualifierService` — the same composition
``application/services/_bracket/`` uses — so the orchestration module keeps room
for the run lifecycle and review, which is where its rules actually live.
"""

from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple

from application.errors import NotFoundError, require_found
from application.feature_flags import requires_feature
from application.services.async_qualifier import async_qualifier_access as access
from application.services.async_qualifier import async_qualifier_rules as rules
from application.services.audit_service import AuditActions
from application.services.seedgen_service import SeedGenerationService
from application.tenant_context import require_tenant_id
from models import (
    AsyncQualifierPermalink,
    AsyncQualifierPool,
    FeatureFlag,
    GeneratedSeeds,
    Preset,
    User,
)

# A single roll takes on the order of a minute per seed against a live generator,
# so a batch is bounded to something an admin can wait out in one page load.
MAX_ROLL_COUNT = 25


@dataclass(frozen=True)
class BulkPermalinkAdd:
    """What a paste-many produced: the permalinks, and the lines that were not.

    The count alone was the whole answer before, which made a typo indistinguishable
    from a success — paste seven lines, hear "added five", and the two that were
    dropped are yours to find. ``rejected`` carries the 1-based input line so the
    admin can go straight to it.
    """

    created: List[AsyncQualifierPermalink]
    rejected: List[Tuple[int, str, str]]   # line number, the line, why it was refused

    @property
    def summary(self) -> str:
        """One sentence naming every refused line — the notification's body."""
        added = f'Added {len(self.created)} permalink(s)'
        if not self.rejected:
            return added
        lines = '; '.join(f'line {n}: {why}' for n, _, why in self.rejected)
        return f'{added}, skipped {len(self.rejected)} — {lines}'


def roll_refusal(preset: Optional[Preset]) -> Optional[str]:
    """Why this preset cannot fill a pool by rolling, or ``None`` if it can.

    Shared with the page so the Roll button can say so before it is pressed. The
    refusal used to arrive only after clicking, in a dialog whose only field was a
    count — nothing there can change the preset, so the only exit was Cancel.
    """
    if preset is None:
        return "Pool has no preset to roll from"
    if preset.randomizer in SeedGenerationService.ASYNC_RANDOMIZERS:
        # Task-queue backends are not wired into this batch yet. Each roll would
        # take minutes and complete independently, which breaks the "abort with
        # nothing half-written" property roll_permalinks relies on — a pool would
        # sit part-filled with no way to tell a slow roll from a lost one.
        return (f"'{preset.randomizer}' rolls seeds asynchronously and cannot "
                "fill a qualifier pool yet. Pick a preset for another randomizer.")
    return None


class PoolManagementMixin:
    """Pool + permalink CRUD. Requires the host's repositories and audit service."""

    # ------------------------------------------------------------------ pools

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def list_pools(self, actor: Optional[User], qualifier_id: int) -> List[AsyncQualifierPool]:
        qualifier = await self._require_qualifier(qualifier_id)
        await access.ensure_qualifier_admin(actor, qualifier)
        return await self.pool_repository.list_for_qualifier(qualifier_id)

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def create_pool(
        self,
        actor: Optional[User],
        qualifier_id: int,
        *,
        name: str,
        preset_id: Optional[int] = None,
    ) -> AsyncQualifierPool:
        qualifier = await self._require_qualifier(qualifier_id)
        await access.ensure_qualifier_admin(actor, qualifier)
        name = (name or '').strip()
        if not name:
            raise ValueError("Pool name is required")
        if preset_id is not None and await self.preset_repository.get_by_id(preset_id) is None:
            raise NotFoundError("Preset not found")
        existing = await self.pool_repository.list_for_qualifier(qualifier_id)
        if any(p.name.lower() == name.lower() for p in existing):
            raise ValueError(f"A pool named '{name}' already exists")
        pool = await self.pool_repository.create(
            qualifier_id=qualifier_id, name=name, preset_id=preset_id
        )
        await self.audit_service.write_log(
            actor, AuditActions.ASYNC_QUALIFIER_POOL_CREATED,
            {'qualifier_id': qualifier_id, 'pool_id': pool.id, 'name': name},
        )
        return pool

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def update_pool(
        self,
        actor: Optional[User],
        pool_id: int,
        *,
        name: Optional[str] = None,
        preset_id: Optional[int] = None,
        clear_preset: bool = False,
    ) -> AsyncQualifierPool:
        pool = await self._require_pool(pool_id)
        await self._ensure_pool_admin(actor, pool)
        changes: dict = {}
        if name is not None:
            name = name.strip()
            if not name:
                raise ValueError("Pool name is required")
            changes['name'] = name
        if clear_preset:
            changes['preset_id'] = None
        elif preset_id is not None:
            if await self.preset_repository.get_by_id(preset_id) is None:
                raise NotFoundError("Preset not found")
            changes['preset_id'] = preset_id
        pool = await self.pool_repository.update(pool, **changes)
        await self.audit_service.write_log(
            actor, AuditActions.ASYNC_QUALIFIER_POOL_UPDATED,
            {'pool_id': pool.id, 'fields': sorted(changes.keys())},
        )
        return pool

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def delete_pool(self, actor: Optional[User], pool_id: int) -> None:
        pool = await self._require_pool(pool_id)
        await self._ensure_pool_admin(actor, pool)
        await self.audit_service.write_log(
            actor, AuditActions.ASYNC_QUALIFIER_POOL_DELETED,
            {'pool_id': pool.id, 'qualifier_id': pool.qualifier_id},
        )
        await self.pool_repository.delete(pool)

    # ------------------------------------------------------------- permalinks

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def add_permalink(
        self,
        actor: Optional[User],
        pool_id: int,
        *,
        url: str,
        notes: Optional[str] = None,
        live_race: bool = False,
    ) -> AsyncQualifierPermalink:
        pool = await self._require_pool(pool_id)
        await self._ensure_pool_admin(actor, pool)
        url = rules.validate_permalink_url(url)
        permalink = await self.permalink_repository.create(
            pool_id=pool_id, url=url, notes=(notes or '').strip() or None, live_race=live_race
        )
        await self.audit_service.write_log(
            actor, AuditActions.ASYNC_QUALIFIER_PERMALINK_ADDED,
            {'pool_id': pool_id, 'permalink_id': permalink.id},
        )
        return permalink

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def add_permalinks_bulk(
        self, actor: Optional[User], pool_id: int, *, urls: Sequence[str]
    ) -> BulkPermalinkAdd:
        """Paste-many: one permalink per usable line, and a report on the rest.

        A bad line is skipped rather than aborting the paste, because the common
        case is twenty good lines and one that came across mangled — refusing all
        twenty makes the admin re-paste, and taking all twenty-one puts a seed that
        will not open in front of a runner.
        """
        pool = await self._require_pool(pool_id)
        await self._ensure_pool_admin(actor, pool)
        created: List[AsyncQualifierPermalink] = []
        rejected: List[Tuple[int, str, str]] = []
        for number, raw in enumerate(urls, start=1):
            url = (raw or '').strip()
            if not url:
                continue
            error = rules.permalink_url_error(url)
            if error is not None:
                rejected.append((number, url, error))
                continue
            created.append(await self.permalink_repository.create(pool_id=pool_id, url=url))
        if created:
            await self.audit_service.write_log(
                actor, AuditActions.ASYNC_QUALIFIER_PERMALINK_ADDED,
                {'pool_id': pool_id, 'count': len(created), 'skipped': len(rejected)},
            )
        return BulkPermalinkAdd(created=created, rejected=rejected)

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def roll_permalinks(
        self, actor: Optional[User], pool_id: int, *, count: int,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> List[AsyncQualifierPermalink]:
        """Roll ``count`` fresh seeds from the pool's preset into permalinks.

        ``on_progress(n, count)`` is called before the n-th roll, so a caller can
        say how far a batch that takes minutes has got.
        """
        pool = require_found(await self.pool_repository.get_with_permalinks(pool_id), "Pool")
        await self._ensure_pool_admin(actor, pool)
        # Re-checked here and not only in the picker: a pool created before DK64
        # became asynchronous can already point at one.
        preset = pool.preset
        refusal = roll_refusal(preset)
        if refusal is not None or preset is None:
            raise ValueError(refusal or "Pool has no preset to roll from")
        if count < 1 or count > MAX_ROLL_COUNT:
            raise ValueError(f"Roll count must be between 1 and {MAX_ROLL_COUNT}")
        # A keyed randomizer raises on the first roll when this community has not
        # configured its credential — before any permalink row is created, so the
        # batch aborts with nothing half-written.
        seedgen = SeedGenerationService()
        created: List[AsyncQualifierPermalink] = []
        for n in range(1, count + 1):
            if on_progress is not None:
                on_progress(n, count)
            call = await seedgen.generate_seed_call(
                preset.randomizer, preset, surface='qualifier',
            )
            # Same provenance record a match seed gets: the pool records which
            # preset it rolls from, but only the snapshot records what that preset
            # *said* at the moment this permalink was made.
            seed = await GeneratedSeeds.create(
                tenant_id=require_tenant_id(),
                seed_url=call.value.url,
                seed_info=f"Rolled for qualifier pool {pool_id}",
                randomizer=preset.randomizer,
                preset_id=pool.preset_id,  # type: ignore[attr-defined]
                settings_snapshot=call.value.settings,
                rolled_by_id=actor.id if actor is not None else None,
                provider_meta=call.as_meta(),
                seed_code=call.value.code or None,
            )
            created.append(await self.permalink_repository.create(
                pool_id=pool_id, url=call.value.url, generated_seed_id=seed.id,
            ))
        await self.audit_service.write_log(
            actor, AuditActions.ASYNC_QUALIFIER_PERMALINK_ADDED,
            {'pool_id': pool_id, 'count': len(created), 'rolled': True},
        )
        return created

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def update_permalink(
        self,
        actor: Optional[User],
        permalink_id: int,
        *,
        url: Optional[str] = None,
        notes: Optional[str] = None,
        live_race: Optional[bool] = None,
    ) -> AsyncQualifierPermalink:
        permalink = await self._require_permalink(permalink_id)
        await self._ensure_permalink_admin(actor, permalink)
        changes: dict = {}
        if url is not None:
            changes['url'] = rules.validate_permalink_url(url)
        if notes is not None:
            changes['notes'] = notes.strip() or None
        if live_race is not None:
            changes['live_race'] = live_race
        permalink = await self.permalink_repository.update(permalink, **changes)
        await self.audit_service.write_log(
            actor, AuditActions.ASYNC_QUALIFIER_PERMALINK_UPDATED,
            {'permalink_id': permalink.id, 'fields': sorted(changes.keys())},
        )
        return permalink

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def delete_permalink(self, actor: Optional[User], permalink_id: int) -> None:
        permalink = await self._require_permalink(permalink_id)
        await self._ensure_permalink_admin(actor, permalink)
        await self.audit_service.write_log(
            actor, AuditActions.ASYNC_QUALIFIER_PERMALINK_DELETED,
            {'permalink_id': permalink.id, 'pool_id': permalink.pool_id},
        )
        await self.permalink_repository.delete(permalink)

    # --------------------------------------------------------------- gateways

    async def _require_pool(self, pool_id: int) -> AsyncQualifierPool:
        return await access.require_pool(self.pool_repository, pool_id)

    async def _require_permalink(self, permalink_id: int) -> AsyncQualifierPermalink:
        return await access.require_permalink(self.permalink_repository, permalink_id)

    async def _ensure_pool_admin(self, actor: Optional[User], pool: AsyncQualifierPool) -> None:
        qualifier = await self._require_qualifier(pool.qualifier_id)
        await access.ensure_qualifier_admin(actor, qualifier)

    async def _ensure_permalink_admin(
        self, actor: Optional[User], permalink: AsyncQualifierPermalink
    ) -> None:
        pool = await self._require_pool(permalink.pool_id)
        await self._ensure_pool_admin(actor, pool)
