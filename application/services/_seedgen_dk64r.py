"""The Donkey Kong 64 Randomizer backend (api.dk64rando.com).

Split out of ``seedgen_service`` because DK64R carries more machinery than every
other backend put together: its upstream is a task queue (submit -> poll ->
result) rather than a request, it expands a portable settings string before it
can roll, and it publishes a preset catalogue of its own.

:class:`DK64RBackend` is a mixin on ``SeedGenerationService`` rather than a
standalone client: it reads this tenant's credential through the service's
``_credential`` and returns the same value objects every other backend does.
"""

import asyncio
import json
import time
from typing import List, Optional

import aiohttp

import application.services._seedgen_codes as seed_codes
from application.services._seedgen_types import RemotePreset, RolledSeed
from application.utils.mocks.mock_dk64 import MockDK64Session, is_mock_dk64
from application.utils.seed_provider import (
    SeedProviderBadResponse,
    SeedProviderError,
    SeedProviderInvalidRequest,
    SeedProviderRateLimited,
    SeedProviderUnavailable,
)
from models import Preset

# DK64 Randomizer (api.dk64rando.com) — a task-queue backend: submit → poll →
# result.
DK64R_API_BASE = 'https://api.dk64rando.com/api'
DK64R_BRANCHES = {'stable', 'dev'}
# The player-facing site host per branch (the permalink the roll returns).
DK64R_SITE_HOSTS = {'stable': 'dk64randomizer.com', 'dev': 'dev.dk64randomizer.com'}
DK64R_POLL_INTERVAL = 5.0        # seconds — matches the reference web client's cadence
DK64R_GENERATION_TIMEOUT = 600.0  # seconds (10 min) worst-case queue + generation budget


class DK64RBackend:
    """Every outbound call DK64R needs, from settings conversion to the catalogue."""

    async def _dk64r_presets(self, branch: Optional[str]) -> List[RemotePreset]:
        """Fetch DK64R's published presets for one branch.

        Each entry's ``settings_string`` is the site's own portable format; it is
        stored as-is and expanded through ``/convert_settings`` at roll time
        (``_dk64r_resolve_settings``), so an imported preset keeps whatever the
        upstream currently means by it rather than a snapshot expanded here.
        """
        branch = branch or 'stable'
        if branch not in DK64R_BRANCHES:
            raise ValueError(
                f"Unknown DK64R branch '{branch}' (expected 'stable' or 'dev')."
            )

        async with await self._dk64r_session() as session:
            async with session.get(
                f"{DK64R_API_BASE}/get_presets", params={'branch': branch},
            ) as resp:
                if resp.status != 200:
                    raise await self._dk64r_error(resp, 'list the presets')
                data = await resp.json()

        if not isinstance(data, list):
            raise SeedProviderBadResponse(
                'DK64 Randomizer returned an unexpected preset catalogue.',
                provider='dk64r', operation='list_presets',
            )

        presets: List[RemotePreset] = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get('name') or '').strip()
            settings_string = entry.get('settings_string')
            # An entry without either is not importable — skipping it beats
            # failing the whole catalogue over one malformed row.
            if not name or not settings_string:
                continue
            description = (entry.get('description') or '').strip() or None
            presets.append(RemotePreset(
                name=name,
                description=description,
                settings={'_branch': branch, 'settings_string': str(settings_string)},
            ))
        return presets

    @staticmethod
    def _dk64r_seed(result: dict, branch: str, settings: Optional[dict]) -> RolledSeed:
        seed_number = result.get('seed_number')
        if seed_number is None:
            raise ValueError('DK64 Randomizer returned no seed identifier.')
        return RolledSeed(
            url=f"https://{DK64R_SITE_HOSTS[branch]}/randomizer.html?seed_id={seed_number}",
            settings=settings or None,
            code=seed_codes.dk64r_code(result.get('hash')),
        )

    async def _dk64r_prepare(self, preset: Optional[Preset]) -> tuple:
        """Resolve a DK64R preset into ``(branch, settings, settings_string)``.

        Pure: no HTTP and no credential, so an unrecognised branch raises before
        anything is sent or persisted.
        """
        if preset is not None:
            settings = dict(preset.settings or {})
        else:
            # Read off the shared event loop — a blocking file read stalls every
            # connected user (CLAUDE.md: never block the event loop).
            def _read_default() -> dict:
                with open("presets/dk64r/sgl.json", "r", encoding="utf-8") as f:
                    return json.load(f)

            settings = await asyncio.to_thread(_read_default)

        # Optional per-preset branch override, stripped before anything is sent.
        branch = str(settings.pop('_branch', 'stable'))
        if branch not in DK64R_BRANCHES:
            raise ValueError(
                f"Unknown DK64R branch '{branch}' (expected 'stable' or 'dev')."
            )

        # A settings string is the site's own portable preset format; expand it
        # to the full settings dict. A full-JSON preset is submitted as-is
        # (converting a dict would yield a string — the opposite direction).
        return branch, settings, settings.get('settings_string')

    async def _dk64r_session(self):
        """An HTTP session for DK64R: the real one, or the simulated queue.

        Each roll phase opens its own, because a poll may run minutes after its
        submit and in a different process — there is no session to keep alive
        across that gap. The mock's task registry is process-wide for the same
        reason.
        """
        # The simulated queue authenticates nobody, so dev and CI roll DK64
        # without a credential row — matching every other backend's behaviour
        # under MOCK_SEEDGEN, which short-circuits before its own key lookup.
        if is_mock_dk64():
            return MockDK64Session()
        api_key = await self._credential('dk64r', 'api_key')
        return aiohttp.ClientSession(
            headers={'X-API-Key': api_key}, timeout=aiohttp.ClientTimeout(total=60),
        )

    async def _dk64r_resolve_settings(
        self, session, settings: dict, settings_string: Optional[str], params: dict,
    ) -> dict:
        """Expand the preset if needed, then force the spoiler log off."""
        settings_dict = (
            await self._dk64r_convert(session, settings_string, params)
            if settings_string is not None else settings
        )

        # Never generate a spoiler log, whatever the preset asked for. The
        # upstream serves one to anyone holding the seed number at
        # ``GET /get_spoiler_log?hash=<seed_number>``, and the seed number is the
        # permalink we DM to the players — so a preset with this on hands every
        # racer the item locations. Several of the site's own presets, including
        # "Season 5 Race Settings", convert with it set to true, so trusting the
        # preset is not a safe default. Forced last, after both settings shapes
        # have resolved, and it lands in the snapshot ``GeneratedSeeds`` records.
        settings_dict['generate_spoilerlog'] = False
        return settings_dict

    async def _dk64r_convert(self, session, settings_string: str, params: dict) -> dict:
        """Expand a DK64R settings string into the full settings JSON object."""
        async with session.post(
            f"{DK64R_API_BASE}/convert_settings",
            json={'settings': settings_string}, params=params,
        ) as resp:
            if resp.status != 200:
                raise await self._dk64r_error(resp, 'convert settings')
            data = await resp.json()
        if not isinstance(data, dict):
            raise ValueError('DK64 Randomizer returned an unexpected settings payload.')
        return data

    async def _dk64r_submit(self, session, settings_dict: dict, params: dict) -> str:
        """Submit a generation task; return its task id."""
        async with session.post(
            f"{DK64R_API_BASE}/submit-task",
            json={'settings_data': json.dumps(settings_dict)}, params=params,
        ) as resp:
            if resp.status != 200:
                raise await self._dk64r_error(resp, 'submit the seed')
            data = await resp.json()
        task_id = (data or {}).get('task_id')
        if not task_id:
            raise ValueError('DK64 Randomizer did not return a task id.')
        return task_id

    async def _dk64r_poll(self, session, task_id: str, params: dict) -> dict:
        """Poll a task until it finishes; return its ``result`` object.

        Bounded by ``DK64R_GENERATION_TIMEOUT``. A crashed task surfaces as HTTP
        500; the reference client also defends against a ``failed`` status inside
        a 200, so both are handled.
        """
        deadline = time.monotonic() + DK64R_GENERATION_TIMEOUT
        while time.monotonic() < deadline:
            result = await self._dk64r_poll_once(session, task_id, params)
            if result is not None:
                return result
            await asyncio.sleep(DK64R_POLL_INTERVAL)
        raise ValueError('DK64 Randomizer seed generation timed out.')

    async def _dk64r_poll_once(self, session, task_id: str, params: dict) -> Optional[dict]:
        """Check a task once: its ``result`` if finished, ``None`` if still going.

        A crashed task surfaces as HTTP 500; the reference client also defends
        against a ``failed`` status inside a 200, so both raise here. Everything
        that merely means "not done yet" returns ``None``, which is what lets the
        persisted poller tell a task worth re-checking from a dead end.
        """
        async with session.get(
            f"{DK64R_API_BASE}/task-status/{task_id}", params=params,
        ) as resp:
            if resp.status != 200:
                raise await self._dk64r_error(resp, 'generate the seed')
            data = await resp.json()
        status = (data or {}).get('status')
        if status == 'finished':
            result = data.get('result')
            if not isinstance(result, dict):
                raise ValueError('DK64 Randomizer finished without a seed result.')
            return result
        if status == 'failed':
            raise ValueError('DK64 Randomizer failed to generate the seed.')
        if status not in ('queued', 'started'):
            raise ValueError(
                f"DK64 Randomizer returned an unexpected status '{status}'."
            )
        return None

    @staticmethod
    async def _dk64r_error(resp, action: str) -> SeedProviderError:
        """The failed DK64R call as the shared taxonomy, keeping its upstream text.

        A plain ``ValueError`` here was classed "bad response" and never retried,
        so a 503 or 429 failed the call outright.
        """
        detail = ''
        try:
            body = await resp.json()
            if isinstance(body, dict):
                detail = body.get('error') or body.get('message') or ''
        except Exception:
            try:
                detail = (await resp.text())[:200]
            except Exception:
                detail = ''
        suffix = f': {detail}' if detail else ''
        message = f"DK64 Randomizer failed to {action} (HTTP {resp.status}){suffix}"
        cls: type[SeedProviderError]
        if resp.status == 429:
            cls = SeedProviderRateLimited
        elif 500 <= resp.status < 600:
            cls = SeedProviderUnavailable
        else:
            cls = SeedProviderInvalidRequest
        return cls(
            message, provider='dk64r', operation=action,
            status_code=resp.status, provider_message=detail or None,
        )
