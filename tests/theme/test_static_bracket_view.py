"""The static (socket-free) bracket documents.

Three things worth pinning, none of which a screenshot would catch reliably:

* the documents carry **no NiceGUI/Quasar payload and open no socket** — that is
  the entire reason the surface exists, and one stray ``ui.`` helper or an
  inherited ``<script src>`` would quietly reintroduce the cost;
* **entrant names are escaped**. This is the one bracket renderer that builds
  markup by hand rather than through ``ui.label``, so the XSS guarantee is a
  property of this module and has to be asserted here;
* it renders the **same** classes and structure as the interactive view, so one
  stylesheet keeps serving both.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from application.services.match.match_status import MatchStatus
from models import (
    BracketEntryStatus,
    BracketFormat,
    BracketMatchState,
    BracketState,
)
from theme.brackets.static_view import (
    StaticBracketView,
    StaticIndexView,
    render_bracket_document,
    render_index_document,
)


def _entry(entry_id: int, seed=None, status=BracketEntryStatus.ACTIVE, group=None):
    return SimpleNamespace(
        id=entry_id, seed=seed, entrant_id=entry_id, group_number=group, status=status,
    )


def _match(
    match_id, rnd, position, e1, e2, *, winner=None, state=BracketMatchState.PENDING,
    winner_to=None, winner_slot=None, group=None, s1=None, s2=None, forfeit=False,
):
    return SimpleNamespace(
        id=match_id, round=rnd, position=position, entry1_id=e1, entry2_id=e2,
        winner_id=winner, entry1_score=s1, entry2_score=s2, state=state,
        forfeit=forfeit, winner_to_id=winner_to, winner_to_slot=winner_slot,
        loser_to_id=None, loser_to_slot=None, group_number=group,
    )


def _bracket(fmt=BracketFormat.SINGLE_ELIM, state=BracketState.ACTIVE, config=None):
    return SimpleNamespace(
        id=7, tournament_id=3, name='Main Event', stage_order=0, format=fmt,
        state=state, config=config or {},
    )


def _elim_view(**kwargs):
    matches = [
        _match(1, 1, 1, 1, 4, winner=1, state=BracketMatchState.COMPLETE,
               winner_to=3, winner_slot=1),
        _match(2, 1, 2, 2, 3, state=BracketMatchState.OPEN, winner_to=3, winner_slot=2),
        _match(3, 2, 1, 1, None),
    ]
    defaults = dict(
        bracket=_bracket(),
        tournament_name='Spring Cup',
        entries=[_entry(i, seed=i) for i in (1, 2, 3, 4)],
        matches=matches,
        entry_name={1: 'Alice', 2: 'Bob', 3: 'Cara', 4: 'Dana'},
        generated_at=datetime(2026, 6, 1, 18, 0, tzinfo=timezone.utc),
    )
    defaults.update(kwargs)
    return StaticBracketView(**defaults)


class TestNoFrameworkPayload:
    """The point of the surface: a document that costs one request and stops."""

    def test_loads_no_nicegui_or_quasar_assets(self):
        html = render_bracket_document(_elim_view())
        assert '_nicegui' not in html
        assert 'socket.io' not in html
        assert html.count('<link rel="stylesheet"') == 2
        assert 'quasar' not in html.lower().split('</head>')[1]

    def test_the_only_script_is_the_inline_one(self):
        html = render_bracket_document(_elim_view())
        assert '<script src' not in html
        assert html.count('<script>') == 1

    def test_it_refreshes_itself_instead_of_subscribing(self):
        html = render_bracket_document(_elim_view(refresh_seconds=45))
        assert '<meta http-equiv="refresh" content="45">' in html

    def test_the_pages_stay_out_of_search_indexes(self):
        assert 'noindex' in render_bracket_document(_elim_view())
        assert 'noindex' in render_index_document(
            StaticIndexView(tournament_id=3, tournament_name='Spring Cup',
                            brackets=[_bracket()]),
        )


class TestEscaping:
    @pytest.mark.parametrize('field_value', [
        '<script>alert(1)</script>',
        '" onload="alert(1)',
        "<img src=x onerror=alert(1)>",
    ])
    def test_entrant_names_are_escaped(self, field_value):
        html = render_bracket_document(
            _elim_view(entry_name={1: field_value, 2: 'B', 3: 'C', 4: 'D'}),
        )
        assert field_value not in html
        assert '&lt;' in html or '&quot;' in html

    def test_bracket_and_tournament_names_are_escaped(self):
        html = render_bracket_document(_elim_view(
            bracket=_bracket(config={}),
            tournament_name='<b>Cup</b>',
        ))
        assert '<b>Cup</b>' not in html
        assert '&lt;b&gt;Cup&lt;/b&gt;' in html


class TestAvatars:
    """Discord avatars layer over the initial disc; the letter is the fallback."""

    def test_an_avatar_is_layered_over_the_initial(self):
        html = render_bracket_document(_elim_view(
            entry_avatar={1: 'https://cdn.discordapp.com/avatars/9/h.png?size=64'},
        ))
        assert 'class="bracket-avatar-img"' in html
        assert 'src="https://cdn.discordapp.com/avatars/9/h.png?size=64"' in html
        # The letter stays in the DOM so a stale hash degrades to it.
        assert 'onerror="this.remove()"' in html

    def test_entrants_without_one_render_only_the_letter(self):
        html = render_bracket_document(_elim_view())
        assert 'bracket-avatar-img' not in html

    def test_a_url_that_is_not_a_plain_https_link_is_dropped(self):
        html = render_bracket_document(_elim_view(
            entry_avatar={1: 'javascript:alert(1)', 2: 'https://x/" onload="alert(1)'},
        ))
        assert 'javascript:' not in html
        assert 'onload=' not in html


class TestEliminationMarkup:
    def test_renders_both_the_2d_bracket_and_the_phone_list(self):
        """The stylesheet's media query picks one — so both must be in the DOM."""
        html = render_bracket_document(_elim_view())
        assert 'class="bracket-2d"' in html
        assert 'class="bracket-mobile-list"' in html
        assert '<details class="bracket-round-accordion"' in html

    def test_cards_carry_the_shared_state_and_slot_classes(self):
        html = render_bracket_document(_elim_view())
        assert 'bracket-match is-complete' in html
        assert 'bracket-match is-open' in html
        assert 'bracket-slot is-winner' in html
        assert 'bracket-slot is-loser' in html
        assert 'data-entrant-id="1"' in html

    def test_an_empty_slot_says_where_its_entrant_comes_from(self):
        html = render_bracket_document(_elim_view())
        assert 'Winner of 2' in html

    def test_a_live_matchup_links_the_watch_url(self):
        html = render_bracket_document(_elim_view(live_state={
            2: {'status': MatchStatus.LIVE, 'watch_url': 'https://twitch.tv/x'},
        }))
        assert 'bracket-live-pill' in html
        assert 'href="https://twitch.tv/x"' in html
        assert 'rel="noopener noreferrer"' in html

    def test_a_watch_url_that_is_not_http_is_not_linked(self):
        html = render_bracket_document(_elim_view(live_state={
            2: {'status': MatchStatus.LIVE, 'watch_url': 'javascript:alert(1)'},
        }))
        assert 'bracket-live-pill' in html
        assert 'javascript:' not in html

    def test_a_draft_stage_shows_the_seeded_field_not_a_bracket(self):
        html = render_bracket_document(_elim_view(
            bracket=_bracket(state=BracketState.DRAFT),
        ))
        assert 'This stage has not started yet.' in html
        assert 'Seeded entrants' in html
        assert 'bracket-canvas' not in html


class TestGroupAndSwissMarkup:
    def _rr_view(self):
        matches = [
            _match(1, 1, 1, 1, 2, winner=1, state=BracketMatchState.COMPLETE, group=1),
            _match(2, 1, 2, 3, 4, winner=3, state=BracketMatchState.COMPLETE, group=1),
            _match(3, 2, 1, 1, 3, winner=1, state=BracketMatchState.COMPLETE, group=1),
        ]
        return _elim_view(
            bracket=_bracket(fmt=BracketFormat.ROUND_ROBIN, config={'group_count': 1}),
            entries=[_entry(i, seed=i, group=1) for i in (1, 2, 3, 4)],
            matches=matches,
            advancement={'count': 2, 'per_group': True},
        )

    def test_round_robin_renders_standings_with_the_cut_line(self):
        html = render_bracket_document(self._rr_view())
        assert 'bracket-standings' in html
        assert 'is-advancing' in html
        assert 'cut-below' in html
        assert 'bracket-crosstable' in html

    def test_swiss_renders_standings_and_per_round_pairings(self):
        matches = [
            _match(1, 1, 1, 1, 2, winner=1, state=BracketMatchState.COMPLETE, s1=2, s2=0),
            _match(2, 1, 2, 3, 4, winner=4, state=BracketMatchState.COMPLETE, s1=1, s2=2),
            _match(3, 2, 1, 1, 4, state=BracketMatchState.OPEN),
        ]
        html = render_bracket_document(_elim_view(
            bracket=_bracket(
                fmt=BracketFormat.SWISS,
                config={'tiebreakers': ['buchholz'], 'swiss_rounds': 3},
            ),
            matches=matches,
            advancement={'count': 2},
        ))
        assert 'bracket-standings' in html
        assert 'bracket-pairings' in html
        # Tabs need client state; the static twin stacks the rounds instead.
        assert 'Round 1' in html and 'Round 2' in html
        assert '2–0' in html

    def test_a_dropped_entrant_is_marked_in_swiss_standings(self):
        html = render_bracket_document(_elim_view(
            bracket=_bracket(fmt=BracketFormat.SWISS, config={}),
            entries=[
                _entry(1, seed=1),
                _entry(2, seed=2, status=BracketEntryStatus.DROPPED),
            ],
            matches=[_match(1, 1, 1, 1, 2, winner=1, state=BracketMatchState.COMPLETE)],
        ))
        assert 'is-dropped' in html


class TestLinks:
    def test_links_carry_the_tenant_root_path(self):
        html = render_bracket_document(_elim_view(root_path='/t/demo'))
        assert 'href="/t/demo/live/tournament/3/brackets"' in html
        assert 'href="/t/demo/brackets/7"' in html

    def test_the_index_links_each_stage_to_its_static_view(self):
        html = render_index_document(StaticIndexView(
            tournament_id=3, tournament_name='Spring Cup',
            brackets=[_bracket()], root_path='/t/demo',
        ))
        assert 'href="/t/demo/live/brackets/7"' in html
        assert 'Main Event' in html

    def test_an_empty_index_says_so(self):
        html = render_index_document(StaticIndexView(
            tournament_id=3, tournament_name='Spring Cup', brackets=[],
        ))
        assert 'No brackets have been published' in html


class TestTenantTheme:
    def test_the_brand_primary_reaches_the_bracket_accent(self):
        html = render_bracket_document(_elim_view(primary_color='#123456'))
        assert '--q-primary:#123456' in html

    def test_no_palette_block_without_a_configured_primary(self):
        html = render_bracket_document(_elim_view(primary_color=None))
        assert '--q-primary' not in html

    def test_a_primary_that_is_not_a_hex_colour_is_dropped(self):
        html = render_bracket_document(_elim_view(primary_color='red;}body{background:url(x)}'))
        assert '--q-primary' not in html
        assert 'background:url' not in html
