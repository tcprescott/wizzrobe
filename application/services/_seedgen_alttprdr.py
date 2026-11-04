"""The ALttP Door Randomizer backend: our own sidecar, our own patch hosting.

Aerinon's Door Randomizer has no public API, so it runs in a container of its
own (``sidecars/door_rando``) on the private compose network. That container
never holds a ROM: it returns the seed as byte writes on top of the
randomizer's base patch. This backend stores both under an unguessable token
and hands back a link to Wizzrobe's own patcher page, where the player's
browser applies them to their own Japanese 1.0 ROM.

:class:`DoorRandoBackend` is a mixin on ``SeedGenerationService``, like
``DK64RBackend``, and returns the same :class:`RolledSeed` every backend does.
"""

import json
import os
import secrets
from typing import Optional, Tuple

import aiohttp

import application.services._seedgen_codes as seed_codes
from application.repositories import DoorSeedRepository
from application.services._seedgen_types import RolledSeed
from application.utils.door_seed_store import DoorSeedStore
from application.utils.seed_provider import SeedProviderBadResponse, raise_for_status
from models import Preset

DOOR_SPOILER_ACCESS = ('public', 'staff', 'none')
# The sidecar retries a failed layout itself (DOOR_RANDO_ATTEMPTS x
# DOOR_RANDO_ATTEMPT_TIMEOUT, 6 x 60s by default), so one call can run long.
DOOR_RANDO_TIMEOUT = 380.0


def door_rando_url() -> str:
    return os.getenv('DOOR_RANDO_URL', '').rstrip('/')


def door_rando_configured() -> bool:
    return bool(door_rando_url())


def door_preset_settings(settings: Optional[dict]) -> Tuple[dict, str]:
    """A Door Randomizer preset's randomizer settings and spoiler access, checked.

    The settings are the randomizer's own settings-file keys (the same shape
    SahasrahBot's ``doors: true`` presets carry); the sidecar refuses any key
    the randomizer doesn't know, so only the outer shape is judged here.
    """
    if not isinstance(settings, dict):
        raise ValueError('A Door Randomizer preset needs "settings": the randomizer\'s settings.')
    unknown = set(settings) - {'settings', 'spoiler'}
    if unknown:
        raise ValueError(
            f"A Door Randomizer preset holds \"settings\" and \"spoiler\", not {', '.join(sorted(unknown))}."
        )
    inner = settings.get('settings')
    if not isinstance(inner, dict) or not inner:
        raise ValueError('A Door Randomizer preset needs "settings": the randomizer\'s settings.')
    spoiler = settings.get('spoiler', 'staff')
    if spoiler not in DOOR_SPOILER_ACCESS:
        raise ValueError('"spoiler" is one of public, staff or none.')
    return inner, spoiler


class DoorRandoBackend:
    """Roll through the sidecar, then host what it made."""

    def _door_headers(self) -> dict:
        token = os.getenv('DOOR_RANDO_TOKEN', '')
        return {'Authorization': f'Bearer {token}'} if token else {}

    async def _generate_alttprdr(self, preset: Optional[Preset] = None) -> RolledSeed:
        if preset is None:
            raise ValueError('Door Randomizer seeds roll from a preset. Attach one to this tournament.')
        settings, spoiler_access = door_preset_settings(preset.settings)
        base = door_rando_url()
        if not base:
            raise ValueError('The Door Randomizer service is not set up on this server.')

        timeout = aiohttp.ClientTimeout(total=DOOR_RANDO_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout, headers=self._door_headers()) as session:
            async with session.post(f'{base}/generate', json={'settings': settings}) as resp:
                await raise_for_status(resp, provider='alttprdr', operation='generate_seed')
                result = await resp.json()
            version = result.get('version')
            patch = result.get('patch')
            if not isinstance(version, str) or not isinstance(patch, dict):
                raise SeedProviderBadResponse(
                    'The Door Randomizer service answered without a patch.',
                    provider='alttprdr', operation='generate_seed',
                )
            store = DoorSeedStore()
            if not await store.has_base_patch(version):
                async with session.get(f'{base}/base-patch') as resp:
                    await raise_for_status(resp, provider='alttprdr', operation='base_patch')
                    if resp.headers.get('X-Door-Rando-Version') != version:
                        raise SeedProviderBadResponse(
                            'The Door Randomizer service changed version mid-roll. Try again.',
                            provider='alttprdr', operation='base_patch',
                        )
                    await store.put_base_patch(version, await resp.read())

        token = secrets.token_urlsafe(16)
        code = seed_codes.alttpr_code([patch])
        await store.put_patch(token, json.dumps({'version': version, 'patch': patch}, separators=(',', ':')).encode())
        spoiler = result.get('spoiler')
        if spoiler_access != 'none' and spoiler is not None:
            await store.put_spoiler(token, json.dumps(spoiler, indent=2).encode())
        await DoorSeedRepository.create(
            token=token, version=version, seed_code=code, spoiler_access=spoiler_access,
        )
        return RolledSeed(
            url=await self._door_seed_url(token),
            settings={'settings': settings, 'spoiler': spoiler_access, 'version': version, 'seed': result.get('seed')},
            code=code,
        )

    @staticmethod
    async def _door_seed_url(token: str) -> str:
        from application.services.tenant_service import TenantService
        from application.tenant_context import require_tenant_id
        from application.utils.tenant_urls import tenant_url

        tenant = await TenantService.get_by_id(require_tenant_id())
        return tenant_url(tenant, f'/door/{token}')
