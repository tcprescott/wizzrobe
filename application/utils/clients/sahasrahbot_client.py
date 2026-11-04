"""SahasrahBot's public preset API, for importing a player's presets.

SahasrahBot (github.com/tcprescott/sahasrahbot) kept presets in namespaces, one
per Discord user, named ``namespace/preset`` like Wizzrobe's spaces. Its API is
public and read-only:

* ``GET /presets/api/<randomizer>/list`` →
  ``{"global": [names], "namespaces": [{"name": ns, "presets": [names]}]}``.
* ``GET /presets/api/<randomizer>?preset=<ns>/<name>`` →
  ``{"preset": ..., "randomizer": ..., "data": {the preset, parsed}}``, or
  ``{}`` when there's no such preset.

The YAML holds ``settings`` plus flags saying how SahasrahBot rolled it. Wizzrobe
can't reproduce ``forced_locations`` (SahasrahBot placed those items itself), so
:func:`to_wizzrobe` refuses those with the reason. It maps ``doors`` presets to the
Door Randomizer (spoiler for staff, its default) and ``avianart`` ones to the
AVIANART randomizer.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import aiohttp
import yaml

SAHASRAHBOT_API = 'https://sahasrahbotapi.synack.live'
RANDOMIZERS = ('alttpr', 'alttprmystery', 'sm', 'smz3')
REQUEST_TIMEOUT_SECONDS = 20
MAX_PRESET_BYTES = 64 * 1024
ROLL_OWNED_KEYS = frozenset({'seed', 'race', 'spoilerKey'})


class SahasrahBotAPIError(Exception):
    """SahasrahBot didn't answer, or sent something this module doesn't recognise."""


@dataclass(frozen=True)
class ImportablePreset:
    randomizer: str
    settings: Dict[str, Any]
    description: Optional[str]


async def _get(path: str, params: Optional[Dict[str, str]] = None) -> Any:
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                f'{SAHASRAHBOT_API}{path}', params=params, headers={'User-Agent': 'Wizzrobe preset import'},
            ) as resp:
                if resp.status != 200:
                    raise SahasrahBotAPIError(f'SahasrahBot answered {resp.status}')
                text = await resp.text()
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        raise SahasrahBotAPIError("Couldn't reach SahasrahBot. Try again in a minute.") from e
    try:
        return json.loads(text)
    except ValueError as e:
        raise SahasrahBotAPIError('SahasrahBot sent something that isn\'t JSON') from e


async def namespace_presets(randomizer: str, namespace: str) -> List[str]:
    """The names of one namespace's presets for one randomizer; empty when it has none."""
    payload = await _get(f'/presets/api/{randomizer}/list')
    rows = payload.get('namespaces') if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise SahasrahBotAPIError('SahasrahBot sent a preset list in a shape Wizzrobe doesn\'t know')
    for row in rows:
        if isinstance(row, dict) and str(row.get('name', '')).lower() == namespace.lower():
            return [str(p) for p in row.get('presets') or [] if isinstance(p, str)]
    return []


async def fetch_preset(randomizer: str, namespace: str, name: str) -> Dict[str, Any]:
    """One preset's parsed YAML, or :class:`SahasrahBotAPIError`."""
    payload = await _get(f'/presets/api/{randomizer}', {'preset': f'{namespace}/{name}'})
    data = payload.get('data') if isinstance(payload, dict) else None
    if isinstance(data, str):
        if len(data.encode()) > MAX_PRESET_BYTES:
            raise SahasrahBotAPIError(f'{namespace}/{name} is too big to import')
        try:
            data = yaml.safe_load(data)
        except yaml.YAMLError as e:
            raise SahasrahBotAPIError(f"{namespace}/{name} isn't valid YAML") from e
    elif data is None:
        raise SahasrahBotAPIError(f"SahasrahBot has no {randomizer} preset {namespace}/{name}")
    elif len(json.dumps(data, default=str).encode()) > MAX_PRESET_BYTES:
        raise SahasrahBotAPIError(f'{namespace}/{name} is too big to import')
    if not isinstance(data, dict):
        raise SahasrahBotAPIError(f"{namespace}/{name} isn't a preset")
    return data


def to_wizzrobe(randomizer: str, data: Dict[str, Any]) -> ImportablePreset:
    """What Wizzrobe stores for a SahasrahBot preset, or ``ValueError`` saying why it can't."""
    if randomizer == 'alttprmystery':
        weights = {k: v for k, v in data.items() if k != 'description'}
        description = data.get('description')
        return ImportablePreset('alttprmystery', weights, str(description).strip() if description else None)
    settings = data.get('settings')
    if not isinstance(settings, dict):
        raise ValueError('it has no settings')
    if data.get('forced_locations'):
        raise ValueError('it places items itself (forced_locations), which Wizzrobe can\'t do')
    description = data.get('description') or data.get('goal_name')
    description = str(description).strip() if description else None
    if randomizer == 'alttpr' and data.get('doors'):
        return ImportablePreset('alttprdr', {'settings': settings, 'spoiler': 'staff'}, description)
    if randomizer == 'alttpr' and data.get('avianart'):
        return ImportablePreset('avianart', {'preset': settings.get('preset')}, description)
    if randomizer in ('sm', 'smz3'):
        settings = {k: v for k, v in settings.items() if k not in ROLL_OWNED_KEYS}
    return ImportablePreset(randomizer, settings, description)

