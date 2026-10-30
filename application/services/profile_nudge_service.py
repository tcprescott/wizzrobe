"""Profile Nudge Service — a community's recommended profile items.

STAFF pick which profile items their community wants every member to have
(Challonge linked, pronouns set, ...). A member missing one sees a banner on
every page until they either complete it or, where the rule allows, dismiss it.

Storage is split by ownership:

* **Rules** are community config, in ``Tenant.config['profile_nudges']`` beside
  the timezone policy: ``{'rules': [{'check', 'dismissible', 'note'}]}``.
* **Dismissals** are one :class:`ProfileNudgeDismissal` row per member per check
  per community.

A check only applies where the thing it asks for can actually be done here: the
Challonge rule needs the Challonge feature on and the integration configured,
the timezone rule needs the community to follow each member's clock. A rule
whose check stops applying goes quiet rather than sending members to a control
that isn't there.

The banner is evaluated on every framed page build, so :meth:`pending_for` costs
one tenant read when no rules are set and at most two more queries otherwise.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Iterable, Mapping, Optional

from application.errors import require_found
from application.repositories import ProfileNudgeRepository
from application.repositories.tenant_repository import TenantRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.tenant_context import get_current_tenant_id, require_tenant_id
from models import FeatureFlag, User

CONFIG_KEY = 'profile_nudges'
MAX_NOTE_LENGTH = 200


class ProfileCheck(str, Enum):
    CHALLONGE = 'challonge'
    TWITCH = 'twitch'
    RACETIME = 'racetime'
    PRONOUNS = 'pronouns'
    TIMEZONE = 'timezone'
    MATCHERINO = 'matcherino'


class ProfileSection(str, Enum):
    """Where on the Profile tab each check is fixed; the banner deep-links here."""

    PERSONAL = 'personal'
    CONNECTED_ACCOUNTS = 'connected-accounts'
    MATCHERINO = 'matcherino'
    NOTIFICATIONS = 'notifications'


@dataclass(frozen=True)
class ProfileCheckSpec:
    check: ProfileCheck
    label: str
    prompt: str
    action_label: str
    section: ProfileSection
    is_met: Callable[[User], bool]


@dataclass(frozen=True)
class ProfileNudge:
    """One unmet rule, ready for the banner."""

    check: ProfileCheck
    prompt: str
    action_label: str
    section: ProfileSection
    dismissible: bool
    note: Optional[str] = None


CHECKS: tuple[ProfileCheckSpec, ...] = (
    ProfileCheckSpec(
        ProfileCheck.CHALLONGE, 'Challonge account linked',
        "Link your Challonge account so your bracket matches show up here and you can schedule them.",
        'Link Challonge', ProfileSection.CONNECTED_ACCOUNTS,
        lambda u: bool(u.challonge_user_id),
    ),
    ProfileCheckSpec(
        ProfileCheck.TWITCH, 'Twitch account linked',
        'Link your Twitch account so we know which channel is yours.',
        'Link Twitch', ProfileSection.CONNECTED_ACCOUNTS,
        lambda u: bool(u.twitch_user_id),
    ),
    ProfileCheckSpec(
        ProfileCheck.RACETIME, 'racetime.gg account linked',
        'Link your racetime.gg account so your race results are credited to you.',
        'Link racetime.gg', ProfileSection.CONNECTED_ACCOUNTS,
        lambda u: bool(u.racetime_user_id),
    ),
    ProfileCheckSpec(
        ProfileCheck.PRONOUNS, 'Pronouns set',
        "Add your pronouns so commentators and crew get them right.",
        'Add pronouns', ProfileSection.PERSONAL,
        lambda u: bool((u.pronouns or '').strip()),
    ),
    ProfileCheckSpec(
        ProfileCheck.TIMEZONE, 'Timezone chosen',
        "Pick your timezone so match times aren't guessed from whichever device you're on.",
        'Pick timezone', ProfileSection.PERSONAL,
        lambda u: bool(u.timezone),
    ),
    ProfileCheckSpec(
        ProfileCheck.MATCHERINO, 'Matcherino handle entered',
        'Add your Matcherino handle so prize money can reach you.',
        'Add handle', ProfileSection.MATCHERINO,
        lambda u: bool((u.matcherino_username or '').strip()),
    ),
)

_SPEC_BY_CHECK: dict[ProfileCheck, ProfileCheckSpec] = {s.check: s for s in CHECKS}


def _parse_check(value: object) -> Optional[ProfileCheck]:
    try:
        return ProfileCheck(value)
    except ValueError:
        return None


class ProfileNudgeService:
    """Read and write a community's profile rules, and work out who owes what."""

    def __init__(self) -> None:
        self.repository = ProfileNudgeRepository()
        self.audit_service = AuditService()

    @staticmethod
    def _clean_rules(stored: object) -> list[dict]:
        """Stored config → ``[{check, dismissible, note}]`` in catalogue order."""
        raw = stored.get('rules') if isinstance(stored, Mapping) else None
        by_check: dict[ProfileCheck, dict] = {}
        for entry in raw if isinstance(raw, list) else []:
            if not isinstance(entry, Mapping):
                continue
            check = _parse_check(entry.get('check'))
            if check is None or check in by_check:
                continue
            note = entry.get('note')
            note = note.strip()[:MAX_NOTE_LENGTH] if isinstance(note, str) else ''
            by_check[check] = {
                'check': check,
                'dismissible': entry.get('dismissible', True) is not False,
                'note': note or None,
            }
        return [by_check[s.check] for s in CHECKS if s.check in by_check]

    async def get_rules(self, tenant_id: Optional[int] = None) -> list[dict]:
        """The community's configured rules. Never raises on a missing tenant."""
        tenant_id = tenant_id if tenant_id is not None else get_current_tenant_id()
        if tenant_id is None:
            return []
        tenant = await TenantRepository.get_by_id(tenant_id)
        return self._clean_rules((tenant.config or {}).get(CONFIG_KEY) if tenant else None)

    async def unavailable_reasons(self) -> dict[ProfileCheck, str]:
        """Why each check a member couldn't complete here right now is out.

        A check missing from the result applies. Staff see these on the admin
        tab; members never see a rule whose check is listed.
        """
        from application.services.challonge_service import ChallongeService
        from application.services.feature_flag_service import FeatureFlagService
        from application.services.racetime_service import RacetimeService
        from application.services.timezone_service import MODE_USER, TimezoneService
        from application.services.twitch_service import TwitchService

        live = await FeatureFlagService().enabled_flags()
        reasons: dict[ProfileCheck, str] = {}
        if FeatureFlag.CHALLONGE not in live:
            reasons[ProfileCheck.CHALLONGE] = 'Challonge is turned off for this community.'
        elif not ChallongeService.is_configured():
            reasons[ProfileCheck.CHALLONGE] = "Challonge linking isn't set up on this server."
        if not TwitchService.is_configured():
            reasons[ProfileCheck.TWITCH] = "Twitch linking isn't set up on this server."
        if not RacetimeService.is_configured():
            reasons[ProfileCheck.RACETIME] = "racetime.gg linking isn't set up on this server."
        if (await TimezoneService.get_settings())['mode'] != MODE_USER:
            reasons[ProfileCheck.TIMEZONE] = (
                'This community shows everyone one timezone (Admin → Settings → General), '
                "so members don't pick their own."
            )
        if FeatureFlag.PAYOUTS not in live:
            reasons[ProfileCheck.MATCHERINO] = (
                "Payouts are turned off for this community, so profiles don't ask for a handle."
            )
        return reasons

    async def set_rules(self, actor: User, rules: Iterable[Mapping]) -> list[dict]:
        """Replace the community's rules (STAFF only).

        ``rules`` is ``[{check, dismissible, note}]``; a check missing from it is
        switched off. Dismissals are kept, so re-enabling a rule does not
        re-nag members who already acknowledged it.
        """
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            'Only Staff can change recommended profile items.',
        )
        rules = list(rules)
        for entry in rules:
            if _parse_check(entry.get('check')) is None:
                raise ValueError(f'"{entry.get("check")}" is not a profile item Wizzrobe knows about.')
            note = entry.get('note')
            if isinstance(note, str) and len(note.strip()) > MAX_NOTE_LENGTH:
                raise ValueError(f'Keep each note under {MAX_NOTE_LENGTH} characters.')
        cleaned = self._clean_rules({'rules': rules})

        tenant_id = require_tenant_id()
        tenant = require_found(await TenantRepository.get_by_id(tenant_id), 'Tenant')
        stored = [
            {'check': r['check'].value, 'dismissible': r['dismissible'], 'note': r['note']}
            for r in cleaned
        ]
        config = dict(tenant.config or {})
        config[CONFIG_KEY] = {'rules': stored}
        await TenantRepository.update(tenant, config=config)

        await self.audit_service.write_log(
            actor, AuditActions.PROFILE_NUDGES_UPDATED,
            {'tenant_id': tenant_id, 'rules': stored},
        )
        return cleaned

    async def pending_for(self, user: Optional[User]) -> list[ProfileNudge]:
        """The rules ``user`` has neither completed nor dismissed, in catalogue order.

        The caller decides whether the viewer is a member; this only answers for
        the community in scope.
        """
        if user is None or get_current_tenant_id() is None:
            return []
        rules = await self.get_rules()
        if not rules:
            return []
        unmet = [r for r in rules if not _SPEC_BY_CHECK[r['check']].is_met(user)]
        if not unmet:
            return []
        unavailable = await self.unavailable_reasons()
        unmet = [r for r in unmet if r['check'] not in unavailable]
        if not unmet:
            return []
        dismissed = await self.repository.dismissed_checks(user.id)
        nudges = []
        for rule in unmet:
            if rule['dismissible'] and rule['check'].value in dismissed:
                continue
            spec = _SPEC_BY_CHECK[rule['check']]
            nudges.append(ProfileNudge(
                check=spec.check, prompt=spec.prompt, action_label=spec.action_label,
                section=spec.section, dismissible=rule['dismissible'], note=rule['note'],
            ))
        return nudges

    async def dismiss(self, actor: User, check: str) -> None:
        """Acknowledge one rule for ``actor`` in this community, hiding its banner."""
        parsed = _parse_check(check)
        rule = next((r for r in await self.get_rules() if r['check'] == parsed), None)
        if rule is None:
            raise ValueError("That item isn't on this community's list any more.")
        if not rule['dismissible']:
            raise ValueError(
                "This community needs this one done, so it can't be dismissed. "
                'Use the button beside it to finish it.'
            )
        if await self.repository.dismiss(actor.id, rule['check'].value):
            await self.audit_service.write_log(
                actor, AuditActions.PROFILE_NUDGE_DISMISSED,
                {'tenant_id': require_tenant_id(), 'check': rule['check'].value},
            )

    @staticmethod
    def spec(check: ProfileCheck) -> ProfileCheckSpec:
        return _SPEC_BY_CHECK[check]
