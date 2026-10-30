"""The checkout dialog's option list, given a set of people.

Building the dialog needs a live NiceGUI client, so what is locked here is the
pure part: which people become selectable. The two hard exclusions live in the
scoped read *and* here, because the "include people outside this community"
toggle refills from the unscoped list — that toggle widens which community's
people are offered, never who may borrow.
"""

from models import SYSTEM_USER_DISCORD_ID, User
from theme.dialog.checkout_dialog import _borrower_options


def _person(user_id: int, name: str, *, discord_id: int = 500, is_active: bool = True) -> User:
    return User(id=user_id, username=name, discord_id=discord_id, is_active=is_active)


class TestBorrowerOptions:
    def test_offers_people_by_id_and_preferred_name(self):
        options = _borrower_options([_person(1, 'alice'), _person(2, 'bob', discord_id=501)])
        assert options == {'1': 'alice', '2': 'bob'}

    def test_the_system_account_is_never_selectable(self):
        options = _borrower_options([
            _person(1, 'alice'),
            _person(2, 'System', discord_id=SYSTEM_USER_DISCORD_ID),
        ])
        assert options == {'1': 'alice'}

    def test_a_deactivated_account_is_never_selectable(self):
        options = _borrower_options([
            _person(1, 'alice'),
            _person(2, 'departed', discord_id=502, is_active=False),
        ])
        assert options == {'1': 'alice'}

    def test_an_empty_community_produces_an_empty_picker(self):
        assert _borrower_options([]) == {}

    def test_the_exclusions_hold_for_the_widened_list_too(self):
        # The escape hatch refills from get_all_users(), which is unscoped and
        # includes the service account — this is the guard that keeps it out.
        platform = [
            _person(1, 'alice'),
            _person(2, 'System', discord_id=SYSTEM_USER_DISCORD_ID),
            _person(3, 'departed', discord_id=503, is_active=False),
            _person(4, 'walk_in', discord_id=504),
        ]
        assert _borrower_options(platform) == {'1': 'alice', '4': 'walk_in'}
