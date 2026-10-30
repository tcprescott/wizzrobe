"""REST API tests for the bracket management endpoints (api/routers/brackets.py).

Peer of ``test_api_brackets.py``, which covers the create → start → report →
complete happy path and the auth matrix. This module covers the management and
correction surface layered on top: editing a DRAFT bracket, round metadata,
reseeding, overriding a reported result, the single-match read, a player's own
open matchups, and stage close-out. Standings and the advance dry run moved to
``test_brackets_standings.py``; the tournament-level roster lives in
``test_brackets_roster.py``; the setup helpers all four share are in
``tests/api/_bracket_api.py``.
"""

from models import Role
from tests.api._bracket_api import draft, enroll, staff_token, token_for, tournament
from tests.api_helpers import client_for, create_user_token
from tests.factories import make_member


class TestBracketEditing:
    async def test_update_bracket(self, db, app):
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            bracket_id = await draft(c, t)

            r = await c.patch(f'/api/brackets/{bracket_id}', json={'name': 'Playoffs'})
            assert r.status_code == 200
            assert r.json()['name'] == 'Playoffs'

            r = await c.patch(f'/api/brackets/{bracket_id}', json={'stage_order': 3})
            assert r.status_code == 200
            assert r.json()['name'] == 'Playoffs'
            assert r.json()['stage_order'] == 3

    async def test_update_rejects_blank_name(self, db, app):
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            bracket_id = await draft(c, t)
            r = await c.patch(f'/api/brackets/{bracket_id}', json={'name': '  '})
            assert r.status_code == 400

    async def test_update_rejects_started_bracket(self, db, app):
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            bracket_id = await draft(c, t)
            await enroll(c, bracket_id, t, ('Alice', 'Bob'))
            await c.post(f'/api/brackets/{bracket_id}/start')

            r = await c.patch(f'/api/brackets/{bracket_id}', json={'name': 'Nope'})
            assert r.status_code == 400

    async def test_update_changes_format_while_draft(self, db, app):
        """A DRAFT stage has no match graph, so the format is still a decision."""
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            bracket_id = await draft(c, t)
            r = await c.patch(f'/api/brackets/{bracket_id}', json={'format': 'swiss'})
            assert r.status_code == 200
            assert r.json()['format'] == 'swiss'

    async def test_update_rejects_unknown_format(self, db, app):
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            bracket_id = await draft(c, t)
            r = await c.patch(f'/api/brackets/{bracket_id}', json={'format': 'ladder'})
            assert r.status_code == 422

    async def test_update_rejects_taken_stage_order(self, db, app):
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            await draft(c, t, name='Groups', stage_order=0)
            second = await draft(c, t, name='Playoffs', stage_order=1)
            r = await c.patch(f'/api/brackets/{second}', json={'stage_order': 0})
            assert r.status_code == 400

    async def test_delete_bracket(self, db, app):
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            bracket_id = await draft(c, t)
            assert (await c.delete(f'/api/brackets/{bracket_id}')).status_code == 204
            assert (await c.get(f'/api/brackets/{bracket_id}')).status_code == 404

    async def test_delete_rejects_started_bracket(self, db, app):
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            bracket_id = await draft(c, t)
            await enroll(c, bracket_id, t, ('Alice', 'Bob'))
            await c.post(f'/api/brackets/{bracket_id}/start')
            assert (await c.delete(f'/api/brackets/{bracket_id}')).status_code == 400

    async def test_delete_read_only_forbidden(self, db, app):
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            bracket_id = await draft(c, t)
        _, ro = await create_user_token(username='ro', roles=[Role.STAFF], read_only=True)
        async with client_for(app, ro) as c:
            assert (await c.delete(f'/api/brackets/{bracket_id}')).status_code == 403

    async def test_delete_missing_bracket_404(self, db, app):
        _, staff = await staff_token()
        async with client_for(app, staff) as c:
            assert (await c.delete('/api/brackets/999999')).status_code == 404


class TestRoundMetadata:
    async def test_round_metadata_round_trips_and_clears(self, db, app):
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            bracket_id = await draft(c, t)

            r = await c.put(f'/api/brackets/{bracket_id}/rounds', json={
                'rounds': {'1': {'best_of': 3, 'scheduled_at': '2026-06-12T18:00:00'}},
            })
            assert r.status_code == 200
            assert r.json()['config']['rounds']['1']['best_of'] == 3

            cleared = await c.put(
                f'/api/brackets/{bracket_id}/rounds', json={'rounds': None},
            )
            assert cleared.status_code == 200
            assert 'rounds' not in (cleared.json()['config'] or {})

    async def test_round_metadata_editable_after_start(self, db, app):
        """Round chrome is display-only, so unlike PATCH it survives ACTIVE."""
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            bracket_id = await draft(c, t)
            await enroll(c, bracket_id, t, ('Alice', 'Bob'))
            await c.post(f'/api/brackets/{bracket_id}/start')

            r = await c.put(f'/api/brackets/{bracket_id}/rounds', json={
                'rounds': {'1': {'best_of': 5}},
            })
            assert r.status_code == 200
            assert r.json()['config']['rounds']['1']['best_of'] == 5

    async def test_round_metadata_rejects_even_best_of(self, db, app):
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            bracket_id = await draft(c, t)
            r = await c.put(f'/api/brackets/{bracket_id}/rounds', json={
                'rounds': {'1': {'best_of': 2}},
            })
            assert r.status_code == 400


class TestSeeds:
    async def test_set_seeds(self, db, app):
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            bracket_id = await draft(c, t)
            first, second = await enroll(c, bracket_id, t, ('Alice', 'Bob'))

            r = await c.patch(f'/api/brackets/{bracket_id}/seeds', json={
                'seeds': {str(first): 2, str(second): 1},
            })
            assert r.status_code == 200
            assert [row['seed'] for row in r.json()] == [1, 2]
            assert [row['id'] for row in r.json()] == [second, first]

    async def test_set_seeds_rejects_duplicate_and_writes_nothing(self, db, app):
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            bracket_id = await draft(c, t)
            first, second = await enroll(c, bracket_id, t, ('Alice', 'Bob'))

            bad = await c.patch(f'/api/brackets/{bracket_id}/seeds', json={
                'seeds': {str(first): 1, str(second): 1},
            })
            assert bad.status_code == 400
            entries = (await c.get(f'/api/brackets/{bracket_id}/entries')).json()
            assert all(row['seed'] is None for row in entries)

    async def test_set_seeds_rejects_zero(self, db, app):
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            bracket_id = await draft(c, t)
            first, _second = await enroll(c, bracket_id, t, ('Alice', 'Bob'))
            r = await c.patch(f'/api/brackets/{bracket_id}/seeds', json={
                'seeds': {str(first): 0},
            })
            assert r.status_code == 400

    async def test_set_seeds_rejects_foreign_entry(self, db, app):
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            a = await draft(c, t, name='A', stage_order=0)
            b = await draft(c, t, name='B', stage_order=1)
            (entry_in_a,) = await enroll(c, a, t, ('Alice',))
            r = await c.patch(f'/api/brackets/{b}/seeds', json={
                'seeds': {str(entry_in_a): 1},
            })
            assert r.status_code == 400

    async def test_set_seeds_read_only_forbidden(self, db, app):
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            bracket_id = await draft(c, t)
            (entry_id,) = await enroll(c, bracket_id, t, ('Alice',))
        _, ro = await create_user_token(username='ro', roles=[Role.STAFF], read_only=True)
        async with client_for(app, ro) as c:
            r = await c.patch(f'/api/brackets/{bracket_id}/seeds', json={
                'seeds': {str(entry_id): 1},
            })
            assert r.status_code == 403


class TestMatchRead:
    async def test_get_match_with_games(self, db, app):
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            bracket_id = await draft(c, t)
            await enroll(c, bracket_id, t, ('Alice', 'Bob'))
            await c.post(f'/api/brackets/{bracket_id}/start')
            match_id = (await c.get(f'/api/brackets/{bracket_id}/matches')).json()[0]['id']

            r = await c.get(f'/api/brackets/matches/{match_id}')
            assert r.status_code == 200
            assert r.json()['id'] == match_id
            # Game rows are created lazily, so an unscheduled series has none.
            assert r.json()['games'] == []

    async def test_get_match_not_found(self, db, app):
        _, staff = await staff_token()
        async with client_for(app, staff) as c:
            assert (await c.get('/api/brackets/matches/999999')).status_code == 404

    async def test_get_match_readable_with_role_less_token(self, db, app):
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            bracket_id = await draft(c, t)
            await enroll(c, bracket_id, t, ('Alice', 'Bob'))
            await c.post(f'/api/brackets/{bracket_id}/start')
            match_id = (await c.get(f'/api/brackets/{bracket_id}/matches')).json()[0]['id']
        _, plain = await create_user_token(username='plain')
        async with client_for(app, plain) as c:
            assert (await c.get(f'/api/brackets/matches/{match_id}')).status_code == 200


class TestMyOpenMatches:
    async def _linked_bracket(self, c, tournament, users):
        bracket_id = await draft(c, tournament)
        await enroll(
            c, bracket_id, tournament, ('Alice', 'Bob'),
            user_ids=[u.id for u in users],
        )
        await c.post(f'/api/brackets/{bracket_id}/start')
        return bracket_id

    async def test_lists_only_the_callers_matchups(self, db, app):
        _, staff = await staff_token()
        t = await tournament()
        alice = await make_member(discord_id=9301, username='alice')
        bob = await make_member(discord_id=9302, username='bob')
        async with client_for(app, staff) as c:
            await self._linked_bracket(c, t, (alice, bob))

        _, outsider = await create_user_token(username='outsider')
        async with client_for(app, outsider) as c:
            assert (await c.get('/api/brackets/my-open-matches')).json() == []

        async with client_for(app, await token_for(alice)) as c:
            r = await c.get('/api/brackets/my-open-matches')
            assert r.status_code == 200
            assert len(r.json()) == 1

    async def test_tournament_filter(self, db, app):
        _, staff = await staff_token()
        t = await tournament()
        alice = await make_member(discord_id=9311, username='alice')
        bob = await make_member(discord_id=9312, username='bob')
        async with client_for(app, staff) as c:
            await self._linked_bracket(c, t, (alice, bob))

        async with client_for(app, await token_for(alice)) as c:
            same = await c.get(f'/api/brackets/my-open-matches?tournament_id={t.id}')
            assert len(same.json()) == 1
            other = await c.get('/api/brackets/my-open-matches?tournament_id=999999')
            assert other.json() == []

    async def test_unlinked_entrants_are_excluded(self, db, app):
        """A matchup is only listed when *both* entrants resolve to a user."""
        _, staff = await staff_token()
        t = await tournament()
        alice = await make_member(discord_id=9321, username='alice')
        async with client_for(app, staff) as c:
            bracket_id = await draft(c, t)
            entrant = (await c.post('/api/brackets/entrants', json={
                'tournament_id': t.id, 'display_name': 'Alice', 'user_id': alice.id,
            })).json()
            await c.post(f'/api/brackets/{bracket_id}/entries', json={
                'entrant_id': entrant['id'],
            })
            unlinked = (await c.post('/api/brackets/entrants', json={
                'tournament_id': t.id, 'display_name': 'Bob',
            })).json()
            await c.post(f'/api/brackets/{bracket_id}/entries', json={
                'entrant_id': unlinked['id'],
            })
            await c.post(f'/api/brackets/{bracket_id}/start')

        async with client_for(app, await token_for(alice)) as c:
            assert (await c.get('/api/brackets/my-open-matches')).json() == []

    async def test_unauthenticated_is_rejected(self, db, app):
        async with client_for(app, None) as c:
            assert (await c.get('/api/brackets/my-open-matches')).status_code == 401


class TestOverrideResult:
    async def _played(self, c, tournament):
        """A single-elim Bo1 played out; returns (bracket_id, the played match)."""
        bracket_id = await draft(c, tournament)
        await enroll(c, bracket_id, tournament, ('Alice', 'Bob'))
        await c.post(f'/api/brackets/{bracket_id}/start')
        match = (await c.get(f'/api/brackets/{bracket_id}/open-matches')).json()[0]
        await c.post(f"/api/brackets/matches/{match['id']}/result", json={
            'winner_entry_id': match['entry1_id'],
        })
        return bracket_id, match

    async def test_override_swaps_winner_and_reranks(self, db, app):
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            bracket_id, match = await self._played(c, t)

            r = await c.patch(f"/api/brackets/matches/{match['id']}/result", json={
                'winner_entry_id': match['entry2_id'],
                'entry1_score': 1, 'entry2_score': 3,
            })
            assert r.status_code == 200
            assert r.json()['winner_id'] == match['entry2_id']
            assert r.json()['entry1_score'] == 1
            assert r.json()['entry2_score'] == 3

            # The stage auto-finalized on the original result, so its ranks are
            # recomputed from the correction rather than left stale.
            entries = (await c.get(f'/api/brackets/{bracket_id}/entries')).json()
            champion = next(e for e in entries if e['final_rank'] == 1)
            assert champion['id'] == match['entry2_id']

    async def test_override_rejects_incomplete_match(self, db, app):
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            bracket_id = await draft(c, t)
            await enroll(c, bracket_id, t, ('Alice', 'Bob'))
            await c.post(f'/api/brackets/{bracket_id}/start')
            match = (await c.get(f'/api/brackets/{bracket_id}/open-matches')).json()[0]

            r = await c.patch(f"/api/brackets/matches/{match['id']}/result", json={
                'winner_entry_id': match['entry1_id'],
            })
            assert r.status_code == 400

    async def test_override_rejects_lower_winner_score(self, db, app):
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            _bracket_id, match = await self._played(c, t)
            r = await c.patch(f"/api/brackets/matches/{match['id']}/result", json={
                'winner_entry_id': match['entry2_id'],
                'entry1_score': 3, 'entry2_score': 0,
            })
            assert r.status_code == 400

            ff = await c.patch(f"/api/brackets/matches/{match['id']}/result", json={
                'winner_entry_id': match['entry2_id'],
                'entry1_score': 3, 'entry2_score': 0, 'forfeit': True,
            })
            assert ff.status_code == 200
            assert ff.json()['forfeit'] is True

    async def test_override_missing_match_404(self, db, app):
        _, staff = await staff_token()
        async with client_for(app, staff) as c:
            r = await c.patch('/api/brackets/matches/999999/result', json={
                'winner_entry_id': 1,
            })
            assert r.status_code == 404

    async def test_override_read_only_forbidden(self, db, app):
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            _bracket_id, match = await self._played(c, t)
        _, ro = await create_user_token(username='ro', roles=[Role.STAFF], read_only=True)
        async with client_for(app, ro) as c:
            r = await c.patch(f"/api/brackets/matches/{match['id']}/result", json={
                'winner_entry_id': match['entry2_id'],
            })
            assert r.status_code == 403



class TestCancelAndEntryRemoval:
    async def _started(self, c, tournament, names=('Alice', 'Bob')):
        bracket_id = await draft(c, tournament)
        entry_ids = await enroll(c, bracket_id, tournament, names)
        await c.post(f'/api/brackets/{bracket_id}/start')
        return bracket_id, entry_ids

    async def test_cancel_an_active_stage(self, db, app):
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            bracket_id = await draft(c, t)
            await enroll(c, bracket_id, t, ('Alice', 'Bob', 'Cara'))
            await c.post(f'/api/brackets/{bracket_id}/start')

            r = await c.post(f'/api/brackets/{bracket_id}/cancel')
            assert r.status_code == 200
            assert r.json()['state'] == 'cancelled'

            assert (await c.delete(f'/api/brackets/{bracket_id}')).status_code == 204

    async def test_cancel_read_only_forbidden(self, db, app):
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            bracket_id = await draft(c, t)
        _, ro = await create_user_token(username='ro-cancel', roles=[Role.STAFF], read_only=True)
        async with client_for(app, ro) as c:
            assert (await c.post(f'/api/brackets/{bracket_id}/cancel')).status_code == 403

    async def test_unenroll_while_draft_then_refused_once_started(self, db, app):
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            bracket_id = await draft(c, t)
            entry_ids = await enroll(c, bracket_id, t, ('Alice', 'Bob', 'Cara'))

            assert (await c.delete(f'/api/brackets/entries/{entry_ids[0]}')).status_code == 204
            assert len((await c.get(f'/api/brackets/{bracket_id}/entries')).json()) == 2

            await c.post(f'/api/brackets/{bracket_id}/start')
            assert (await c.delete(f'/api/brackets/entries/{entry_ids[1]}')).status_code == 400

    async def test_retire_an_entry_mid_stage(self, db, app):
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            bracket_id, entry_ids = await self._started(c, t, ('Alice', 'Bob', 'Cara'))
            r = await c.post(f'/api/brackets/entries/{entry_ids[0]}/retire')
            assert r.status_code == 200
            assert r.json()['status'] == 'dropped'
            # Not idempotent by design — a second retire is a mistake worth saying.
            assert (await c.post(
                f'/api/brackets/entries/{entry_ids[0]}/retire'
            )).status_code == 400

    async def test_retire_missing_entry_404(self, db, app):
        _, staff = await staff_token()
        async with client_for(app, staff) as c:
            assert (await c.post('/api/brackets/entries/999999/retire')).status_code == 404

    async def test_complete_accepts_tie_breaks(self, db, app):
        """The argument the service always took and no caller ever passed."""
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            bracket_id = (await c.post('/api/brackets', json={
                'tournament_id': t.id, 'name': 'Swiss', 'format': 'round_robin',
            })).json()['id']
            entry_ids = await enroll(c, bracket_id, t, ('Alice', 'Bob'))
            await c.post(f'/api/brackets/{bracket_id}/start')
            match = (await c.get(f'/api/brackets/{bracket_id}/matches')).json()[0]
            await c.post(f"/api/brackets/matches/{match['id']}/result",
                         json={'winner_entry_id': match['entry1_id']})

            r = await c.post(
                f'/api/brackets/{bracket_id}/complete',
                json={'tie_breaks': {str(entry_ids[1]): 1, str(entry_ids[0]): 2}},
            )
            assert r.status_code == 200
            ranks = {
                row['id']: row['final_rank']
                for row in (await c.get(f'/api/brackets/{bracket_id}/entries')).json()
            }
            assert ranks[entry_ids[1]] == 1
            assert ranks[entry_ids[0]] == 2
