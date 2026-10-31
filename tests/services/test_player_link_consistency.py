"""All three account-link paths report a collision the same way.

Each provider column on ``User`` is ``unique=True`` — participant/result matching
has to resolve one user per provider id. Two of the three write paths pre-checked
that; ``ChallongeService.record_player_link``, the one the OAuth callback calls,
did not, so it reached the index, raised ``IntegrityError``, and the callback
answered *"Could not link Challonge. Please try again."* — advice that fails
identically every time it is followed.

Parameterised over every provider on purpose: this is the guard that a fourth one
cannot be added without the check.
"""

import pytest

from application.services.challonge_service import ChallongeService
from application.services.racetime_service import RacetimeService
from application.services.twitch_service import TwitchService
from models import User

pytestmark = pytest.mark.usefixtures('bypass_auth')

PROVIDERS = [
    ('racetime', RacetimeService, 'racetime', 'rtHolder1'),
    ('Twitch', TwitchService, 'twitch', '20009'),
    ('Challonge', ChallongeService, 'challonge', 'cu_9009'),
]


async def _make_user(discord_id: int, username: str, prefix=None, pid=None) -> User:
    kwargs = {f'{prefix}_user_id': pid} if prefix and pid else {}
    return await User.create(discord_id=discord_id, username=username, **kwargs)


@pytest.mark.parametrize('label,factory,prefix,pid', PROVIDERS, ids=[p[0] for p in PROVIDERS])
async def test_all_three_providers_report_a_collision_the_same_way(
    db, label, factory, prefix, pid,
):
    await _make_user(1, 'holder', prefix, pid)
    claimant = await _make_user(2, 'claimant')
    with pytest.raises(ValueError) as excinfo:
        await factory().record_player_link(claimant, pid, 'whatever', actor=claimant)
    message = str(excinfo.value)
    assert 'already linked to holder' in message, message
    assert label in message, message
    await claimant.refresh_from_db()
    assert getattr(claimant, f'{prefix}_user_id') is None


@pytest.mark.parametrize('label,factory,prefix,pid', PROVIDERS, ids=[p[0] for p in PROVIDERS])
async def test_all_three_providers_reject_a_blank_id(db, label, factory, prefix, pid):
    user = await _make_user(1, 'alice')
    with pytest.raises(ValueError, match='required'):
        await factory().record_player_link(user, '   ', 'whatever', actor=user)


@pytest.mark.parametrize('label,factory,prefix,pid', PROVIDERS, ids=[p[0] for p in PROVIDERS])
async def test_all_three_providers_strip_the_incoming_identity(
    db, label, factory, prefix, pid,
):
    user = await _make_user(1, 'alice')
    await factory().record_player_link(user, f'  {pid}  ', '  Name  ', actor=user)
    await user.refresh_from_db()
    assert getattr(user, f'{prefix}_user_id') == pid
    assert getattr(user, f'{prefix}_username') == 'Name'
