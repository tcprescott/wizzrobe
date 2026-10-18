"""Email and Telegram notifications, and the "send me a test" button.

Both channels are copies of the Discord DM, like web push: every DM that goes
through ``DiscordService.send_dm`` is mirrored here (:meth:`mirror_dm`), so
email and Telegram reach people for exactly the messages DMs do and nothing new
has to opt into them. A person turns each on from Profile → Notifications:

* **Email** is stored only once confirmed, either from Discord (an incremental
  ``identify email`` authorization, accepted only when Discord says the address
  is verified) or by a 6-digit code we mail to a typed address.
* **Telegram** is connected by a one-time ``t.me/<bot>?start=<token>`` link; the
  bot worker (``telegram_bot_worker``) sees the ``/start <token>`` and binds
  that chat. No phone number is involved. ``/stop`` in the chat turns it off.

Gated by the ``notification_channels`` flag. The settings entry points refuse
with it off; the DM mirror (a soft integration point on every DM) and the bot
worker (no community in scope) skip quietly instead.

Players are untrusted, so every outbound send a person can trigger is
rate-limited per account: confirmation codes, and tests.
"""

import hashlib
import hmac
import html
import logging
import os
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Deque, Dict, Iterable, Optional, Tuple

from itsdangerous import BadSignature, URLSafeSerializer

from application.feature_flags import requires_feature
from application.repositories import NotificationChannelRepository
from application.services.audit_service import AuditActions, AuditService
from application.utils.clients import sendgrid_client, telegram_client
from application.utils.environment import get_base_url
from application.utils.mocks.mock_discord import is_mock_discord
from models import FeatureFlag, User

logger = logging.getLogger(__name__)

EMAIL = 'email'
TELEGRAM = 'telegram'

CODE_TTL = timedelta(minutes=15)
TELEGRAM_LINK_TTL = timedelta(minutes=15)
MAX_CODE_ATTEMPTS = 5

#: Confirmation emails one account may request in a rolling hour.
CODES_PER_HOUR = 5
#: Test sends one account may make in a rolling ten minutes.
TESTS_PER_WINDOW = 3
TEST_WINDOW_SECONDS = 600

_SUBJECT_LIMIT = 90
_BODY_LIMIT = 4000
# Same tokens web push strips: templates only emit **bold** and the bracket
# escape, and member names legitimately contain __.
_MARKDOWN = re.compile(r'\*\*|\\(?=[\[\]])')
# Deliberately plain: one @, a dotted domain, no spaces, quotes or brackets.
# The confirmation code is the real proof; this only stops obvious junk and
# anything that could smuggle a second recipient or a header.
_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)


class Channel(str, Enum):
    """Where a test can go. ``DEVICES`` is every web-push device the person has."""

    DISCORD = 'discord'
    DEVICES = 'devices'
    EMAIL = 'email'
    TELEGRAM = 'telegram'


# Single worker, so an in-process window is enough (see login_throttle).
_hits: Dict[Tuple[str, int], Deque[float]] = defaultdict(deque)


def _take_slot(kind: str, user_id: int, limit: int, window: float) -> bool:
    now = time.monotonic()
    hits = _hits[(kind, user_id)]
    while hits and hits[0] < now - window:
        hits.popleft()
    if len(hits) >= limit:
        return False
    hits.append(now)
    return True


def reset_rate_limits() -> None:
    _hits.clear()


def email_available() -> bool:
    """Whether this server can send email (or fakes it in mock development)."""
    return sendgrid_client.is_configured() or is_mock_discord()


def telegram_available() -> bool:
    return telegram_client.is_configured() or is_mock_discord()


def _secret() -> bytes:
    return (os.environ.get('STORAGE_SECRET') or '').encode()


def _hash(*parts: object) -> str:
    message = '\x1f'.join(str(p) for p in parts).encode()
    return hmac.new(_secret(), message, hashlib.sha256).hexdigest()


def _unsubscribe_serializer() -> URLSafeSerializer:
    return URLSafeSerializer(os.environ.get('STORAGE_SECRET') or '', salt='email-unsubscribe')


def unsubscribe_url(user: User) -> str:
    token = _unsubscribe_serializer().dumps({'u': user.id, 'e': user.email or ''})
    return f'{get_base_url()}/notifications/email/unsubscribe?t={token}'


def normalize_email(raw: str) -> str:
    email = (raw or '').strip()
    if len(email) > 254 or not _EMAIL_RE.match(email):
        raise ValueError("That doesn't look like an email address. Check it and try again.")
    local, _, domain = email.rpartition('@')
    return f'{local}@{domain.lower()}'


def plain_text(message: str) -> str:
    text = _MARKDOWN.sub('', message or '').strip()
    if len(text) > _BODY_LIMIT:
        text = text[: _BODY_LIMIT - 1] + '…'
    return text


def _subject(text: str) -> str:
    first = next((line.strip() for line in text.splitlines() if line.strip()), 'Wizzrobe')
    if len(first) > _SUBJECT_LIMIT:
        first = first[: _SUBJECT_LIMIT - 1] + '…'
    return first


def _now() -> datetime:
    return datetime.now(timezone.utc)


class NotificationChannelService:
    """Settings, delivery and tests for the email and Telegram channels."""

    def __init__(self) -> None:
        self.repository = NotificationChannelRepository()
        self.audit_service = AuditService()

    # ------------------------------------------------------------- email

    @requires_feature(FeatureFlag.NOTIFICATION_CHANNELS)
    async def start_email_verification(self, user: User, raw_email: str) -> str:
        """Mail a 6-digit code to ``raw_email``. Returns the address as stored."""
        if not email_available():
            raise ValueError("Email isn't set up on this server yet.")
        email = normalize_email(raw_email)
        if not _take_slot('email-code', user.id, CODES_PER_HOUR, 3600):
            raise ValueError("That's a lot of codes in a short time. Wait a bit and try again.")
        code = f'{secrets.randbelow(10 ** 6):06d}'
        await self.repository.replace_verification(
            user, EMAIL, email, _hash(EMAIL, user.id, code), _now() + CODE_TTL,
        )
        text = (
            f'Your Wizzrobe code is {code}\n\n'
            'Type it on your profile to start getting notifications at this address. '
            "It expires in 15 minutes. If you didn't ask for it, ignore this email."
        )
        body = (
            f'<p style="font-size:16px">Your Wizzrobe code is</p>'
            f'<p style="font-size:32px;font-weight:700;letter-spacing:6px">{code}</p>'
            '<p>Type it on your profile to start getting notifications at this address. '
            "It expires in 15 minutes. If you didn't ask for it, ignore this email.</p>"
        )
        if not await self._deliver_email(email, f'Your Wizzrobe code: {code}', text, body):
            raise ValueError("We couldn't send to that address. Check it and try again.")
        return email

    @requires_feature(FeatureFlag.NOTIFICATION_CHANNELS)
    async def confirm_email(self, user: User, code: str) -> str:
        verification = await self.repository.get_verification(user, EMAIL)
        if verification is None or verification.expires_at < _now() or not verification.target:
            raise ValueError('That code has expired. Send yourself a new one.')
        if verification.attempts >= MAX_CODE_ATTEMPTS:
            await self.repository.delete_verification(verification)
            raise ValueError('Too many wrong codes. Send yourself a new one.')
        cleaned = re.sub(r'\D', '', code or '')
        if not hmac.compare_digest(verification.secret_hash, _hash(EMAIL, user.id, cleaned)):
            await self.repository.count_attempt(verification)
            raise ValueError("That code doesn't match. Check the email and try again.")
        email = verification.target
        await self.repository.delete_verification(verification)
        await self.repository.set_email(user, email, enabled=True)
        await self.audit_service.write_log(
            user, AuditActions.NOTIFICATION_EMAIL_CONFIRMED,
            {'target_user_id': user.id, 'source': 'code'},
        )
        return email

    @requires_feature(FeatureFlag.NOTIFICATION_CHANNELS)
    async def use_discord_email(
        self, user: User, discord_id: str, email: Optional[str], verified: bool,
    ) -> str:
        """Store the address Discord returned for this person's own account."""
        if not user.discord_id or str(user.discord_id) != str(discord_id):
            raise ValueError("That Discord account isn't the one you're signed in with.")
        if not email:
            raise ValueError("Your Discord account doesn't have an email address. Use a different address instead.")
        if not verified:
            raise ValueError("Discord hasn't confirmed that email yet. Use a different address and we'll send it a code.")
        address = normalize_email(email)
        await self.repository.set_email(user, address, enabled=True)
        await self.audit_service.write_log(
            user, AuditActions.NOTIFICATION_EMAIL_CONFIRMED,
            {'target_user_id': user.id, 'source': 'discord'},
        )
        return address

    @requires_feature(FeatureFlag.NOTIFICATION_CHANNELS)
    async def remove_email(self, user: User) -> None:
        if not user.email:
            return
        await self.repository.set_email(user, None, enabled=False)
        await self.audit_service.write_log(
            user, AuditActions.NOTIFICATION_EMAIL_REMOVED, {'target_user_id': user.id},
        )

    @requires_feature(FeatureFlag.NOTIFICATION_CHANNELS)
    async def set_email_enabled(self, user: User, enabled: bool) -> None:
        if enabled and not user.email:
            raise ValueError('Add an email address first.')
        await self.repository.set_email_notifications(user, enabled)

    # feature-gate: exempt — the unsubscribe link must always turn email off.
    async def unsubscribe_email(self, token: str) -> bool:
        """The one-click link in every email. ``True`` when it turned email off.

        Reached signed out, from any community or none, so it is not
        flag-gated: turning a notification *off* must always work.
        """
        try:
            data = _unsubscribe_serializer().loads(token or '')
            user_id = int(data['u'])
        except (BadSignature, KeyError, TypeError, ValueError):
            return False
        user = await self.repository.get_user(user_id)
        # A link from before the address changed still turns off the old one only.
        if user is None or (user.email or '') != data.get('e'):
            return False
        if user.email_notifications:
            await self.repository.set_email_notifications(user, False)
        return True

    # ---------------------------------------------------------- telegram

    @requires_feature(FeatureFlag.NOTIFICATION_CHANNELS)
    async def create_telegram_link(self, user: User) -> Optional[str]:
        """The ``t.me`` link that connects this account, or ``None`` in mock mode.

        Mock development has no bot to talk to, so the chat is bound on the spot.
        """
        if is_mock_discord():
            await self._bind_telegram(user, -user.id, f'mock_{user.username}'[:64])
            return None
        if telegram_client.is_configured():
            bot = await telegram_client.bot_username()
            if not bot:
                raise ValueError("Telegram isn't answering right now. Try again in a minute.")
            token = secrets.token_urlsafe(24)
            await self.repository.replace_verification(
                user, TELEGRAM, None, _hash(TELEGRAM, token), _now() + TELEGRAM_LINK_TTL,
            )
            return f'https://t.me/{bot}?start={token}'
        raise ValueError("Telegram isn't set up on this server yet.")

    @requires_feature(FeatureFlag.NOTIFICATION_CHANNELS)
    async def disconnect_telegram(self, user: User) -> None:
        if user.telegram_chat_id is None:
            return
        await self.repository.set_telegram(user, None, None)
        await self.audit_service.write_log(
            user, AuditActions.NOTIFICATION_TELEGRAM_UNLINKED, {'target_user_id': user.id},
        )

    @requires_feature(FeatureFlag.NOTIFICATION_CHANNELS)
    async def set_telegram_enabled(self, user: User, enabled: bool) -> None:
        if enabled and user.telegram_chat_id is None:
            raise ValueError('Connect Telegram first.')
        await self.repository.set_telegram_notifications(user, enabled)

    # feature-gate: exempt — bot worker with no community in scope; see docstring.
    async def handle_telegram_start(self, token: str, chat_id: int, username: Optional[str]) -> str:
        """Bind the chat that sent ``/start <token>``. Returns the bot's reply.

        Called by the bot worker with no community in scope; the token was only
        ever minted behind the flag, so it carries that check with it.
        """
        verification = await self.repository.get_verification_by_hash(TELEGRAM, _hash(TELEGRAM, token))
        if verification is None or verification.expires_at < _now():
            return 'That link has expired. Open Wizzrobe, go to Profile → Notifications and press Connect again.'
        user = verification.user
        owner = await self.repository.get_by_telegram_chat(chat_id)
        if owner is not None and owner.id != user.id:
            return ('This Telegram account is already connected to another Wizzrobe account. '
                    'Disconnect it there first.')
        await self.repository.delete_verification(verification)
        await self._bind_telegram(user, chat_id, username)
        return (f"You're connected, {user.preferred_name}. Wizzrobe will message you here about "
                'your matches and shifts. Send /stop any time to turn it off.')

    # feature-gate: exempt — /stop must always turn Telegram off.
    async def handle_telegram_stop(self, chat_id: int) -> str:
        user = await self.repository.get_by_telegram_chat(chat_id)
        if user is None:
            return "This chat isn't connected to a Wizzrobe account."
        await self.repository.set_telegram_notifications(user, False)
        return 'Telegram notifications are off. Turn them back on from Profile → Notifications.'

    async def _bind_telegram(self, user: User, chat_id: int, username: Optional[str]) -> None:
        await self.repository.set_telegram(user, chat_id, (username or '')[:64] or None)
        await self.audit_service.write_log(
            user, AuditActions.NOTIFICATION_TELEGRAM_LINKED, {'target_user_id': user.id},
        )

    # ------------------------------------------------------------ tests

    # feature-gate: exempt — Discord and device tests work in every community;
    # email and Telegram are refused below unless the flag is on.
    async def send_test(self, user: User, channels: Iterable[Channel]) -> Dict[Channel, bool]:
        """Send a test to each chosen channel. Maps each to whether it went out."""
        chosen = list(dict.fromkeys(Channel(c) for c in channels))
        if not chosen:
            raise ValueError('Pick at least one place to send the test.')
        if any(c in (Channel.EMAIL, Channel.TELEGRAM) for c in chosen):
            from application.services.feature_flag_service import FeatureFlagService
            if not await FeatureFlagService().is_enabled(FeatureFlag.NOTIFICATION_CHANNELS):
                raise ValueError("Email and Telegram aren't turned on for this community.")
        if not _take_slot('test', user.id, TESTS_PER_WINDOW, TEST_WINDOW_SECONDS):
            raise ValueError('You can send 3 tests every 10 minutes. Try again in a few minutes.')
        from application.services.discord.discord_service import DiscordService
        from application.services.notification_links import profile_notifications
        from application.services.web_push_service import WebPushService

        message = "This is a test from Wizzrobe. If you're reading it, notifications reach you here."
        link = await profile_notifications()
        url, label = (link.url, link.label) if link else (None, None)
        results: Dict[Channel, bool] = {}
        for channel in chosen:
            if channel is Channel.DISCORD:
                if not user.discord_id:
                    results[channel] = False
                    continue
                ok, _ = await DiscordService().send_dm(user.discord_id, message, link=link, mirror=False)
                results[channel] = ok
            elif channel is Channel.DEVICES:
                results[channel] = await WebPushService().send_test(user, message, navigate=url)
            elif channel is Channel.EMAIL:
                results[channel] = await self._email(user, message, url, label)
            elif channel is Channel.TELEGRAM:
                results[channel] = await self._telegram(user, message, url, label)
        return results

    # --------------------------------------------------------- delivery

    # feature-gate: exempt — soft integration point on every DM; checks the flag itself.
    async def mirror_dm(
        self, discord_id: int, message: str, navigate: Optional[str] = None,
        label: Optional[str] = None, silent: bool = False,
    ) -> None:
        """Copy one Discord DM to the recipient's email and Telegram.

        Runs on the event dispatch worker inside the sending community's
        ``tenant_scope``; never raises. ``silent`` reaches Telegram only: an
        email has no way to arrive quietly.
        """
        try:
            user = await self.repository.get_by_discord_id(discord_id)
            if user is None:
                return
            wants_email = user.email_notifications and bool(user.email)
            wants_telegram = user.telegram_notifications and user.telegram_chat_id is not None
            if not (wants_email or wants_telegram):
                return
            from application.services.feature_flag_service import FeatureFlagService, reset_flag_cache

            # This worker's context outlives any request, so its flag cache would never refresh.
            reset_flag_cache()
            if not await FeatureFlagService().is_enabled(FeatureFlag.NOTIFICATION_CHANNELS):
                return
            if wants_email:
                await self._email(user, message, navigate, label)
            if wants_telegram:
                await self._telegram(user, message, navigate, label, silent=silent)
        except Exception:
            logger.exception('email/Telegram DM mirror failed for discord id %s', discord_id)

    async def _email(self, user: User, message: str, navigate: Optional[str], label: Optional[str]) -> bool:
        if not user.email:
            return False
        text = plain_text(message)
        stop = unsubscribe_url(user)
        footer = ("You're getting this because you turned on email notifications in Wizzrobe.")
        text_body = text
        if navigate:
            text_body += f'\n\n{label or "Open Wizzrobe"}: {navigate}'
        text_body += f'\n\n--\n{footer}\nStop these emails: {stop}'
        paragraphs = ''.join(
            f'<p style="margin:0 0 12px">{html.escape(line)}</p>' for line in text.splitlines() if line.strip()
        )
        button = ''
        if navigate:
            button = (
                f'<p style="margin:20px 0"><a href="{html.escape(navigate, quote=True)}" '
                'style="background:#9C6B12;color:#ffffff;padding:10px 18px;border-radius:8px;'
                f'text-decoration:none;font-weight:700">{html.escape(label or "Open Wizzrobe")}</a></p>'
            )
        html_body = (
            '<div style="font-family:Arial,sans-serif;font-size:15px;line-height:1.5;color:#1C1B19">'
            f'{paragraphs}{button}'
            f'<p style="margin-top:28px;font-size:12px;color:#6F6D67">{html.escape(footer)} '
            f'<a href="{html.escape(stop, quote=True)}" style="color:#6F6D67">Stop these emails</a>.</p>'
            '</div>'
        )
        headers = {
            'List-Unsubscribe': f'<{stop}>',
            'List-Unsubscribe-Post': 'List-Unsubscribe=One-Click',
        }
        return await self._deliver_email(user.email, _subject(text), text_body, html_body, headers)

    async def _deliver_email(
        self, to: str, subject: str, text: str, html_body: str,
        headers: Optional[Dict[str, str]] = None,
    ) -> bool:
        # Mock first: a dev box on a production snapshot must never mail real people.
        if is_mock_discord():
            print(f'[MOCK email] -> {to}: {subject}\n{text}')
            return True
        if sendgrid_client.is_configured():
            return await sendgrid_client.send_email(to, subject, text, html_body, headers)
        return False

    async def _telegram(
        self, user: User, message: str, navigate: Optional[str], label: Optional[str],
        silent: bool = False,
    ) -> bool:
        chat_id = user.telegram_chat_id
        if chat_id is None:
            return False
        text = plain_text(message)
        if is_mock_discord():
            suffix = f' [button: {label} -> {navigate}]' if navigate else ''
            print(f'[MOCK Telegram] -> {chat_id}: {text}{suffix}')
            return True
        if telegram_client.is_configured():
            try:
                return await telegram_client.send_message(chat_id, text, navigate, label, silent=silent)
            except telegram_client.TelegramBlocked:
                # They blocked the bot or deleted the chat; stop trying.
                logger.info('Telegram chat for user %s refused messages; disconnecting it', user.id)
                await self.repository.set_telegram(user, None, None)
                return False
        return False
