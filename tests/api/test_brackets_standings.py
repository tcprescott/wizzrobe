"""REST API tests for bracket standings and the advance dry run.

Split from ``test_brackets_management.py`` when that module approached the
file-length guideline. Both halves are the same surface read from different
ends: management *changes* a bracket, this asks what the bracket currently says
— the group tables a round-robin produces, their tie-breaks, and the preview of
who would advance if the stage closed now. Setup helpers are shared through
``tests.api._bracket_api``.
"""

from tests.api._bracket_api import draft, enroll, staff_token, tournament
from tests.api_helpers import client_for, create_user_token


class TestStandings:
    async def _round_robin(self, c, tournament, names, *, group_count=1):
        bracket_id = await draft(
            c, tournament, name='Groups', format='round_robin',
            config={'group_count': group_count},
        )
        await enroll(c, bracket_id, tournament, names)
        await c.post(f'/api/brackets/{bracket_id}/start')
        return bracket_id

    async def _play_all(self, c, bracket_id, *, always_win=None):
        """Report every match, letting ``always_win`` (an entry id) win its games."""
        for m in (await c.get(f'/api/brackets/{bracket_id}/matches')).json():
            slots = (m['entry1_id'], m['entry2_id'])
            pick = always_win if always_win in slots else m['entry1_id']
            await c.post(f"/api/brackets/matches/{m['id']}/result", json={
                'winner_entry_id': pick,
            })

    async def test_standings_before_any_result(self, db, app):
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            bracket_id = await self._round_robin(
                c, t, ('Alice', 'Bob', 'Carol', 'Dave'),
            )
            groups = (await c.get(f'/api/brackets/{bracket_id}/standings')).json()
            assert len(groups) == 1
            rows = groups[0]['rows']
            assert len(rows) == 4
            assert {row['points'] for row in rows} == {0.0}
            assert {row['rank'] for row in rows} == {1}
            assert {row['display_name'] for row in rows} == {
                'Alice', 'Bob', 'Carol', 'Dave',
            }
            assert all(row['status'] == 'active' for row in rows)
            assert all(row['entrant_status'] == 'active' for row in rows)

    async def test_standings_rank_the_winner_first(self, db, app):
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            bracket_id = await self._round_robin(
                c, t, ('Alice', 'Bob', 'Carol', 'Dave'),
            )
            matches = (await c.get(f'/api/brackets/{bracket_id}/matches')).json()
            winner = matches[0]['entry1_id']
            await self._play_all(c, bracket_id, always_win=winner)

            rows = (await c.get(f'/api/brackets/{bracket_id}/standings')).json()[0]['rows']
            top = rows[0]
            assert top['entry_id'] == winner
            assert top['rank'] == 1
            assert top['wins'] == 3
            assert top['losses'] == 0
            assert top['points'] == 3.0
            assert 'buchholz' in top['tiebreakers']
            assert [row['rank'] for row in rows] == sorted(row['rank'] for row in rows)
            # final_rank is only persisted once the stage completes.
            assert all(row['final_rank'] is None for row in rows)

            assert (await c.post(f'/api/brackets/{bracket_id}/complete')).status_code == 200
            rows = (await c.get(f'/api/brackets/{bracket_id}/standings')).json()[0]['rows']
            assert rows[0]['final_rank'] == 1

    async def test_standings_per_group(self, db, app):
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            bracket_id = await self._round_robin(
                c, t, ('Alice', 'Bob', 'Carol', 'Dave'), group_count=2,
            )
            groups = (await c.get(f'/api/brackets/{bracket_id}/standings')).json()
            assert len(groups) == 2
            assert all(len(g['rows']) == 2 for g in groups)
            numbers = [g['group_number'] for g in groups]
            assert numbers == sorted(numbers)
            entry_ids = [row['entry_id'] for g in groups for row in g['rows']]
            assert len(set(entry_ids)) == 4

    async def test_dropped_entrant_stays_in_the_table(self, db, app):
        """A roster drop keeps the row (its results still count) and is visible.

        The two drop levels are independent: `drop_entrant` writes the roster
        state only, so the row's stage-entry `status` stays ACTIVE while
        `entrant_status` flips to DROPPED.
        """
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            bracket_id = await self._round_robin(c, t, ('Alice', 'Bob', 'Carol', 'Dave'))
            entries = (await c.get(f'/api/brackets/{bracket_id}/entries')).json()
            dropped_entry = entries[0]
            await c.post(f"/api/brackets/entrants/{dropped_entry['entrant_id']}/drop")

            rows = (await c.get(f'/api/brackets/{bracket_id}/standings')).json()[0]['rows']
            assert len(rows) == 4
            row = next(r for r in rows if r['entry_id'] == dropped_entry['id'])
            assert row['entrant_status'] == 'dropped'
            assert row['status'] == 'active'
            assert all(
                r['entrant_status'] == 'active'
                for r in rows if r['entry_id'] != dropped_entry['id']
            )

    async def test_swiss_standings_single_group(self, db, app):
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            bracket_id = await draft(c, t, name='Swiss', format='swiss')
            await enroll(c, bracket_id, t, ('Alice', 'Bob', 'Carol', 'Dave'))
            await c.post(f'/api/brackets/{bracket_id}/start')

            groups = (await c.get(f'/api/brackets/{bracket_id}/standings')).json()
            assert len(groups) == 1
            assert groups[0]['group_number'] is None
            assert len(groups[0]['rows']) == 4

    async def test_elimination_standings_rejected(self, db, app):
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            bracket_id = await draft(c, t)
            r = await c.get(f'/api/brackets/{bracket_id}/standings')
            assert r.status_code == 400
            assert 'round-robin' in r.json()['detail']

    async def test_standings_missing_bracket_404(self, db, app):
        _, staff = await staff_token()
        async with client_for(app, staff) as c:
            assert (await c.get('/api/brackets/999999/standings')).status_code == 404

    async def test_standings_readable_with_role_less_token(self, db, app):
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            bracket_id = await self._round_robin(c, t, ('Alice', 'Bob'))
        _, plain = await create_user_token(username='plain')
        async with client_for(app, plain) as c:
            r = await c.get(f'/api/brackets/{bracket_id}/standings')
            assert r.status_code == 200


class TestAdvancingPreview:
    async def test_preview_matches_the_advance(self, db, app):
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            groups_id = await draft(
                c, t, name='Groups', format='round_robin', stage_order=0,
                config={'group_count': 1},
            )
            await draft(
                c, t, name='Playoffs', format='single_elim', stage_order=1,
                config={'advancement': {'count': 2}},
            )
            await enroll(c, groups_id, t, ('Alice', 'Bob', 'Carol', 'Dave'))
            await c.post(f'/api/brackets/{groups_id}/start')

            early = await c.get(
                f'/api/brackets/advancing-preview'
                f'?tournament_id={t.id}&from_stage_order=0'
            )
            assert early.status_code == 400

            for m in (await c.get(f'/api/brackets/{groups_id}/matches')).json():
                await c.post(f"/api/brackets/matches/{m['id']}/result", json={
                    'winner_entry_id': m['entry1_id'],
                })
            await c.post(f'/api/brackets/{groups_id}/complete')

            preview = await c.get(
                f'/api/brackets/advancing-preview'
                f'?tournament_id={t.id}&from_stage_order=0'
            )
            assert preview.status_code == 200
            assert [row['final_rank'] for row in preview.json()] == [1, 2]
            previewed = [row['entrant_id'] for row in preview.json()]

            advanced = await c.post(
                f'/api/brackets/advance-stage?tournament_id={t.id}',
                json={'from_stage_order': 0},
            )
            assert advanced.status_code == 200
            next_entries = (await c.get(
                f"/api/brackets/{advanced.json()['id']}/entries"
            )).json()
            assert sorted(row['entrant_id'] for row in next_entries) == sorted(previewed)

    async def test_preview_missing_stage_404(self, db, app):
        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            r = await c.get(
                f'/api/brackets/advancing-preview'
                f'?tournament_id={t.id}&from_stage_order=7'
            )
            assert r.status_code == 404

    async def test_preview_is_staff_only(self, db, app):
        """The next stage is a draft, so even its existence is staff-only."""
        _, plain = await create_user_token(username='plain')
        t = await tournament()
        async with client_for(app, plain) as c:
            r = await c.get(
                f'/api/brackets/advancing-preview'
                f'?tournament_id={t.id}&from_stage_order=0'
            )
            assert r.status_code == 403


class TestMatchupInUnpublishedStage:
    async def test_a_cancelled_stage_matchup_reads_as_absent(self, db, app):
        from models import Bracket, BracketState

        _, staff = await staff_token()
        t = await tournament()
        async with client_for(app, staff) as c:
            stage = await draft(c, t, name='Main', format='single_elim')
            await enroll(c, stage, t, ('Alice', 'Bob'))
            await c.post(f'/api/brackets/{stage}/start')
            match_id = (await c.get(f'/api/brackets/{stage}/matches')).json()[0]['id']
        await Bracket.filter(id=stage).update(state=BracketState.CANCELLED)

        _, plain = await create_user_token(username='plain')
        async with client_for(app, plain) as c:
            assert (await c.get(f'/api/brackets/matches/{match_id}')).status_code == 404
        async with client_for(app, staff) as c:
            assert (await c.get(f'/api/brackets/matches/{match_id}')).status_code == 200
