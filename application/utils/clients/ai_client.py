"""Ask an AI provider for one JSON answer, whichever provider a community uses.

Raw HTTP over ``httpx`` rather than a vendor SDK: there are two wire shapes to
speak (Anthropic's Messages API and the OpenAI chat-completions shape most
other providers and gateways copy) and one small call each. :func:`complete_json`
is the provider interface; adding a provider is one more ``_call_*`` function
and a branch in it.

The community's key arrives per call and is never logged. Errors come back as
:class:`AiProviderError` with a message staff can act on ("the key was
refused", "the model name wasn't found"), never the provider's raw body.

``MOCK_AI`` (or any key starting ``mock-`` outside production) answers from
:mod:`application.utils.mocks.mock_ai` so the dev seed and browser checks
run offline.
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

from application.utils.environment import env_flag, is_production
from application.utils.ssrf import pin_public_url
from models import AiProvider

logger = logging.getLogger(__name__)

ANTHROPIC_URL = 'https://api.anthropic.com/v1/messages'
ANTHROPIC_VERSION = '2026-10-21'
DEFAULT_MODELS: Dict[AiProvider, str] = {
    AiProvider.ANTHROPIC: 'claude-opus-5-5',
    AiProvider.OPENAI_COMPATIBLE: '',
}
PROVIDER_LABELS: Dict[AiProvider, str] = {
    AiProvider.ANTHROPIC: 'Claude (Anthropic)',
    AiProvider.OPENAI_COMPATIBLE: 'OpenAI-compatible',
}
MAX_TOKENS = 4000
_TIMEOUT = httpx.Timeout(120.0, connect=10.0)


class AiProviderError(Exception):
    """A call that didn't produce an answer, worded for the staff member who pressed the button."""


@dataclass
class AiAnswer:
    data: Dict[str, Any]
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None


def is_mock(api_key: str) -> bool:
    if is_production():
        return False
    return env_flag('MOCK_AI') or api_key.startswith('mock-')


def _parse_json(text: str) -> Dict[str, Any]:
    cleaned = re.sub(r'^```(?:json)?\s*|\s*```$', '', (text or '').strip())
    start, end = cleaned.find('{'), cleaned.rfind('}')
    try:
        data = json.loads(cleaned[start:end + 1] if start >= 0 else cleaned)
    except ValueError:
        raise AiProviderError("The AI's answer wasn't in the expected shape. Try again.") from None
    if not isinstance(data, dict):
        raise AiProviderError("The AI's answer wasn't in the expected shape. Try again.")
    return data


def _status_error(status: int) -> AiProviderError:
    if status in (401, 403):
        return AiProviderError('The provider refused the API key. Check it under Settings → Integrations → AI provider.')
    if status == 404:
        return AiProviderError("The provider doesn't know that model or address. Check them under Settings → Integrations.")
    if status == 429:
        return AiProviderError('The provider says this key is over its rate limit or out of credit. Try again later.')
    if status == 400:
        return AiProviderError('The provider turned the request down. Check the model name under Settings → Integrations.')
    return AiProviderError(f'The provider had a problem (HTTP {status}). Try again in a minute.')


async def _post(url: str, *, headers: Dict[str, str], body: Dict[str, Any],
                public_only: bool = False) -> Dict[str, Any]:
    extensions: Dict[str, str] = {}
    if public_only and is_production():
        try:
            url, extra, extensions = await pin_public_url(url, subject='The provider address')
        except ValueError as exc:
            raise AiProviderError(str(exc)) from None
        headers = {**headers, **extra}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(url, json=body, headers=headers, extensions=extensions)
    except httpx.TimeoutException:
        raise AiProviderError('The provider took too long to answer. Try again.') from None
    except httpx.HTTPError as exc:
        logger.warning('AI provider request failed: %s', type(exc).__name__)
        raise AiProviderError("Couldn't reach the provider. Try again in a minute.") from None
    if response.status_code >= 400:
        logger.info('AI provider answered HTTP %s', response.status_code)
        raise _status_error(response.status_code)
    try:
        payload = response.json()
    except ValueError:
        raise AiProviderError("The provider's answer couldn't be read. Try again.") from None
    return payload if isinstance(payload, dict) else {}


async def _call_anthropic(api_key: str, model: str, system: str, prompt: str,
                          schema: Dict[str, Any]) -> AiAnswer:
    payload = await _post(ANTHROPIC_URL, headers={
        'x-api-key': api_key,
        'anthropic-version': ANTHROPIC_VERSION,
        'content-type': 'application/json',
    }, body={
        'model': model,
        'max_tokens': MAX_TOKENS,
        'system': system,
        'messages': [{'role': 'user', 'content': prompt}],
        'output_config': {'effort': 'low', 'format': {'type': 'json_schema', 'schema': schema}},
    })
    if payload.get('stop_reason') == 'refusal':
        raise AiProviderError('The AI declined to answer this one. Try rewording the section title.')
    text = ''.join(b.get('text', '') for b in payload.get('content') or [] if b.get('type') == 'text')
    usage = payload.get('usage') or {}
    return AiAnswer(_parse_json(text), usage.get('input_tokens'), usage.get('output_tokens'))


async def _call_openai_compatible(api_key: str, model: str, base_url: str, system: str, prompt: str,
                                  schema: Dict[str, Any]) -> AiAnswer:
    payload = await _post(f"{base_url.rstrip('/')}/chat/completions", headers={
        'Authorization': f'Bearer {api_key}',
        'content-type': 'application/json',
    }, body={
        'model': model,
        'max_tokens': MAX_TOKENS,
        'response_format': {'type': 'json_object'},
        'messages': [
            {'role': 'system', 'content': f'{system}\n\nAnswer with one JSON object matching this schema:\n'
                                          f'{json.dumps(schema)}'},
            {'role': 'user', 'content': prompt},
        ],
    }, public_only=True)
    choices = payload.get('choices') or [{}]
    text = ((choices[0] or {}).get('message') or {}).get('content') or ''
    usage = payload.get('usage') or {}
    return AiAnswer(_parse_json(text), usage.get('prompt_tokens'), usage.get('completion_tokens'))


async def complete_json(provider: AiProvider, *, api_key: str, model: str, base_url: Optional[str],
                        system: str, prompt: str, schema: Dict[str, Any]) -> AiAnswer:
    """One request, one JSON object back. Raises :class:`AiProviderError`."""
    if is_mock(api_key):
        from application.utils.mocks.mock_ai import mock_answer
        return mock_answer(prompt)
    if provider is AiProvider.ANTHROPIC:
        return await _call_anthropic(api_key, model, system, prompt, schema)
    if not base_url:
        raise AiProviderError('Set the provider address under Settings → Integrations first.')
    return await _call_openai_compatible(api_key, model, base_url, system, prompt, schema)
