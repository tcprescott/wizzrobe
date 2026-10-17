"""
Triforce Text Service - Business Logic Layer

Manages community-submitted texts that may be embedded in randomizer seeds.

Ported from sahasrahbot's ``alttprbot/util/triforce_text.py`` and
``alttprbot_api/blueprints/triforcetexts.py``. The submission/moderation
pool is scoped to a Tournament; moderators are the tournament's admins.
"""

import random
import re
from typing import List, Optional

from application.feature_flags import requires_feature
from application.repositories.triforce_text_repository import TriforceTextRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.feature_flag_service import FeatureFlagService
from application.services.seedgen_service import SeedGenerationService
from application.tenant_context import require_tenant_id
from models import FeatureFlag, Tournament, TriforceText, User

# Same character set as sahasrahbot: A-Z, a-z, 0-9, space, common punctuation,
# arrow glyphs, hiragana, and katakana. 19 chars max per line is an ALTTP
# end-game text engine limit.
TEXT_LINE_REGEX = re.compile(
    r"^[A-Za-z0-9 \?!,\-….~～''↑↓→←ぁ-ゟァ-ヿ]{0,19}$"
)
MAX_LINES = 3


class TriforceTextService:
    """Service for triforce text operations."""

    def __init__(self) -> None:
        self.repository = TriforceTextRepository()
        self.audit_service = AuditService()

    @staticmethod
    def _validate_lines(lines: List[str]) -> List[str]:
        if not isinstance(lines, list) or len(lines) != MAX_LINES:
            raise ValueError(f"Triforce text needs exactly {MAX_LINES} lines.")
        cleaned = [(line or '').rstrip('\r\n') for line in lines]
        for idx, line in enumerate(cleaned, start=1):
            if not TEXT_LINE_REGEX.match(line):
                raise ValueError(
                    f"Line {idx} contains invalid characters or exceeds 19 characters."
                )
        if not any(line.strip() for line in cleaned):
            raise ValueError("At least one line needs some text in it.")
        return cleaned

    @requires_feature(FeatureFlag.TRIFORCE_TEXTS)
    async def submit(
        self,
        tournament_id: int,
        lines: List[str],
        user: User,
    ) -> TriforceText:
        if user is None:
            raise ValueError("You must be logged in to submit a triforce text.")
        tournament = await Tournament.get_or_none(id=tournament_id, tenant_id=require_tenant_id())
        if tournament is None:
            raise ValueError("Tournament not found.")
        if not tournament.is_active or not SeedGenerationService.supports_triforce_texts(
            tournament.seed_generator
        ):
            raise ValueError("This tournament is not accepting submissions.")
        if not (await AuthService.is_staff(user) or await AuthService.is_triforce_submitter(user)):
            raise ValueError(
                "Triforce text submissions are a paid add-on for this event. "
                "Check the tournament page for how to unlock it."
            )

        cleaned = self._validate_lines(lines)
        text = "\n".join(cleaned)

        created = await self.repository.create(
            tournament=tournament,
            user=user,
            text=text,
            author=user.preferred_name,
        )
        await self.audit_service.write_log(
            user,
            AuditActions.TRIFORCE_TEXT_SUBMITTED,
            {
                'triforce_text_id': created.id,
                'tournament_id': tournament.id,
            },
        )
        return created

    @requires_feature(FeatureFlag.TRIFORCE_TEXTS)
    async def list_supporting_tournaments(self) -> List[Tournament]:
        """Active tournaments whose generator can embed triforce texts."""
        return await Tournament.filter(
            is_active=True,
            seed_generator__in=list(SeedGenerationService.TRIFORCE_TEXT_RANDOMIZERS),
            tenant_id=require_tenant_id(),
        ).order_by('name')

    @requires_feature(FeatureFlag.TRIFORCE_TEXTS)
    async def list_user_submissions(
        self, tournament_id: int, user: User
    ) -> List[TriforceText]:
        tournament = await Tournament.get_or_none(id=tournament_id, tenant_id=require_tenant_id())
        if tournament is None or user is None:
            return []
        return await self.repository.list_by_tournament_and_user(tournament, user)

    @requires_feature(FeatureFlag.TRIFORCE_TEXTS)
    async def list_for_moderation(
        self,
        tournament_id: int,
        status: Optional[str] = None,
        *,
        actor: Optional[User],
    ) -> List[TriforceText]:
        """List submissions for moderation: staff, or an admin of this tournament.

        ``status`` may be ``None`` (all), ``'pending'``, ``'approved'``, or
        ``'rejected'``.
        """
        if not (
            await AuthService.is_staff(actor)
            or await AuthService.is_tournament_admin(actor, tournament_id)
        ):
            raise PermissionError('You do not have permission to moderate this pool.')
        tournament = await Tournament.get_or_none(id=tournament_id, tenant_id=require_tenant_id())
        if tournament is None:
            return []
        return await self.repository.list_by_tournament(tournament, status=status)

    @requires_feature(FeatureFlag.TRIFORCE_TEXTS)
    async def moderate(
        self,
        text_id: int,
        approved: bool,
        actor: User,
    ) -> TriforceText:
        triforce_text = await self.repository.get_by_id(text_id)
        if triforce_text is None:
            raise ValueError("Triforce text not found.")

        is_staff = await AuthService.is_staff(actor)
        is_admin = await AuthService.is_tournament_admin(actor, triforce_text.tournament_id)
        if not (is_staff or is_admin):
            raise ValueError("You do not have permission to moderate this pool.")

        updated = await self.repository.set_moderation(triforce_text, approved, actor)
        await self.audit_service.write_log(
            actor,
            AuditActions.TRIFORCE_TEXT_APPROVED if approved else AuditActions.TRIFORCE_TEXT_REJECTED,
            {
                'triforce_text_id': updated.id,
                'tournament_id': updated.tournament_id,
            },
        )
        return updated

    @requires_feature(FeatureFlag.TRIFORCE_TEXTS)
    async def delete(self, text_id: int, actor: User) -> None:
        triforce_text = await self.repository.get_by_id(text_id)
        if triforce_text is None:
            raise ValueError("Triforce text not found.")

        is_staff = await AuthService.is_staff(actor)
        is_admin = await AuthService.is_tournament_admin(actor, triforce_text.tournament_id)
        if not (is_staff or is_admin):
            raise ValueError("You do not have permission to delete this submission.")

        details = {
            'triforce_text_id': triforce_text.id,
            'tournament_id': triforce_text.tournament_id,
        }
        await self.repository.delete(triforce_text)
        await self.audit_service.write_log(
            actor, AuditActions.TRIFORCE_TEXT_DELETED, details
        )

    async def get_balanced_text(self, tournament: Tournament) -> Optional[str]:
        """Pick a random user with approved texts, then a random text from that user.

        Ensures every submitter has equal weight regardless of how many texts
        they have approved. Texts whose submitter has been deleted (user FK
        nulled) form their own bucket so approved content remains usable.
        Returns None if no approved texts exist.

        Soft-gated: this runs inside every ALTTPR roll, so a community without the
        Triforce Texts feature must get a seed with no custom text — not a failed
        roll. The flag is still enforced here (nothing embeds a text where the
        feature is off), it just answers "no text" instead of raising the way the
        submission/moderation entry points do.
        """
        if tournament is None:
            return None
        if not await FeatureFlagService().is_enabled(FeatureFlag.TRIFORCE_TEXTS):
            return None
        buckets = await self.repository.list_approved_user_buckets(tournament)
        if not buckets:
            return None
        chosen_user_id = random.choice(buckets)
        texts = await self.repository.list_approved_by_user(tournament, chosen_user_id)
        if not texts:
            return None
        return random.choice(texts).text

    async def get_random_text(self, tournament: Tournament) -> Optional[str]:
        """Pick a uniformly random approved text. Returns None when none exist.

        Soft-gated for the same reason as :meth:`get_balanced_text`.
        """
        if tournament is None:
            return None
        if not await FeatureFlagService().is_enabled(FeatureFlag.TRIFORCE_TEXTS):
            return None
        texts = await self.repository.list_approved(tournament)
        if not texts:
            return None
        return random.choice(texts).text
