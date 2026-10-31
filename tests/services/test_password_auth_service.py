"""Password sign-in: the service a super-admin uses on /platform, the sign-in
check, and the session resolver every page uses to learn who is signed in."""

import pytest

from application.services.audit_service import AuditActions
from application.services.auth_service import (
    AUTH_METHOD_PASSWORD,
    get_user_from_session,
    password_session_stamp,
)
from application.services.password_auth_service import (
    PasswordAuthService,
    generate_password,
    hash_password,
    verify_password,
)
from application.utils import login_throttle
from models import AuditLog, Role, User, UserRole

PASSWORD = 'correct-horse-battery'


@pytest.fixture
async def admin(db):
    user = await User.create(discord_id=91001, username='platform-owner')
    await UserRole.create(user=user, role=Role.SUPER_ADMIN, tenant=None)
    return user


@pytest.fixture
async def member(db):
    return await User.create(discord_id=91002, username='Mira')


def _password_session(user: User) -> dict:
    return {
        'authenticated': True, 'user_id': user.id,
        'auth_method': AUTH_METHOD_PASSWORD, 'password_stamp': password_session_stamp(user),
    }


async def test_hash_round_trip_and_rejects_wrong_password():
    encoded = await hash_password(PASSWORD)
    assert PASSWORD not in encoded
    assert await verify_password(PASSWORD, encoded)
    assert not await verify_password('wrong-password-here', encoded)
    assert not await verify_password(PASSWORD, None)


def test_generated_passwords_are_long_enough_and_differ():
    a, b = generate_password(), generate_password()
    assert a != b and len(a) >= 12


async def test_set_password_then_authenticate_case_insensitive_name(admin, member):
    service = PasswordAuthService()
    await service.set_password(admin, member, 'MVance', PASSWORD)
    await member.refresh_from_db()
    assert member.login_name == 'mvance'
    assert (await service.authenticate('mvance', PASSWORD)).id == member.id
    assert (await service.authenticate('  MVANCE ', PASSWORD)).id == member.id
    assert await service.authenticate('mvance', 'not-the-password') is None
    assert await service.authenticate('nobody-here', PASSWORD) is None


async def test_audit_never_records_the_password(admin, member):
    await PasswordAuthService().set_password(admin, member, 'mvance', PASSWORD)
    row = await AuditLog.filter(action=AuditActions.USER_PASSWORD_SET).first()
    assert row is not None
    assert PASSWORD not in str(row.details)


async def test_only_a_super_admin_can_set_passwords(member):
    other = await User.create(discord_id=91003, username='staffer')
    with pytest.raises(PermissionError):
        await PasswordAuthService().set_password(other, member, 'mvance', PASSWORD)


async def test_validation_messages(admin, member):
    service = PasswordAuthService()
    with pytest.raises(ValueError, match='at least 12'):
        await service.set_password(admin, member, 'mvance', 'short')
    with pytest.raises(ValueError, match='Sign-in names'):
        await service.set_password(admin, member, 'a b', PASSWORD)
    await service.set_password(admin, member, 'mvance', PASSWORD)
    other = await User.create(discord_id=91004, username='other')
    with pytest.raises(ValueError, match='taken'):
        await service.set_password(admin, other, 'MVANCE', PASSWORD)


async def test_create_account_without_discord(admin):
    user = await PasswordAuthService().create_account(admin, 'Ana Lucero', 'alucero', PASSWORD)
    assert user.discord_id is None
    assert user.preferred_name == 'Ana Lucero'
    assert (await PasswordAuthService().authenticate('alucero', PASSWORD)).id == user.id


async def test_a_password_only_account_cannot_lose_its_password(admin):
    user = await PasswordAuthService().create_account(admin, 'Ana', 'alucero', PASSWORD)
    with pytest.raises(ValueError, match='Deactivate'):
        await PasswordAuthService().remove_password(admin, user)


async def test_inactive_and_placeholder_accounts_cannot_sign_in(admin, member):
    service = PasswordAuthService()
    await service.set_password(admin, member, 'mvance', PASSWORD)
    await service.set_active(admin, member, False)
    assert await service.authenticate('mvance', PASSWORD) is None
    placeholder = await User.create(username='sg', is_placeholder=True)
    with pytest.raises(ValueError):
        await service.set_password(admin, placeholder, 'sgname', PASSWORD)


async def test_cannot_deactivate_yourself(admin):
    with pytest.raises(ValueError):
        await PasswordAuthService().set_active(admin, admin, False)


async def test_password_session_resolves_and_ends_on_change_or_removal(admin, member):
    service = PasswordAuthService()
    await service.set_password(admin, member, 'mvance', PASSWORD)
    await member.refresh_from_db()
    session = _password_session(member)
    assert (await get_user_from_session(session)).id == member.id

    await service.set_password(admin, member, 'mvance', 'a-new-password-here')
    assert await get_user_from_session(session) is None

    await member.refresh_from_db()
    session = _password_session(member)
    await service.remove_password(admin, member)
    assert await get_user_from_session(session) is None


async def test_discord_session_by_user_id_survives_a_password_change(admin, member):
    session = {'authenticated': True, 'user_id': member.id, 'discord_id': member.discord_id}
    await PasswordAuthService().set_password(admin, member, 'mvance', PASSWORD)
    assert (await get_user_from_session(session)).id == member.id


async def test_legacy_discord_session_gains_user_id(member):
    session = {'authenticated': True, 'discord_id': member.discord_id}
    assert (await get_user_from_session(session)).id == member.id
    assert session['user_id'] == member.id


async def test_deactivated_account_session_resolves_to_none(admin, member):
    session = {'authenticated': True, 'user_id': member.id}
    await PasswordAuthService().set_active(admin, member, False)
    assert await get_user_from_session(session) is None


def test_throttle_blocks_one_name_after_repeated_failures():
    login_throttle.reset()
    try:
        for _ in range(login_throttle.MAX_FAILURES_PER_NAME):
            login_throttle.record_failure('mvance', None)
        assert login_throttle.is_blocked('mvance', None)
        assert not login_throttle.is_blocked('someone-else', None)
        login_throttle.record_success('mvance')
        assert not login_throttle.is_blocked('mvance', None)
    finally:
        login_throttle.reset()


def test_throttle_counts_each_try_before_it_is_checked():
    """Tries fired in parallel each take a slot up front, so the limit holds even
    before any of them has failed."""
    login_throttle.reset()
    try:
        for _ in range(login_throttle.MAX_FAILURES_PER_NAME):
            assert login_throttle.begin_attempt('mvance', 'browser-a')
        assert not login_throttle.begin_attempt('mvance', 'browser-b')
        login_throttle.record_success('mvance', 'browser-a')
        assert login_throttle.begin_attempt('mvance', 'browser-b')
    finally:
        login_throttle.reset()


def test_throttle_success_hands_back_the_browser_slot():
    login_throttle.reset()
    try:
        for i in range(login_throttle.MAX_FAILURES_PER_BROWSER * 2):
            assert login_throttle.begin_attempt(f'name-{i}', 'desk-laptop')
            login_throttle.record_success(f'name-{i}', 'desk-laptop')
        assert not login_throttle.is_blocked('anyone', 'desk-laptop')
    finally:
        login_throttle.reset()


async def test_successful_sign_in_is_audited_without_the_password(admin, member):
    service = PasswordAuthService()
    await service.set_password(admin, member, 'mvance', PASSWORD)
    await service.authenticate('mvance', 'not-the-password')
    assert not await AuditLog.filter(action=AuditActions.USER_PASSWORD_SIGNED_IN).exists()
    await service.authenticate('mvance', PASSWORD)
    row = await AuditLog.get(action=AuditActions.USER_PASSWORD_SIGNED_IN)
    assert row.user_id == member.id
    assert PASSWORD not in str(row.details)


async def test_super_admins_cannot_have_or_use_a_password(admin, member):
    service = PasswordAuthService()
    with pytest.raises(ValueError, match='Discord only'):
        await service.set_password(admin, admin, 'owner', PASSWORD)

    await service.set_password(admin, member, 'mvance', PASSWORD)
    await member.refresh_from_db()
    session = _password_session(member)
    await UserRole.create(user=member, role=Role.SUPER_ADMIN, tenant=None)
    assert await service.authenticate('mvance', PASSWORD) is None
    assert await get_user_from_session(session) is None
