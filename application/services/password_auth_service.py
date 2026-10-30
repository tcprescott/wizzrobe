"""Password sign-in and the platform's account administration.

Discord is the normal way in. A platform super-admin can also give an account a
sign-in name and password (or create an account that has no Discord at all) so
someone without Discord can sign in at the unlisted ``/password-login`` page.
There is no self-service: nobody sets, changes or resets their own password.

Passwords are hashed with scrypt (stdlib, memory-hard) and verified in a worker
thread so a sign-in never stalls the shared event loop.
"""

import asyncio
import base64
import hashlib
import hmac
import re
import secrets
from datetime import datetime, timezone
from typing import Optional

from application.repositories.user_repository import UserRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from models import User

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 256
_LOGIN_NAME_RE = re.compile(r'^[a-z0-9][a-z0-9._-]{2,63}$')

# scrypt cost: 2**14 * 8 * 128 bytes = 16 MiB and ~50 ms per hash.
_N, _R, _P = 2 ** 14, 8, 1
_SCHEME = 'scrypt'
# Anyone can make the sign-in page hash, and fresh names and browsers dodge the
# per-name and per-browser limits, so cap how many hashes run at once: a flood
# queues behind this instead of claiming 16 MiB and a worker thread apiece.
_MAX_CONCURRENT_HASHES = 4
_hash_slots = asyncio.Semaphore(_MAX_CONCURRENT_HASHES)


def _hash_sync(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode('utf-8'), salt=salt, n=n, r=r, p=p, maxmem=64 * 1024 * 1024, dklen=32)


def _encode(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _hash_sync(password, salt, _N, _R, _P)
    b64 = lambda b: base64.b64encode(b).decode('ascii')
    return f'{_SCHEME}${_N}${_R}${_P}${b64(salt)}${b64(digest)}'


def _verify_sync(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = encoded.split('$')
        if scheme != _SCHEME:
            return False
        actual = _hash_sync(password, base64.b64decode(salt), int(n), int(r), int(p))
        return hmac.compare_digest(actual, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


# Verified against when the name is unknown, so a miss costs the same time as a
# wrong password and response timing doesn't reveal which names exist.
_DUMMY_HASH = _encode(secrets.token_urlsafe(16))


async def hash_password(password: str) -> str:
    async with _hash_slots:
        return await asyncio.to_thread(_encode, password)


async def verify_password(password: str, encoded: Optional[str]) -> bool:
    async with _hash_slots:
        return await asyncio.to_thread(_verify_sync, password, encoded or _DUMMY_HASH) and bool(encoded)


def generate_password() -> str:
    """A random password that is easy to read aloud and type: four words of
    letters and digits joined by dashes, about 95 bits."""
    return '-'.join(secrets.token_urlsafe(6) for _ in range(4))


def normalize_login_name(value: Optional[str]) -> str:
    return (value or '').strip().lower()


class PasswordAuthService:
    """Sign-in check plus the super-admin account actions on /platform."""

    def __init__(self) -> None:
        self.repository = UserRepository()
        self.audit_service = AuditService()

    async def authenticate(self, login_name: str, password: str) -> Optional[User]:
        """The account these credentials open, or ``None`` for any failure.

        Unknown name, wrong password, no password, inactive or placeholder all
        return ``None`` alike, so the caller shows one message for every case.
        """
        name = normalize_login_name(login_name)
        if not name or not password or len(password) > MAX_PASSWORD_LENGTH:
            await verify_password('x', None)
            return None
        user = await self.repository.get_by_login_name(name)
        ok = await verify_password(password, user.password_hash if user else None)
        if not ok or user is None or not user.is_active or user.is_placeholder or user.is_system:
            return None
        # A password has no second factor, and a super-admin opens every
        # community: they sign in with Discord only.
        if await AuthService.is_super_admin(user):
            return None
        await self.audit_service.write_log(user, AuditActions.USER_PASSWORD_SIGNED_IN, {'login_name': name})
        return user

    @staticmethod
    async def _ensure_super_admin(actor: User) -> None:
        await AuthService.ensure(
            await AuthService.is_super_admin(actor),
            'Only a platform admin can manage sign-in passwords.',
        )

    async def list_accounts(self, actor: User) -> list[User]:
        await self._ensure_super_admin(actor)
        return await self.repository.list_for_platform()

    async def _check_login_name(self, login_name: str, user: Optional[User]) -> str:
        name = normalize_login_name(login_name)
        if not _LOGIN_NAME_RE.match(name):
            raise ValueError(
                'Sign-in names are 3 to 64 characters: letters, digits, dots, '
                'dashes and underscores, starting with a letter or digit.'
            )
        existing = await self.repository.get_by_login_name(name)
        if existing is not None and (user is None or existing.id != user.id):
            raise ValueError('That sign-in name is taken. Pick another.')
        return name

    @staticmethod
    def _check_password(password: str) -> None:
        if len(password or '') < MIN_PASSWORD_LENGTH:
            raise ValueError(f'Use at least {MIN_PASSWORD_LENGTH} characters.')
        if len(password) > MAX_PASSWORD_LENGTH:
            raise ValueError(f'Use at most {MAX_PASSWORD_LENGTH} characters.')

    async def create_account(self, actor: User, display_name: str, login_name: str, password: str) -> User:
        """A new account with no Discord link that signs in only by password."""
        await self._ensure_super_admin(actor)
        display = (display_name or '').strip()
        if not display:
            raise ValueError('Enter a display name.')
        if len(display) > 150:
            raise ValueError('Keep the display name to 150 characters.')
        name = await self._check_login_name(login_name, None)
        self._check_password(password)
        user = await self.repository.create_password_account(
            username=name, display_name=display, login_name=name,
            password_hash=await hash_password(password),
            changed_at=datetime.now(timezone.utc),
        )
        await self.audit_service.write_log(
            actor, AuditActions.USER_CREATED,
            {'target_user_id': user.id, 'login_name': name, 'source': 'platform_password'},
        )
        await self.audit_service.write_log(
            actor, AuditActions.USER_PASSWORD_SET,
            {'target_user_id': user.id, 'login_name': name, 'new_account': True},
        )
        return user

    async def set_password(self, actor: User, user: User, login_name: str, password: str) -> User:
        """Give an account a sign-in name and password, or change either."""
        await self._ensure_super_admin(actor)
        if user.is_placeholder or user.is_system:
            raise ValueError("This account can't have a password.")
        if await AuthService.is_super_admin(user):
            raise ValueError("Platform admins sign in with Discord only, so they can't have a password.")
        name = await self._check_login_name(login_name, user)
        self._check_password(password)
        await self.repository.update(
            user, login_name=name, password_hash=await hash_password(password),
            password_changed_at=datetime.now(timezone.utc),
        )
        await self.audit_service.write_log(
            actor, AuditActions.USER_PASSWORD_SET,
            {'target_user_id': user.id, 'login_name': name, 'new_account': False},
        )
        return user

    async def remove_password(self, actor: User, user: User) -> User:
        """Take password sign-in away. Ends every password session at once."""
        await self._ensure_super_admin(actor)
        if not user.password_hash:
            raise ValueError("This account doesn't have a password.")
        if not user.discord_id:
            raise ValueError(
                f"{user.preferred_name} has no Discord, so the password is the only way "
                f"in. Deactivate the account instead."
            )
        previous = user.login_name
        await self.repository.update(
            user, login_name=None, password_hash=None,
            password_changed_at=datetime.now(timezone.utc),
        )
        await self.audit_service.write_log(
            actor, AuditActions.USER_PASSWORD_REMOVED,
            {'target_user_id': user.id, 'login_name': previous},
        )
        return user

    async def set_active(self, actor: User, user: User, active: bool) -> User:
        """Deactivate or reactivate any account, platform-wide."""
        await self._ensure_super_admin(actor)
        if user.id == actor.id and not active:
            raise ValueError("You can't deactivate your own account.")
        if user.is_system or user.is_placeholder:
            raise ValueError("This account can't be changed here.")
        if user.is_active == active:
            return user
        await self.repository.update(user, is_active=active)
        await self.audit_service.write_log(
            actor, AuditActions.USER_ACTIVATION_CHANGED,
            {'target_user_id': user.id, 'is_active': active},
        )
        return user
