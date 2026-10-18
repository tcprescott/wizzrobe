"""
Seed Generation Service - Business Logic Layer

Handles random seed generation for various randomizers.
Supports: ALTTPR, AVIANART, FF1R, Z1R, Z2R, SMB3R, AoSR, SM, SMZ3, SMMAP, OOTR,
DK64R, DASH, VARIA, WWR, and Test.
"""

import asyncio
import copy
import json
import random
import re
import secrets
import urllib.parse
from pathlib import Path
from typing import ClassVar, Dict, List, Optional, Set

import aiohttp
import yaml
from pyz3r import ALTTPR
from pyz3r.exceptions import Pyz3rException

import application.services._seedgen_codes as seed_codes
import application.services._seedgen_local as local_rolls
import application.services._seedgen_wwr as wwr_permalinks
from application.errors import MissingCredentialError
from application.randomizer_credentials import credentials_for, spec_for
from application.services._seedgen_avianart import (
    AVIANART_MAX_POLLS,
    AVIANART_POLL_INTERVAL,
    avianart_preset_name,
    avianart_presets,
    generate_avianart,
)
from application.services._seedgen_dk64r import (
    DK64R_GENERATION_TIMEOUT,
    DK64R_SITE_HOSTS,
    DK64RBackend,
)
from application.services._seedgen_samus import generate_samus, samus_settings
from application.services._seedgen_types import (
    AsyncRollPoll,
    AsyncRollSubmission,
    RemotePreset,
    RolledSeed,
    WWPermalinkInfo,
)
from application.services._seedgen_varia import VARIA_GENERATION_TIMEOUT, generate_varia, varia_settings
from application.utils.mocks.mock_seedgen import is_mock_seedgen
from application.utils.seed_provider import (
    PROVIDER_MAX_ATTEMPTS,
    PROVIDER_TIMEOUT_SECONDS,
    ProviderCall,
    SeedProviderBadResponse,
    SeedProviderInvalidRequest,
    call_provider,
    raise_for_status,
)
from models import Preset

__all__ = [
    'AsyncRollPoll',
    'AsyncRollSubmission',
    'RemotePreset',
    'RolledSeed',
    'SeedGenerationService',
    'WWPermalinkInfo',
]


async def _read_bundled_preset(path: str) -> str:
    """Read a committed preset file without blocking the shared event loop.

    These are small files that ship in the image, but every connected client
    shares one loop — a synchronous ``open()`` on a seed roll stalls all of
    them, so the read hops to a worker thread.
    """
    return await asyncio.to_thread(Path(path).read_text, encoding='utf-8')


# The bare domain 308s to www, and the generate call is read without following
# redirects, so it has to start on the host that answers it.
_DASH_BASE_URL = 'https://www.dashrando.net'


class SeedGenerationService(DK64RBackend):
    """Service for generating seeds for various randomizers."""

    # Available randomizers
    AVAILABLE_RANDOMIZERS: ClassVar[List[str]] = [
        'alttpr',
        'avianart',
        'ff1r',
        'z1r',
        'z2r',
        'smb3r',
        'aosr',
        'sm',
        'smz3',
        'smmap',
        'ootr',
        'smdash',
        'smvaria',
        'dk64r',
        'wwr',
        'test',
    ]

    # What a person calls each one; the slug above is what the data stores.
    RANDOMIZER_NAMES: ClassVar[Dict[str, str]] = {
        'alttpr': 'A Link to the Past',
        'avianart': 'A Link to the Past (AVIANART)',
        'ff1r': 'Final Fantasy',
        'z1r': 'Zelda 1',
        'z2r': 'Zelda 2',
        'smb3r': 'Super Mario Bros. 3',
        'aosr': 'Aria of Sorrow',
        'sm': 'Super Metroid',
        'smz3': 'SMZ3 (Super Metroid + A Link to the Past)',
        'smmap': 'Super Metroid Map Rando',
        'ootr': 'Ocarina of Time',
        'smdash': 'Super Metroid DASH',
        'smvaria': 'Super Metroid VARIA',
        'dk64r': 'Donkey Kong 64',
        'wwr': 'Wind Waker',
        'test': 'Test randomizer',
    }

    # Randomizers registered for selection but whose generator is not yet
    # wired to an upstream API — rolling one raises ``ValueError``.
    STUB_RANDOMIZERS: ClassVar[Set[str]] = set()

    # Randomizers whose generator resolves a ``Preset`` (its settings feed the
    # roll). Anything else ignores the preset and rolls hard-coded settings.
    PRESET_AWARE_RANDOMIZERS: ClassVar[Set[str]] = {
        'alttpr', 'aosr', 'avianart', 'dk64r', 'ff1r', 'sm', 'smb3r', 'smdash', 'smvaria', 'smz3', 'wwr',
        'z1r', 'z2r',
    }

    # Rolls that ``MOCK_SEEDGEN`` leaves alone. DK64R mocks one layer down (see
    # ``generate_seed_call``); WWR, Z1R, Z2R, SMB3R and AoSR are built locally
    # with no upstream to stand in for, and most of their seeds are plain strings
    # rather than URLs, which is exactly what the dev environment needs to show
    # for the copy card to be seen.
    MOCK_EXEMPT_RANDOMIZERS: ClassVar[Set[str]] = {'aosr', 'dk64r', 'smb3r', 'wwr', 'z1r', 'z2r'}

    # Backends whose roll is a task queue rather than a request: submit now,
    # collect minutes later. These do not return a seed from one call, so every
    # surface that rolls one has to decide what to do with a task instead —
    # ``MatchScheduleService`` queues a ``ProviderTask``, REST answers 202, and
    # qualifier batch rolls refuse until that path is converted.
    ASYNC_RANDOMIZERS: ClassVar[Set[str]] = {'dk64r'}

    # Randomizers whose generator can embed community triforce texts.
    TRIFORCE_TEXT_RANDOMIZERS: ClassVar[Set[str]] = {'alttpr'}

    # Randomizers that publish their own preset catalogue over their API, so a
    # community can pull the upstream's presets instead of hand-authoring the
    # settings blob. The value is the catalogue's variants — DK64R publishes a
    # separate list per branch — or an empty tuple where there is only one list.
    REMOTE_PRESET_BRANCHES: ClassVar[dict] = {
        'avianart': (),
        'dk64r': ('stable', 'dev'),
    }

    # Per-provider overrides of the shared execution contract
    # (``application.utils.seed_provider``). Anything absent takes the defaults.
    #
    # DK64R is the one that must differ on both axes: its upstream is a task
    # queue whose *whole* roll (submit → poll → result) legitimately runs for
    # minutes, so the outer budget has to exceed the poll deadline rather than
    # cut it short — and re-running that roll would submit a second generation
    # task, so it gets exactly one attempt. Retrying the individual requests
    # inside it is safe and is handled within the generator.
    PROVIDER_TIMEOUTS: ClassVar[dict] = {
        'avianart': AVIANART_MAX_POLLS * AVIANART_POLL_INTERVAL + 30.0,
        'dk64r': DK64R_GENERATION_TIMEOUT + 60.0,
        'smvaria': VARIA_GENERATION_TIMEOUT + 10.0,
    }
    PROVIDER_ATTEMPTS: ClassVar[dict] = {
        # AVIANART builds a game per generate call; its polls run inside the attempt.
        'avianart': 1,
        'dk64r': 1,
        # DASH saves the seed upstream before answering, so a retry after a
        # timeout mints a second seed and orphans the first.
        'smdash': 1,
        # VARIA stores every seed it builds, so a retry orphans one.
        'smvaria': 1,
    }

    @staticmethod
    def normalize_preset_settings(randomizer: str, settings: dict) -> dict:
        """Check a preset's settings for what its randomizer needs, before it's saved.

        Most backends hand their settings upstream untouched and let upstream
        judge them. WWR has no upstream, so a pasted permalink is decoded here,
        and one that isn't a permalink, or doesn't fit its vendored build, is
        refused with a ``ValueError`` rather than at the desk minutes before a race.
        FFR, AoSR and the flag-string games are built locally too, so theirs are
        checked here as well, as are AVIANART and VARIA preset names and samus.link settings.
        """
        if randomizer == 'ff1r':
            return {'url': local_rolls.ff1r_reference_url(settings)}
        if randomizer == 'aosr':
            return {'url': local_rolls.aosr_reference_url(settings)}
        if randomizer in local_rolls.FLAG_GAMES:
            return {'flags': local_rolls.flag_string(randomizer, settings)}
        if randomizer == 'avianart':
            return {'preset': avianart_preset_name(settings)}
        if randomizer in ('sm', 'smz3'):
            return samus_settings(randomizer, settings)
        if randomizer == 'smvaria':
            return varia_settings(settings)
        if randomizer != 'wwr':
            return settings
        permalink = settings.get('permalink')
        if not isinstance(permalink, str) or not permalink.strip():
            raise ValueError('A Wind Waker preset needs a "permalink" copied from the randomizer.')
        unknown = set(settings) - {'permalink'}
        if unknown:
            raise ValueError(f"A Wind Waker preset holds only a permalink, not {', '.join(sorted(unknown))}.")
        permalink = permalink.strip()
        wwr_permalinks.summarize(permalink)
        return {'permalink': permalink}

    @staticmethod
    def paste_instructions(randomizer: str, settings: Optional[dict]) -> Optional[str]:
        """What a player does with a seed that isn't a link, or ``None`` for one that is.

        WWR and Z1R seeds are pasted into the player's own copy of the
        randomizer. WWR names its build, because a permalink only loads in the
        exact build that made it and the randomizer's own error message is the
        first a player would otherwise hear of it.
        """
        if randomizer == 'wwr':
            version = (settings or {}).get('version')
            if version:
                build = wwr_permalinks.build_name(version)
                return f'Paste this permalink into Wind Waker Randomizer build **{build}**. Other builds will reject it.'
            return 'Paste this permalink into the Wind Waker Randomizer.'
        if randomizer in local_rolls.FLAG_GAMES:
            return f'Enter this seed and these flags in the {local_rolls.FLAG_GAMES[randomizer]} Randomizer.'
        return None

    @staticmethod
    def describe_wwr_permalink(permalink: str) -> WWPermalinkInfo:
        """The build and headline settings of a pasted permalink, for the Presets tab."""
        parsed = wwr_permalinks.decode_permalink(permalink)
        return WWPermalinkInfo(
            build=parsed.build, version=parsed.version, summary=wwr_permalinks.summarize(permalink),
        )

    @staticmethod
    def describe_ff1r_link(url: str) -> str:
        """The site version an FFR flags link pins, for the Presets tab, or ``ValueError``."""
        host = urllib.parse.urlparse(local_rolls.ff1r_reference_url({'url': url})).hostname or ''
        prefix = host.lower()[:-len(local_rolls.FF1R_HOST)].rstrip('.')
        return prefix.replace('-', '.') if prefix and prefix != 'www' else 'latest'

    @staticmethod
    def example_flag_seed(randomizer: str, flags: str) -> str:
        """What a player would be handed for these flags, for the Presets tab, or ``ValueError``."""
        seed = random.randint(*local_rolls.FLAG_SEED_RANGES[randomizer])
        return f"{seed} - {local_rolls.flag_string(randomizer, {'flags': flags})}"

    @staticmethod
    def rolls_from_flags(randomizer: Optional[str]) -> bool:
        """Whether a roll is a seed number beside a flag string the player types in."""
        return randomizer in local_rolls.FLAG_GAMES

    @classmethod
    def supports_triforce_texts(cls, generator: Optional[str]) -> bool:
        return generator in cls.TRIFORCE_TEXT_RANDOMIZERS

    @classmethod
    def randomizer_name(cls, randomizer: str) -> str:
        return cls.RANDOMIZER_NAMES.get(randomizer, randomizer)

    @classmethod
    def offers_remote_presets(cls, randomizer: Optional[str]) -> bool:
        """Whether this randomizer's API publishes a preset catalogue."""
        return randomizer in cls.REMOTE_PRESET_BRANCHES

    @classmethod
    def remote_preset_branches(cls, randomizer: Optional[str]) -> List[str]:
        """The catalogue variants to choose between, empty where there is one list."""
        return list(cls.REMOTE_PRESET_BRANCHES.get(randomizer or '', ()))

    @classmethod
    def available_randomizers(cls, configured: Set[str]) -> List[str]:
        """The randomizers a tenant may *select*, given the credentials it has set.

        ``configured`` is ``RandomizerCredentialService.configured_randomizers()``.
        Drops any randomizer whose upstream needs a key the community has not
        supplied — validity is not availability: ``AVAILABLE_RANDOMIZERS`` stays
        the whole set (a stored ``dk64r`` preset is a valid row with or without a
        key, and each editor re-adds its own row's randomizer), while the selector
        surfaces offer only what this community can actually roll.
        """
        return [
            r for r in cls.AVAILABLE_RANDOMIZERS
            if not credentials_for(r) or r in configured
        ]

    async def _credential(self, randomizer: str, key: str) -> str:
        """This tenant's value for one randomizer credential.

        The successor to the process-wide ``*_API_KEY`` env vars: credentials are
        per tenant with no deployment-wide fallback, so a community that has not
        supplied one gets a clear error instead of silently rolling on someone
        else's key. Called from inside a generator, i.e. after the ``MOCK_SEEDGEN``
        short-circuit, so mock rolls still need no credentials.
        """
        from application.services.randomizer_credential_service import (
            RandomizerCredentialService,
        )

        value = await RandomizerCredentialService().resolve(randomizer, key)
        if not value:
            raise MissingCredentialError(
                f'{spec_for(randomizer, key).label} is not configured for this '
                f'community. Add it under Admin → Settings → Integrations.'
            )
        return value

    async def generate_seed(self, randomizer: str, preset: Optional[Preset] = None) -> str:
        """
        Generate a seed for the specified randomizer.

        Args:
            randomizer: Name of the randomizer (alttpr, ff1r, z1r, smmap, ootr, test)
            preset: Optional resolved ``Preset`` supplying the randomizer settings.
                Preset-aware backends (``PRESET_AWARE_RANDOMIZERS``: ALTTPR, DK64R, DASH, FFR, WWR, Z1R)
                use ``preset.settings`` when given and fall back to a committed
                default without one. Other backends are still hard-coded and
                ignore the preset.

        Returns:
            URL or string representing the generated seed

        Raises:
            ValueError: If randomizer is not supported, or a
                :class:`~application.utils.seed_provider.SeedProviderError`
                subclass naming what the upstream did.
        """
        call = await self.generate_seed_call(randomizer, preset)
        return call.value.url

    async def generate_seed_call(
        self,
        randomizer: str,
        preset: Optional[Preset] = None,
        *,
        surface: Optional[str] = None,
        triforce_text: Optional[str] = None,
    ) -> ProviderCall:
        """Roll a seed under the provider contract, returning it with its cost.

        The provenance-carrying entry point: the returned
        :class:`~application.utils.seed_provider.ProviderCall` holds the
        :class:`RolledSeed` (permalink + settings as sent) plus the attempt count
        and latency, which is what a ``GeneratedSeeds`` row records. Prefer this
        over :meth:`generate_seed` anywhere the roll is persisted.

        ``triforce_text`` is embedded as the end-game text by the randomizers in
        ``TRIFORCE_TEXT_RANDOMIZERS`` and ignored by the rest.
        """
        generator_map = {
            'alttpr': self._generate_alttpr,
            'avianart': self._generate_avianart,
            'ff1r': self._generate_ff1r,
            'z1r': self._generate_z1r,
            'z2r': self._generate_z2r,
            'smb3r': self._generate_smb3r,
            'aosr': self._generate_aosr,
            'sm': self._generate_sm,
            'smz3': self._generate_smz3,
            'smmap': self._generate_smmap,
            'ootr': self._generate_ootr,
            'smdash': self._generate_smdash,
            'smvaria': self._generate_smvaria,
            'dk64r': self._generate_dk64r,
            'wwr': self._generate_wwr,
            'test': self._generate_test,
        }

        if randomizer not in generator_map:
            raise ValueError(f"Unsupported randomizer: {randomizer}")

        # DK64R opts out of the instant short-circuit and mocks one layer down,
        # at its HTTP session: its roll is a minutes-long task queue, and a
        # permalink returned immediately never puts the presentation layer into
        # the waiting state that is the whole point of testing this backend.
        if is_mock_seedgen() and randomizer not in self.MOCK_EXEMPT_RANDOMIZERS:
            return ProviderCall(
                value=RolledSeed(
                    url=self._mock_seed_url(randomizer), code=seed_codes.mock_code(randomizer),
                ),
                provider=randomizer, operation='generate_seed', surface=surface,
            )

        generator = generator_map[randomizer]
        if randomizer in self.TRIFORCE_TEXT_RANDOMIZERS:
            def _run():
                return generator(preset, triforce_text=triforce_text)
        elif randomizer in self.PRESET_AWARE_RANDOMIZERS:
            def _run():
                return generator(preset)
        else:
            def _run():
                return generator()

        return await call_provider(
            _run,
            provider=randomizer,
            operation='generate_seed',
            surface=surface,
            attempt_timeout=self.PROVIDER_TIMEOUTS.get(randomizer, PROVIDER_TIMEOUT_SECONDS),
            max_attempts=self.PROVIDER_ATTEMPTS.get(randomizer, PROVIDER_MAX_ATTEMPTS),
        )

    @staticmethod
    def _mock_seed_url(randomizer: str) -> str:
        """A believable, unique permalink for MOCK_SEEDGEN mode."""
        return f"https://mock.seedgen.local/{randomizer}/{secrets.token_hex(8)}"

    async def _generate_alttpr(
        self,
        preset: Optional[Preset] = None,
        *,
        triforce_text: Optional[str] = None,
    ) -> RolledSeed:
        """
        Generate an A Link to the Past Randomizer seed.

        Uses ``preset.settings`` when a preset is supplied; otherwise falls back
        to the built-in ``casualboots`` settings (the historical default). A
        ``triforce_text`` becomes the end-game text of this roll only.
        """
        if preset is not None:
            settings = copy.deepcopy(preset.settings)
        else:
            settings = yaml.safe_load(
                await _read_bundled_preset("presets/alttpr/casualboots.yaml")
            )['settings']

        if triforce_text:
            settings.setdefault('texts', {})['end_triforce'] = "{NOBORDER}\n" + triforce_text

        try:
            seed = await ALTTPR.generate(
                settings=settings,
                endpoint='/api/customizer',
            )
        except Pyz3rException as e:
            # pyz3r has already retried five times, so this is terminal here
            # rather than another round of retries; only an outage gets here.
            raise SeedProviderBadResponse(
                "alttpr.com didn't make the seed. It may be down, so try again in a minute.",
                provider='alttpr', operation='generate_seed',
            ) from e
        return RolledSeed(
            url=seed.url, settings=settings, code=seed_codes.alttpr_code(seed.data.get('patch')),
        )

    async def _generate_ff1r(self, preset: Optional[Preset] = None) -> RolledSeed:
        """
        Generate a Final Fantasy 1 Randomizer seed.

        Purely local: the preset's flags link (``{"url": ...}``, copied from the
        site, whose host pins the version) keeps its flags and gets a fresh seed
        number; the site builds the game in the browser, so the link is the seed.
        There is no bundled fallback, so a tournament without a preset fails
        loudly instead of rolling some other season's flags.
        """
        try:
            if preset is None:
                raise ValueError('Final Fantasy needs a preset with a flags link. Add one on the Presets tab.')
            reference = local_rolls.ff1r_reference_url(preset.settings)
        except ValueError as e:
            raise SeedProviderInvalidRequest(str(e), provider='ff1r', operation='generate_seed') from None
        seed = ('%008x' % random.randrange(16 ** 8)).upper()
        up = urllib.parse.urlparse(reference)
        qs = urllib.parse.parse_qs(up.query)
        qs['s'] = [seed]
        newurl = urllib.parse.urlunparse((up.scheme, up.netloc, up.path, up.params,
                                          urllib.parse.urlencode(qs, doseq=True), up.fragment))
        return RolledSeed(url=newurl, settings={'seed': seed, 'flags': qs['f'][0]})

    async def _generate_z1r(self, preset: Optional[Preset] = None) -> RolledSeed:
        """Generate a Zelda 1 Randomizer seed: a "seed - flags" string, purely local."""
        return local_rolls.roll_flag_game('z1r', preset)

    async def _generate_z2r(self, preset: Optional[Preset] = None) -> RolledSeed:
        """Generate a Zelda 2 Randomizer seed: a "seed - flags" string, purely local."""
        return local_rolls.roll_flag_game('z2r', preset)

    async def _generate_smb3r(self, preset: Optional[Preset] = None) -> RolledSeed:
        """Generate a Super Mario Bros. 3 Randomizer seed: a "seed - flags" string, purely local."""
        return local_rolls.roll_flag_game('smb3r', preset)

    async def _generate_aosr(self, preset: Optional[Preset] = None) -> RolledSeed:
        """Generate an Aria of Sorrow Randomizer link, purely local."""
        return local_rolls.roll_aosr(preset)

    async def _generate_avianart(self, preset: Optional[Preset] = None) -> RolledSeed:
        """Generate an AVIANART seed of the preset's named AVIANART preset."""
        return await generate_avianart(preset.settings if preset is not None else None)

    async def _generate_sm(self, preset: Optional[Preset] = None) -> RolledSeed:
        """Generate a Super Metroid Randomizer seed on sm.samus.link."""
        return await generate_samus('sm', preset.settings if preset is not None else None)

    async def _generate_smvaria(self, preset: Optional[Preset] = None) -> RolledSeed:
        """Generate a Super Metroid VARIA Randomizer race seed."""
        return await generate_varia(preset.settings if preset is not None else None)

    async def _generate_smz3(self, preset: Optional[Preset] = None) -> RolledSeed:
        """Generate an SMZ3 Combo Randomizer seed on samus.link."""
        return await generate_samus('smz3', preset.settings if preset is not None else None)

    async def _generate_smmap(self) -> RolledSeed:
        """
        Generate a Super Metroid Map Randomizer seed.
        """
        # Never fall back to a committed default — a leaked spoiler token
        # unlocks spoiler logs for race seeds.
        spoiler_token = await self._credential('smmap', 'spoiler_token')
        settings = await _read_bundled_preset("presets/smmap/community_race_s5.json")

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
            with aiohttp.MultipartWriter('form-data') as mpwriter:
                mpwriter.append(spoiler_token).set_content_disposition('form-data', name='spoiler_token')
                mpwriter.append(settings).set_content_disposition('form-data', name='settings')
            async with session.post('https://maprando.com/randomize', data=mpwriter) as resp:
                # Read the status before the body: an error page is HTML, and
                # parsing it as JSON would report a parse failure instead of the
                # 503 that caused it.
                await raise_for_status(resp, provider='smmap', operation='generate_seed')
                data = await resp.json()

        seed_url = (data or {}).get('seed_url')
        if not seed_url:
            raise SeedProviderBadResponse(
                'Map Rando returned no seed URL.',
                provider='smmap', operation='generate_seed',
            )
        # The spoiler token is a credential and is deliberately not part of the
        # snapshot — settings only.
        return RolledSeed(
            url=f"https://maprando.com{seed_url}", settings=json.loads(settings),
            code=seed_codes.smmap_code(data.get('seed_hash')),
        )

    async def _generate_ootr(self) -> RolledSeed:
        """
        Generate an Ocarina of Time Randomizer seed.
        """
        settings = json.loads(await _read_bundled_preset("presets/ootr/sgl2026.json"))

        # The OOTR API authenticates via a ``key`` query parameter; guard
        # against silently sending key=None when it is not configured.
        api_key = await self._credential('ootr', 'api_key')

        async with aiohttp.request(
            method='post',
            url="https://ootrandomizer.com/api/sglive/seed/create",
            json=settings,
            params={
                "key": api_key,
                "version": "dev_9.1.38-0",
                "encrypt": "true"
            }
        ) as resp:
            await raise_for_status(resp, provider='ootr', operation='generate_seed')
            result = await resp.json()

        seed_id = (result or {}).get('id')
        if not seed_id:
            raise SeedProviderBadResponse(
                'OoT Randomizer returned no seed id.',
                provider='ootr', operation='generate_seed',
            )
        return RolledSeed(
            url=f"https://ootrandomizer.com/seed/get?id={seed_id}", settings=settings,
            code=await seed_codes.fetch_ootr_code(seed_id, api_key),
        )
    
    async def _generate_smdash(self, preset: Optional[Preset] = None) -> RolledSeed:
        """Generate a Super Metroid: DASH seed.

        dashrando.net rolls one of its own named presets server-side: ``GET
        /generate/<tag>?race=1`` saves the seed and answers with a redirect to
        its ``/seed/<key>`` page, where players patch their own ROM. A preset's
        settings are ``{"preset": "<tag>"}``, the tag being one upstream defines
        in ``packages/core/lib/presets.ts``; without one, ``presets/smdash/sgl26.json``.
        """
        if preset is not None:
            settings = copy.deepcopy(preset.settings)
        else:
            settings = json.loads(await _read_bundled_preset("presets/smdash/sgl26.json"))

        tag = settings.get('preset') if isinstance(settings, dict) else None
        if not isinstance(tag, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', tag):
            raise ValueError(
                'A DASH preset needs a "preset" key naming one of dashrando.net\'s presets, e.g. "sgl26".'
            )

        # race=1 makes the seed page hand out a protected ROM; leaving spoiler
        # unset means upstream never generates a spoiler log for this seed.
        async with aiohttp.request(
            method='get',
            url=f"{_DASH_BASE_URL}/generate/{tag}",
            params={'race': '1'},
            allow_redirects=False,
        ) as resp:
            await raise_for_status(resp, provider='smdash', operation='generate_seed')
            location = resp.headers.get('Location') if resp.status in (301, 302, 303, 307, 308) else None

        seed_url = urllib.parse.urljoin(_DASH_BASE_URL + '/', location) if location else ''
        parsed = urllib.parse.urlparse(seed_url)
        if (
            parsed.scheme != 'https'
            or parsed.hostname not in ('www.dashrando.net', 'dashrando.net')
            or not re.fullmatch(r'/seed/[A-Za-z0-9_-]+', parsed.path)
        ):
            raise SeedProviderBadResponse(
                'DASH did not redirect to a seed page.',
                provider='smdash', operation='generate_seed',
            )
        return RolledSeed(
            url=seed_url, settings={'preset': tag, 'race': True},
            code=await seed_codes.fetch_dash_code(seed_url),
        )

    async def _generate_dk64r(self, preset: Optional[Preset] = None) -> RolledSeed:
        """Generate a Donkey Kong 64 Randomizer seed via the api.dk64rando.com queue.

        The upstream is a task queue: convert settings (if needed), submit the
        task, poll until it finishes, and return a shareable dk64randomizer.com
        permalink. The API is key-gated: the community's own key is sent as the
        ``X-API-Key`` header on every call.

        This is the **blocking** shape — a seed back, like every other backend.
        The persisted path (:class:`ProviderTask`, driven by ``seed_roll_worker``)
        uses :meth:`submit_async_roll` / :meth:`poll_async_roll` instead, so a
        roll survives a restart. Both share every HTTP call below.
        """
        branch, settings, settings_string = await self._dk64r_prepare(preset)
        params = {'branch': branch}

        async with await self._dk64r_session() as session:
            settings_dict = await self._dk64r_resolve_settings(
                session, settings, settings_string, params,
            )
            task_id = await self._dk64r_submit(session, settings_dict, params)
            result = await self._dk64r_poll(session, task_id, params)

        return self._dk64r_seed(result, branch, {'branch': branch, **settings_dict})

    # ------------------------------------------------------------------
    # Asynchronous (task-queue) rolls — the persisted path
    # ------------------------------------------------------------------

    async def submit_async_roll(
        self, randomizer: str, preset: Optional[Preset] = None,
    ) -> AsyncRollSubmission:
        """Hand a roll to a task-queue backend and return its upstream handle.

        Returns everything a *later* poll needs — possibly in another process
        after a restart — so no part of an in-flight roll lives only in this
        call's stack frame.
        """
        self._require_async(randomizer)
        branch, settings, settings_string = await self._dk64r_prepare(preset)
        params = {'branch': branch}

        async with await self._dk64r_session() as session:
            settings_dict = await self._dk64r_resolve_settings(
                session, settings, settings_string, params,
            )
            task_id = await self._dk64r_submit(session, settings_dict, params)

        return AsyncRollSubmission(
            provider_task_id=task_id,
            provider_params=params,
            settings={'branch': branch, **settings_dict},
        )

    async def poll_async_roll(
        self, randomizer: str, provider_task_id: str,
        provider_params: Optional[dict] = None, settings: Optional[dict] = None,
    ) -> AsyncRollPoll:
        """Check a submitted task **once**. Never sleeps; the caller owns cadence.

        ``AsyncRollPoll.seed`` is set once the task finished and ``None`` while
        it is still queued or running. A failure raises ``ValueError``, keeping
        "not done yet" and "will never be done" distinguishable to a caller that
        must persist them differently.
        """
        self._require_async(randomizer)
        params = provider_params or {}

        async with await self._dk64r_session() as session:
            result = await self._dk64r_poll_once(session, provider_task_id, params)

        if result is None:
            return AsyncRollPoll(seed=None)

        branch = str((settings or {}).get('branch') or params.get('branch') or 'stable')
        if branch not in DK64R_SITE_HOSTS:
            branch = 'stable'
        return AsyncRollPoll(
            seed=self._dk64r_seed(result, branch, settings),
            # The five-icon verification hash racers compare before starting.
            # The ~9 MB base64 ``patch`` beside it is deliberately dropped here:
            # nothing downstream reads it, and the permalink patches in-browser.
            verification_hash=result.get('hash'),
        )

    # ------------------------------------------------------------------
    # Upstream preset catalogues
    # ------------------------------------------------------------------

    async def list_remote_presets(
        self, randomizer: str, *, branch: Optional[str] = None,
    ) -> List[RemotePreset]:
        """The presets the randomizer's own API publishes, ready to store.

        The alternative to hand-authoring a settings blob: a community pulls the
        upstream's own presets — the ones its players already race — and saves
        the ones it wants as ``Preset`` rows.

        Read-only and idempotent, so it takes the *default* provider budget
        rather than this randomizer's roll overrides: DK64R's 11-minute ceiling
        and single attempt exist because re-running a roll enqueues a second
        generation, which fetching a catalogue does not.
        """
        catalogue_map = {
            'avianart': lambda _branch: avianart_presets(),
            'dk64r': self._dk64r_presets,
        }
        if randomizer not in catalogue_map:
            raise ValueError(f"'{randomizer}' does not publish a preset catalogue.")

        fetch = catalogue_map[randomizer]
        variants = self.remote_preset_branches(randomizer)
        chosen = branch or (variants[0] if variants else None)

        call = await call_provider(
            lambda: fetch(chosen),
            provider=randomizer,
            operation='list_presets',
            surface='preset_import',
        )
        return call.value

    def _require_async(self, randomizer: str) -> None:
        if randomizer not in self.ASYNC_RANDOMIZERS:
            raise ValueError(f"'{randomizer}' is not an asynchronous randomizer.")

    async def _generate_wwr(self, preset: Optional[Preset] = None) -> RolledSeed:
        """Generate a Wind Waker Randomizer permalink.

        Purely local: the preset's reference permalink (``{"permalink": ...}``,
        pasted from the randomizer) keeps its build and settings and gets a fresh
        seed name. Players paste the result into their own copy of that build.
        There is no bundled fallback, so a tournament that lost its preset fails
        loudly instead of rolling some other season's settings.
        """
        settings = preset.settings if preset is not None else None
        reference = settings.get('permalink') if isinstance(settings, dict) else None
        try:
            if not isinstance(reference, str) or not reference.strip():
                raise ValueError('Wind Waker needs a preset with a permalink. Paste one on the Presets tab.')
            version = wwr_permalinks.decode_permalink(reference).version
        except ValueError as e:
            # Raised as the envelope's "will never roll" error so the proctor
            # reads why, rather than the blanket "check the server logs".
            raise SeedProviderInvalidRequest(str(e), provider='wwr', operation='generate_seed') from None
        # The first roll for a build reads its word lists from disk.
        seed_name = await asyncio.to_thread(wwr_permalinks.make_seed_name, version=version)
        return RolledSeed(
            url=wwr_permalinks.reseed(reference, seed_name),
            settings={'reference_permalink': reference.strip(), 'version': version, 'seed_name': seed_name},
        )

    async def _generate_test(self) -> RolledSeed:
        """
        Generate a test seed (for testing purposes).

        The deliberate sleep exercises the UI's in-progress state; it is not a
        provider call and has no settings to snapshot.
        """
        await asyncio.sleep(5)  # Simulate processing time
        return RolledSeed(url="https://example.com/test-seed-url")
