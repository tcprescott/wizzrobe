from types import SimpleNamespace

from application.services.tournament_service import TournamentSignupCard
from application.utils.tournament_signup import SignupWindow
from pages.home_tabs.tournaments import bracket_link


def _card(**kwargs):
    t = SimpleNamespace(id=7, challonge_tournament_url=kwargs.pop('challonge_url', None),
                        bracket_url=kwargs.pop('bracket_url', None))
    return TournamentSignupCard(tournament=t, window=SignupWindow.CLOSED, enrolled=False,
                                entrant_count=0, **kwargs)


def test_challonge_card_links_to_challonge_in_a_new_tab():
    card = _card(challonge_managed=True, challonge_url='https://challonge.com/sgl26')
    assert bracket_link(card, set()) == ('https://challonge.com/sgl26', True)


def test_native_bracket_stays_in_app():
    assert bracket_link(_card(bracket_url='https://example.com/b'), {7}) == ('/tournament/7/brackets', False)


def test_falls_back_to_the_tournament_bracket_url():
    assert bracket_link(_card(bracket_url='https://example.com/b'), set()) == ('https://example.com/b', True)


def test_startgg_card_keeps_its_own_button():
    card = _card(startgg_url='https://www.start.gg/x', bracket_url='https://example.com/b')
    assert bracket_link(card, set()) is None


def test_no_bracket_no_button():
    assert bracket_link(_card(bracket_url='javascript:alert(1)'), set()) is None
