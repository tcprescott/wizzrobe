"""
AI Provider Service - Business Logic Layer

A community's own AI provider: which API, which model, its key, and how many
requests a day it may make. There is no platform key (Thomas, 2026-10-27):
a community that wants AI help brings its own account, so its usage and
bill are its own. Today the only use is drafting survey questions
(:mod:`application.services.survey.suggestions`).

**The key never leaves this module except to make a call.** It is sealed at
rest (:mod:`application.utils.secret_box`, bound to the tenant id), ``status``
says only whether one is set, audit entries name the provider and model and
nothing of the key, and :meth:`resolve` is the single unsealed read.
Staff only.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional

from application.repositories import AiProviderRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.tenant_context import require_tenant_id
from application.utils import secret_box
from application.utils.clients.ai_client import DEFAULT_MODELS, PROVIDER_LABELS
from application.utils.environment import is_production
from application.utils.safe_url import normalize_http_url
from models import AiProvider, User

_PURPOSE = 'ai-provider-key'
MODEL_MAX = 120
KEY_MAX = 512
LIMIT_MIN, LIMIT_MAX, LIMIT_DEFAULT = 1, 200, 20


@dataclass
class ResolvedProvider:
    provider: AiProvider
    model: str
    base_url: Optional[str]
    api_key: str
    daily_limit: int

    @property
    def label(self) -> str:
        return PROVIDER_LABELS[self.provider]


class AiProviderService:
    """The current community's AI provider: status, save, clear, and the one unsealed read."""

    def __init__(self) -> None:
        self.repository = AiProviderRepository()
        self.audit_service = AuditService()

    @staticmethod
    async def _ensure_staff(actor: User) -> None:
        await AuthService.ensure(await AuthService.is_staff(actor), 'Only Staff can change the AI provider.')

    @staticmethod
    def _context() -> str:
        return f'tenant:{require_tenant_id()}'

    async def status(self, actor: User) -> Dict[str, Any]:
        """What the settings card shows. Never the key."""
        await self._ensure_staff(actor)
        row = await self.repository.get_current()
        if row is None:
            return {'configured': False, 'provider': AiProvider.ANTHROPIC.value,
                    'model': DEFAULT_MODELS[AiProvider.ANTHROPIC], 'base_url': None,
                    'daily_limit': LIMIT_DEFAULT, 'key_readable': False, 'updated_at': None}
        return {
            'configured': True,
            'provider': row.provider.value,
            'model': row.model,
            'base_url': row.base_url,
            'daily_limit': row.daily_limit,
            'key_readable': secret_box.open_(row.key_ciphertext, purpose=_PURPOSE, context=self._context()) is not None,
            'updated_at': row.updated_at,
        }

    async def save(self, actor: User, *, provider: str, model: str, base_url: Optional[str],
                   api_key: Optional[str], daily_limit: int) -> None:
        """Set the provider. A blank ``api_key`` keeps the stored one (there must be one)."""
        await self._ensure_staff(actor)
        try:
            chosen = AiProvider(provider)
        except ValueError:
            raise ValueError('Pick a provider from the list.') from None
        model = (model or '').strip()
        if not model or len(model) > MODEL_MAX:
            raise ValueError(f'Give the model name (up to {MODEL_MAX} characters).')
        url: Optional[str] = None
        if chosen is AiProvider.OPENAI_COMPATIBLE:
            url = normalize_http_url(base_url, 'The provider address')
            if url is None:
                raise ValueError('Give the provider address, like https://api.openai.com/v1.')
            if is_production() and not url.lower().startswith('https://'):
                raise ValueError('The provider address must start with https://.')
        try:
            limit = int(daily_limit)
        except (TypeError, ValueError):
            raise ValueError('The daily limit is a whole number.') from None
        if not LIMIT_MIN <= limit <= LIMIT_MAX:
            raise ValueError(f'The daily limit is between {LIMIT_MIN} and {LIMIT_MAX} requests.')
        key = (api_key or '').strip()
        if len(key) > KEY_MAX:
            raise ValueError("That doesn't look like an API key; it's too long.")
        current = await self.repository.get_current()
        fields: Dict[str, Any] = {'provider': chosen, 'model': model, 'base_url': url, 'daily_limit': limit,
                                  'updated_by_id': actor.id}
        if key:
            fields['key_ciphertext'] = secret_box.seal(key, purpose=_PURPOSE, context=self._context())
        elif current is None or secret_box.open_(current.key_ciphertext, purpose=_PURPOSE,
                                                 context=self._context()) is None:
            raise ValueError('Paste the API key.')
        await self.repository.upsert(**fields)
        await self.audit_service.write_log(
            actor, AuditActions.AI_PROVIDER_SET,
            # Names the provider and model, never the key or any part of it.
            {'provider': chosen.value, 'model': model, 'base_url': url, 'daily_limit': limit,
             'key_replaced': bool(key)},
        )

    async def clear(self, actor: User) -> None:
        """Forget the provider and key. Idempotent."""
        await self._ensure_staff(actor)
        if await self.repository.delete_current():
            await self.audit_service.write_log(actor, AuditActions.AI_PROVIDER_CLEARED, {})

    async def is_configured(self) -> Optional[str]:
        """The provider's label when one is set up with a readable key, else ``None``. Ungated: says nothing secret."""
        resolved = await self.resolve()
        return resolved.label if resolved else None

    async def resolve(self) -> Optional[ResolvedProvider]:
        """The provider with its key unsealed.

        The one path that reads the key, for the suggestion service to make a
        call with. Never call it from a page, router, or anything that renders.
        """
        row = await self.repository.get_current()
        if row is None:
            return None
        key = secret_box.open_(row.key_ciphertext, purpose=_PURPOSE, context=self._context())
        if key is None:
            return None
        return ResolvedProvider(row.provider, row.model, row.base_url, key, row.daily_limit)
