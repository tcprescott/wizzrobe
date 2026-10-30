"""Writing the signed-in identity into ``app.storage.user``.

Every sign-in (Discord, the Discord ``/login`` link, the mock picker, password)
goes through :func:`start_session`, so each one records the account id the
same way and none inherits a previous sign-in's password-session keys.
"""

from typing import Any, Optional

from nicegui import app

from application.services.auth_service import (
    AUTH_METHOD_PASSWORD,
    SESSION_AUTH_METHOD,
    SESSION_PASSWORD_STAMP_FIELD,
    SESSION_USER_ID,
    password_session_stamp,
)
from models import User


def start_session(user: User, fields: Optional[dict[str, Any]] = None, *, password: bool = False) -> None:
    storage = app.storage.user
    for key in (SESSION_AUTH_METHOD, SESSION_PASSWORD_STAMP_FIELD):
        storage.pop(key, None)
    if password:
        storage.pop('discord_id', None)
        storage[SESSION_AUTH_METHOD] = AUTH_METHOD_PASSWORD
        storage[SESSION_PASSWORD_STAMP_FIELD] = password_session_stamp(user)
    storage.update({**(fields or {}), 'authenticated': True, SESSION_USER_ID: user.id})
