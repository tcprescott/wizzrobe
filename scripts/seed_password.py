"""Dev-seed fixtures for password sign-in (/password-login).

Its own module because ``seed_dev.py`` sits past the file-length guideline.
"""

from datetime import datetime, timezone

from application.services.password_auth_service import hash_password
from models import User

DEV_PASSWORD = 'wizzrobe-test-password'


async def seed_password_sign_in(users: dict[str, User]) -> None:
    """Password sign-in fixtures: a Discord user who also has a password
    (``staff_user`` as ``staff``) and an account with no Discord at all
    (``desk_password`` as ``desk``, a check-in desk member of each community)."""
    now = datetime.now(timezone.utc)
    staff = users['staff_user']
    if staff.login_name != 'staff':
        staff.login_name = 'staff'
        staff.password_hash = await hash_password(DEV_PASSWORD)
        staff.password_changed_at = now
        await staff.save()
    desk = await User.get_or_none(login_name='desk')
    if desk is None:
        desk = await User.create(
            username='desk', display_name='Desk Laptop (no Discord)', login_name='desk',
            password_hash=await hash_password(DEV_PASSWORD), password_changed_at=now,
        )
    users['desk_password'] = desk
