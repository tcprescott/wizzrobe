"""The app's Admin → Seed Presets screen: what the preset editor needs to know
about each randomizer, checking a pasted permalink or flag string, test rolls
and importing a randomizer's own published presets.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from application.services.auth_service import AuthService
from application.services.preset_service import PresetService, PresetTestRoll
from application.services.randomizer_credential_service import RandomizerCredentialService
from application.services.seedgen_service import SeedGenerationService
from models import Preset, User

# randomizer: (settings key, field label, placeholder, hint). Shared with the NiceGUI Presets tab.
PASTE_FIELDS: Dict[str, Tuple[str, str, str, str]] = {
    'wwr': (
        'permalink', 'Permalink', "Paste the permalink from the randomizer's Permalink box",
        'Each roll keeps the build and settings and only changes the seed.',
    ),
    'ff1r': (
        'url', 'Flags link', 'https://4-9-7.finalfantasyrandomizer.com/?s=00000000&f=…',
        "Copy the link from the randomizer's address bar after setting the flags. "
        'Each roll keeps the flags and site version and only changes the seed.',
    ),
    'z1r': (
        'flags', 'Flags', 'The flag string from the randomizer',
        'Each roll adds a fresh seed number to these flags.',
    ),
    'z2r': (
        'flags', 'Flags', 'The flag string from the randomizer',
        'Each roll adds a fresh seed number to these flags.',
    ),
    'smb3r': (
        'flags', 'Flags', 'The flag string from the randomizer',
        'Each roll adds a fresh seed number to these flags.',
    ),
    'aosr': (
        'url', 'Randomizer link', 'https://aosrando.surge.sh/?…',
        'The aosrando.surge.sh link with your options in it. Each roll keeps the options and only changes the seed.',
    ),
    'avianart': (
        'preset', 'AVIANART preset', 'crosskeys',
        'The preset name from avianart.games, or owner/name for a user preset. '
        'Import → From a randomizer… lists the published ones.',
    ),
}


@dataclass
class PasteField:
    key: str
    label: str
    placeholder: str
    hint: str


@dataclass
class RandomizerEditorInfo:
    slug: str
    name: str
    available: bool
    rolls_from_flags: bool
    paste_field: Optional[PasteField]
    offers_remote_presets: bool
    remote_branches: List[str]


@dataclass
class PresetUsage:
    preset_id: int
    tournaments: List[str]


@dataclass
class PresetEditorContext:
    randomizers: List[RandomizerEditorInfo]
    usage: List[PresetUsage]


@dataclass
class PasteCheck:
    """What a pasted value says about itself, for the editor to show under the field.

    kind is site_version (an FFR link), example (a flag string, with a
    sample of what a player gets), build (a Wind Waker permalink) or none.
    """

    kind: str
    site_version: Optional[str] = None
    example: Optional[str] = None
    build: Optional[str] = None
    version: Optional[str] = None
    summary: Optional[List[str]] = None


@dataclass
class RemotePresetView:
    name: str
    description: Optional[str]


class AdminPresetsService:
    """Stateless; every method is gated on managing presets."""

    async def _ensure(self, actor: User) -> None:
        await AuthService.ensure(await AuthService.can_manage_presets(actor), 'Cannot manage presets')

    async def editor_context(self, actor: User) -> PresetEditorContext:
        await self._ensure(actor)
        configured = await RandomizerCredentialService().configured_randomizers()
        available = set(SeedGenerationService.available_randomizers(configured))
        randomizers = []
        for slug in SeedGenerationService.AVAILABLE_RANDOMIZERS:
            field = PASTE_FIELDS.get(slug)
            randomizers.append(RandomizerEditorInfo(
                slug=slug,
                name=SeedGenerationService.randomizer_name(slug),
                available=slug in available,
                rolls_from_flags=SeedGenerationService.rolls_from_flags(slug),
                paste_field=PasteField(*field) if field else None,
                offers_remote_presets=SeedGenerationService.offers_remote_presets(slug),
                remote_branches=SeedGenerationService.remote_preset_branches(slug),
            ))
        usage = await PresetService().usage(actor)
        return PresetEditorContext(
            randomizers=randomizers,
            usage=[PresetUsage(pid, sorted(names)) for pid, names in sorted(usage.items())],
        )

    async def check_paste(self, actor: User, randomizer: str, value: str) -> PasteCheck:
        """Raises ``ValueError`` with the reason when the value can't be read."""
        await self._ensure(actor)
        value = (value or '').strip()
        if not value or randomizer in ('aosr', 'avianart') or randomizer not in PASTE_FIELDS:
            return PasteCheck(kind='none')
        if randomizer == 'ff1r':
            return PasteCheck(kind='site_version', site_version=SeedGenerationService.describe_ff1r_link(value))
        if randomizer == 'wwr':
            info = SeedGenerationService.describe_wwr_permalink(value)
            return PasteCheck(kind='build', build=info.build, version=info.version, summary=info.summary)
        return PasteCheck(kind='example', example=SeedGenerationService.example_flag_seed(randomizer, value))

    async def test_roll(self, actor: User, preset_id: int) -> PresetTestRoll:
        return await PresetService().test_roll(actor, preset_id)

    async def remote_presets(self, actor: User, randomizer: str, branch: Optional[str]) -> List[RemotePresetView]:
        rows = await PresetService().list_remote_presets(actor, randomizer, branch=branch)
        return [RemotePresetView(r.name, r.description) for r in rows]

    async def import_remote(
        self, actor: User, randomizer: str, names: List[str], branch: Optional[str],
    ) -> List[Preset]:
        return await PresetService().import_remote_presets(actor, randomizer, names, branch=branch)
