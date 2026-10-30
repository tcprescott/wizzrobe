"""Randomizer credential registry — which secret each randomizer's upstream needs.

A randomizer whose upstream API is key-gated cannot be rolled until the community
supplies **its own** credential (stored per tenant as a ``RandomizerCredential``
row). This module is the single declaration of which credentials exist, so the
admin surface, the "can this community select this randomizer?" check, and the
roll-time resolution all read one list instead of a chain of ``if``s.

Pure metadata — a peer of ``application/feature_flags``: it imports nothing from
``application.services`` or ``models``, so every layer may import it freely.

**Adding a credential:** add a :class:`CredentialSpec` under its randomizer, then
read it in the generator with ``SeedGenerationService._credential(randomizer, key)``.
``key`` is the stable value persisted on ``RandomizerCredential.key``, so renaming
one is a data migration.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class CredentialSpec:
    randomizer: str
    key: str
    label: str
    help_text: str


RANDOMIZER_CREDENTIALS: Dict[str, Tuple[CredentialSpec, ...]] = {
    'ootr': (
        CredentialSpec(
            'ootr', 'api_key', 'OoT Randomizer API key',
            'Sent as the "key" parameter to ootrandomizer.com. Request one from '
            'the Ocarina of Time Randomizer team.',
        ),
    ),
    'smmap': (
        CredentialSpec(
            'smmap', 'spoiler_token', 'Map Rando spoiler token',
            'Sent to maprando.com with each roll; it unlocks the spoiler log for '
            'race seeds, so treat it as a secret. Issued by the Map Rando team.',
        ),
    ),
    'dk64r': (
        CredentialSpec(
            'dk64r', 'api_key', 'DK64 Randomizer API key',
            'Sent as the X-API-Key header to api.dk64rando.com. Issued by the '
            'DK64 Randomizer team, whose usage terms apply to your community.',
        ),
    ),
}


def credentials_for(randomizer: Optional[str]) -> Tuple[CredentialSpec, ...]:
    """The credentials ``randomizer`` needs; empty for a credential-free one."""
    return RANDOMIZER_CREDENTIALS.get(randomizer or '', ())


def spec_for(randomizer: str, key: str) -> CredentialSpec:
    """The spec for one credential. Raises ``ValueError`` when it is not declared."""
    for spec in credentials_for(randomizer):
        if spec.key == key:
            return spec
    raise ValueError(f"Unknown randomizer credential '{randomizer}.{key}'")


def all_specs() -> List[CredentialSpec]:
    """Every declared credential, in declaration order (drives UI ordering)."""
    return [spec for specs in RANDOMIZER_CREDENTIALS.values() for spec in specs]
