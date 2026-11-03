"""Shared validator for schema-checked JSON config blobs.

Several aggregates store a closed, ``extra='forbid'`` pydantic model as a JSON
blob (``Tournament.config``, ``AsyncQualifier.config``) and validate it before
persisting. This module owns the single validate-or-normalize routine they share
so the ``ValueError`` surface and the "drop unset keys" normalization stay
identical across every config facet.
"""

from typing import Any, Dict, Optional, Type

from pydantic import BaseModel, ValidationError


def validate_config_blob(
    config: Optional[Dict[str, Any]],
    model_cls: Type[BaseModel],
    label: str,
) -> Optional[Dict[str, Any]]:
    """Validate/normalize a config blob against ``model_cls``.

    ``None`` passes through unchanged (config is optional). Otherwise the dict is
    validated against ``model_cls`` and returned normalized with unset keys
    dropped. ``label`` names the config in the two user-facing errors — an
    ``"<label> config must be an object"`` type error and an ``"Invalid <label>
    config: ..."`` validation error — so the service layer surfaces them the same
    as every other user error.
    """
    if config is None:
        return None
    if not isinstance(config, dict):
        raise ValueError(f"{label} config must be an object")
    try:
        model = model_cls.model_validate(config)
    except ValidationError as exc:
        raise ValueError(f"Invalid {label} config: {_readable(exc)}") from exc
    return model.model_dump(exclude_none=True)


def _readable(exc: ValidationError) -> str:
    """One short sentence per bad field, in place of pydantic's own rendering.

    ``str(ValidationError)`` is four lines per error including a docs URL and the
    input repr — 268 characters for a single even best-of, into a toast. The
    fields are what the operator needs: which key, and what is wrong with it.
    """
    parts = []
    for err in exc.errors():
        location = '.'.join(str(p) for p in err.get('loc', ())) or 'config'
        message = err.get('msg', 'is invalid')
        for prefix in ('Value error, ', 'Assertion failed, '):
            if message.startswith(prefix):
                message = message[len(prefix):]
        parts.append(f'{location}: {message}')
    return '; '.join(parts)
