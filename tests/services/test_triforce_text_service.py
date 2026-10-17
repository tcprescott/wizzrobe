"""Tests for TriforceTextService.

Covers validation, permission gating, balanced vs random selection, and the
audit trail. Uses the in-memory ``db`` fixture to exercise the real ORM.
"""

import pytest

from application.services.audit_service import AuditActions
from application.services.triforce_text_service import TriforceTextService
from models import AuditLog, Role, Tournament, TriforceText, User, UserRole

pytestmark = pytest.mark.asyncio


async def _make_user(discord_id: int, name: str, submitter: bool = True) -> User:
    user = await User.create(discord_id=discord_id, username=name, display_name=name)
    if submitter:
        await UserRole.create(user=user, role=Role.TRIFORCE_SUBMITTER)
    return user


async def _make_tournament(admins: list[User]) -> Tournament:
    t = await Tournament.create(name='Test', is_active=True, seed_generator='alttpr')
    for a in admins:
        await t.admins.add(a)
    return t


async def test_submit_validates_line_count(db):
    svc = TriforceTextService()
    user = await _make_user(1, 'alice')
    t = await _make_tournament([])
    with pytest.raises(ValueError, match="exactly 3 lines"):
        await svc.submit(t.id, ['only', 'two'], user)


async def test_submit_rejects_long_lines(db):
    svc = TriforceTextService()
    user = await _make_user(1, 'alice')
    t = await _make_tournament([])
    with pytest.raises(ValueError, match="Line 2"):
        await svc.submit(t.id, ['ok', 'this line is well over nineteen characters', 'ok'], user)


async def test_submit_rejects_invalid_characters(db):
    svc = TriforceTextService()
    user = await _make_user(1, 'alice')
    t = await _make_tournament([])
    with pytest.raises(ValueError, match="Line 1"):
        await svc.submit(t.id, ['has@symbol', '', ''], user)


async def test_submit_rejects_all_empty(db):
    svc = TriforceTextService()
    user = await _make_user(1, 'alice')
    t = await _make_tournament([])
    with pytest.raises(ValueError, match="At least one"):
        await svc.submit(t.id, ['', '', ''], user)


async def test_submit_rejects_inactive_tournament(db):
    svc = TriforceTextService()
    user = await _make_user(1, 'alice')
    t = await Tournament.create(name='Closed', is_active=False)
    with pytest.raises(ValueError, match="not accepting"):
        await svc.submit(t.id, ['hi', '', ''], user)


async def test_submit_rejects_unsupported_generator(db):
    svc = TriforceTextService()
    user = await _make_user(1, 'alice')
    t = await Tournament.create(name='OoT', is_active=True, seed_generator='ootr')
    with pytest.raises(ValueError, match="not accepting"):
        await svc.submit(t.id, ['hi', '', ''], user)


async def test_submit_requires_paid_role(db):
    svc = TriforceTextService()
    user = await _make_user(1, 'alice', submitter=False)
    t = await _make_tournament([])
    with pytest.raises(ValueError, match="paid"):
        await svc.submit(t.id, ['hi', '', ''], user)


async def test_list_supporting_tournaments(db):
    svc = TriforceTextService()
    supported = await Tournament.create(name='ALTTP', is_active=True, seed_generator='alttpr')
    await Tournament.create(name='OoT', is_active=True, seed_generator='ootr')
    await Tournament.create(name='Closed', is_active=False, seed_generator='alttpr')

    rows = await svc.list_supporting_tournaments()
    assert [t.id for t in rows] == [supported.id]


async def test_submit_writes_pending_row_and_audit(db):
    svc = TriforceTextService()
    user = await _make_user(1, 'alice')
    t = await _make_tournament([])

    created = await svc.submit(t.id, ['Good Luck', 'Have Fun', ''], user)
    assert created.approved is None
    assert created.author == 'alice'
    assert created.text == 'Good Luck\nHave Fun\n'

    actions = await AuditLog.all().values_list('action', flat=True)
    assert AuditActions.TRIFORCE_TEXT_SUBMITTED in actions


async def test_moderate_requires_tournament_admin(db):
    svc = TriforceTextService()
    submitter = await _make_user(1, 'alice')
    not_admin = await _make_user(2, 'bob')
    t = await _make_tournament([])

    entry = await svc.submit(t.id, ['hi', '', ''], submitter)
    with pytest.raises(ValueError, match="permission"):
        await svc.moderate(entry.id, True, not_admin)


async def test_moderate_approves_and_audits(db):
    svc = TriforceTextService()
    submitter = await _make_user(1, 'alice')
    admin = await _make_user(2, 'mod')
    t = await _make_tournament([admin])

    entry = await svc.submit(t.id, ['hi', '', ''], submitter)
    updated = await svc.moderate(entry.id, True, admin)
    assert updated.approved is True
    assert updated.approved_at is not None
    actions = await AuditLog.all().values_list('action', flat=True)
    assert AuditActions.TRIFORCE_TEXT_APPROVED in actions


async def test_moderate_rejects_and_audits(db):
    svc = TriforceTextService()
    submitter = await _make_user(1, 'alice')
    admin = await _make_user(2, 'mod')
    t = await _make_tournament([admin])

    entry = await svc.submit(t.id, ['hi', '', ''], submitter)
    updated = await svc.moderate(entry.id, False, admin)
    assert updated.approved is False
    actions = await AuditLog.all().values_list('action', flat=True)
    assert AuditActions.TRIFORCE_TEXT_REJECTED in actions


async def test_list_for_moderation_filters_by_status(db):
    svc = TriforceTextService()
    alice = await _make_user(1, 'alice')
    bob = await _make_user(2, 'bob')
    admin = await _make_user(3, 'mod')
    t = await _make_tournament([admin])

    p = await svc.submit(t.id, ['pending', '', ''], alice)
    a = await svc.submit(t.id, ['approved', '', ''], alice)
    r = await svc.submit(t.id, ['rejected', '', ''], bob)
    await svc.moderate(a.id, True, admin)
    await svc.moderate(r.id, False, admin)

    pending = await svc.list_for_moderation(t.id, status='pending', actor=admin)
    approved = await svc.list_for_moderation(t.id, status='approved', actor=admin)
    rejected = await svc.list_for_moderation(t.id, status='rejected', actor=admin)
    all_rows = await svc.list_for_moderation(t.id, actor=admin)

    assert [x.id for x in pending] == [p.id]
    assert [x.id for x in approved] == [a.id]
    assert [x.id for x in rejected] == [r.id]
    assert len(all_rows) == 3


async def test_balanced_selection_weights_users_equally(db):
    svc = TriforceTextService()
    alice = await _make_user(1, 'alice')
    bob = await _make_user(2, 'bob')
    admin = await _make_user(3, 'mod')
    t = await _make_tournament([admin])

    # Alice submits 5 approved texts; Bob submits 1. Balanced selection
    # should not let alice's 5 dominate.
    for i in range(5):
        e = await svc.submit(t.id, [f'alice {i}', '', ''], alice)
        await svc.moderate(e.id, True, admin)
    b = await svc.submit(t.id, ['bob 0', '', ''], bob)
    await svc.moderate(b.id, True, admin)

    counts = {'alice': 0, 'bob': 0}
    for _ in range(400):
        text = await svc.get_balanced_text(t)
        if text and text.startswith('alice'):
            counts['alice'] += 1
        elif text == 'bob 0\n\n':
            counts['bob'] += 1
    # Expect roughly 50/50. Allow generous noise.
    assert 100 < counts['alice'] < 300
    assert 100 < counts['bob'] < 300


async def test_random_selection_weights_texts_equally(db):
    svc = TriforceTextService()
    alice = await _make_user(1, 'alice')
    admin = await _make_user(2, 'mod')
    t = await _make_tournament([admin])

    e1 = await svc.submit(t.id, ['a', '', ''], alice)
    e2 = await svc.submit(t.id, ['b', '', ''], alice)
    await svc.moderate(e1.id, True, admin)
    await svc.moderate(e2.id, True, admin)

    seen = set()
    for _ in range(50):
        seen.add(await svc.get_random_text(t))
    assert seen == {'a\n\n', 'b\n\n'}


async def test_selection_returns_none_when_no_approved(db):
    svc = TriforceTextService()
    t = await Tournament.create(name='Empty', is_active=True)
    assert await svc.get_balanced_text(t) is None
    assert await svc.get_random_text(t) is None


async def test_balanced_selection_includes_orphaned_texts(db):
    svc = TriforceTextService()
    submitter = await _make_user(1, 'alice')
    admin = await _make_user(2, 'mod')
    t = await _make_tournament([admin])

    # Approved text whose submitter is later deleted (FK becomes NULL).
    orphan = await svc.submit(t.id, ['orphan', '', ''], submitter)
    await svc.moderate(orphan.id, True, admin)
    await submitter.delete()
    orphan_row = await TriforceText.get(id=orphan.id)
    assert orphan_row.user_id is None

    text = await svc.get_balanced_text(t)
    assert text == 'orphan\n\n'


async def test_delete_requires_permission(db):
    svc = TriforceTextService()
    submitter = await _make_user(1, 'alice')
    other = await _make_user(2, 'bob')
    t = await _make_tournament([])

    entry = await svc.submit(t.id, ['hi', '', ''], submitter)
    with pytest.raises(ValueError, match="permission"):
        await svc.delete(entry.id, other)


async def test_delete_removes_row_and_audits(db):
    svc = TriforceTextService()
    submitter = await _make_user(1, 'alice')
    admin = await _make_user(2, 'mod')
    t = await _make_tournament([admin])

    entry = await svc.submit(t.id, ['hi', '', ''], submitter)
    await svc.delete(entry.id, admin)
    assert await TriforceText.filter(id=entry.id).exists() is False
    actions = await AuditLog.all().values_list('action', flat=True)
    assert AuditActions.TRIFORCE_TEXT_DELETED in actions


async def test_list_for_moderation_refuses_a_non_moderator(db):
    """The service makes the moderator check itself, so a caller that forgets it
    (a new page, tool or handler) can't list another tournament's pool."""
    svc = TriforceTextService()
    alice = await _make_user(11, 'alice')
    t = await _make_tournament([])
    with pytest.raises(PermissionError):
        await svc.list_for_moderation(t.id, actor=alice)
