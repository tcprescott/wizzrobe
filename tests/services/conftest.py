import pytest

from tests.services.test_survey_service import event_world  # noqa: F401  (shared with test_survey_comments)

_ALLOW_GATES = (
    'is_staff',
    'is_tournament_admin',
    'can_edit_tournament',
    'can_crud_match',
    'can_run_match',
    'can_confirm_match',
    'can_assign_match_stream',
    'can_approve_crew',
    'can_manage_stages',
    'can_manage_volunteers',
    'can_grant_roles',
)


@pytest.fixture
def bypass_auth(monkeypatch):
    """Disable AuthService permission checks for service unit tests.

    Opt in per module with ``pytestmark = pytest.mark.usefixtures("bypass_auth")``
    (or request it directly). Tests exercising a *denied* path re-patch the
    specific gate to raise/deny, which overrides this fixture.
    """
    from application.services import auth_service

    async def allow(*_args, **_kwargs):
        return True

    async def noop_ensure(*_args, **_kwargs):
        return None

    for name in _ALLOW_GATES:
        monkeypatch.setattr(auth_service.AuthService, name, allow)
    monkeypatch.setattr(auth_service.AuthService, 'ensure', noop_ensure)
