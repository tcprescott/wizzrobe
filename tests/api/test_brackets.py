"""REST API tests for native bracket endpoints (api/routers/brackets.py).

Brackets are tenant-scoped and Staff-gated in-service. Reads use the any-token
dep and stay role-agnostic; writes reject read-only tokens at the HTTP layer and
require Staff in the service. The router is feature-gated by
``FeatureFlag.BRACKETS`` (404 when the tenant lacks it).
"""

import pytest

from application.tenant_context import tenant_scope
from models import (
    Bracket,
    BracketFormat,
    BracketState,
    FeatureFlag,
    Role,
    TenantFeatureFlag,
    Tournament,
)
from tests.api_helpers import client_for, create_user_token, enable_all_features
from tests.factories import make_member


async def _staff_token(username='staff'):
    return await create_user_token(username=username, roles=[Role.STAFF])


async def _tournament(name='Cup'):
    return await Tournament.create(name=name)


class TestReads:
    async def test_list_unauthenticated(self, db, app):
        async with client_for(app) as c:
            resp = await c.get('/api/brackets?tournament_id=1')
            assert resp.status_code == 401

    async def test_list_role_less_ok(self, db, app):
        """Reads are open to a role-less token — for published stages."""
        _, staff = await _staff_token()
        t = await _tournament()
        async with client_for(app, staff) as c:
            await c.post('/api/brackets', json={
                'tournament_id': t.id, 'name': 'Main', 'format': 'single_elim',
            })
        await Bracket.filter(tournament_id=t.id).update(state=BracketState.ACTIVE)
        _, plain = await create_user_token(username='plain')
        async with client_for(app, plain) as c:
            resp = await c.get(f'/api/brackets?tournament_id={t.id}')
            assert resp.status_code == 200
            assert [b['name'] for b in resp.json()] == ['Main']

    async def test_get_not_found(self, db, app):
        _, staff = await _staff_token()
        async with client_for(app, staff) as c:
            resp = await c.get('/api/brackets/9999')
            assert resp.status_code == 404


class TestUnpublishedStagesAreStaffOnly:
    """The REST twin of the public pages' rule: a DRAFT or CANCELLED stage, and a
    tournament with nothing published, read as absent to a non-staff token.

    Before the rule moved into ``BracketService`` the pages filtered drafts and
    the API handed them, config and seeded field included, to any token.
    """

    async def _setup(self, app):
        _, staff = await _staff_token()
        t = await _tournament('Unannounced')
        async with client_for(app, staff) as c:
            draft = (await c.post('/api/brackets', json={
                'tournament_id': t.id, 'name': 'Draft', 'format': 'single_elim',
            })).json()
        _, plain = await create_user_token(username='plain')
        return t, draft['id'], staff, plain

    async def test_a_non_staff_token_sees_no_draft(self, db, app):
        t, draft_id, _staff, plain = await self._setup(app)
        async with client_for(app, plain) as c:
            assert (await c.get(f'/api/brackets?tournament_id={t.id}')).json() == []
            for path in (
                f'/api/brackets/{draft_id}',
                f'/api/brackets/{draft_id}/matches',
                f'/api/brackets/{draft_id}/open-matches',
                f'/api/brackets/{draft_id}/entries',
                f'/api/brackets/{draft_id}/standings',
                f'/api/brackets/entrants?tournament_id={t.id}',
            ):
                assert (await c.get(path)).status_code == 404, path

    async def test_staff_still_read_drafts(self, db, app):
        t, draft_id, staff, _plain = await self._setup(app)
        async with client_for(app, staff) as c:
            listed = (await c.get(f'/api/brackets?tournament_id={t.id}')).json()
            assert [b['id'] for b in listed] == [draft_id]
            assert (await c.get(f'/api/brackets/{draft_id}')).status_code == 200
            assert (await c.get(f'/api/brackets/entrants?tournament_id={t.id}')).status_code == 200

    async def test_a_cancelled_stage_is_withdrawn_too(self, db, app):
        t, draft_id, _staff, plain = await self._setup(app)
        await Bracket.filter(id=draft_id).update(state=BracketState.CANCELLED)
        async with client_for(app, plain) as c:
            assert (await c.get(f'/api/brackets/{draft_id}')).status_code == 404

    async def test_publishing_opens_it(self, db, app):
        t, draft_id, _staff, plain = await self._setup(app)
        await Bracket.filter(id=draft_id).update(state=BracketState.ACTIVE)
        async with client_for(app, plain) as c:
            assert (await c.get(f'/api/brackets/{draft_id}')).status_code == 200
            assert (await c.get(f'/api/brackets/entrants?tournament_id={t.id}')).status_code == 200



class TestWrites:
    async def test_create_role_less_forbidden(self, db, app):
        """A non-staff token can read but a write returns 403 (service gate)."""
        t = await _tournament()
        _, plain = await create_user_token(username='plain')
        async with client_for(app, plain) as c:
            resp = await c.post('/api/brackets', json={
                'tournament_id': t.id, 'name': 'X', 'format': 'single_elim',
            })
            assert resp.status_code == 403

    async def test_create_read_only_forbidden(self, db, app):
        """A staff read-only token is rejected on a write by require_write_actor."""
        t = await _tournament()
        _, ro = await create_user_token(username='ro', roles=[Role.STAFF], read_only=True)
        async with client_for(app, ro) as c:
            resp = await c.post('/api/brackets', json={
                'tournament_id': t.id, 'name': 'X', 'format': 'single_elim',
            })
            assert resp.status_code == 403

    async def test_create_bad_format(self, db, app):
        _, staff = await _staff_token()
        t = await _tournament()
        async with client_for(app, staff) as c:
            resp = await c.post('/api/brackets', json={
                'tournament_id': t.id, 'name': 'X', 'format': 'nope',
            })
            assert resp.status_code == 422


class TestHappyPath:
    async def test_create_list_get(self, db, app):
        _, staff = await _staff_token()
        t = await _tournament()
        async with client_for(app, staff) as c:
            created = await c.post('/api/brackets', json={
                'tournament_id': t.id, 'name': 'Main', 'format': 'single_elim',
            })
            assert created.status_code == 201
            body = created.json()
            assert body['name'] == 'Main'
            assert body['format'] == 'single_elim'
            assert body['state'] == 'draft'
            bracket_id = body['id']

            listed = await c.get(f'/api/brackets?tournament_id={t.id}')
            assert listed.status_code == 200
            assert [b['id'] for b in listed.json()] == [bracket_id]

            got = await c.get(f'/api/brackets/{bracket_id}')
            assert got.status_code == 200
            assert got.json()['id'] == bracket_id

    async def test_full_flow_report_result(self, db, app):
        _, staff = await _staff_token()
        t = await _tournament()
        async with client_for(app, staff) as c:
            bracket_id = (await c.post('/api/brackets', json={
                'tournament_id': t.id, 'name': 'Main', 'format': 'single_elim',
            })).json()['id']

            e1 = (await c.post('/api/brackets/entrants', json={
                'tournament_id': t.id, 'display_name': 'Alice',
            })).json()
            e2 = (await c.post('/api/brackets/entrants', json={
                'tournament_id': t.id, 'display_name': 'Bob',
            })).json()

            entrants = await c.get(f'/api/brackets/entrants?tournament_id={t.id}')
            assert entrants.status_code == 200
            assert {e['display_name'] for e in entrants.json()} == {'Alice', 'Bob'}

            for e in (e1, e2):
                enrolled = await c.post(f'/api/brackets/{bracket_id}/entries', json={
                    'entrant_id': e['id'],
                })
                assert enrolled.status_code == 201

            started = await c.post(f'/api/brackets/{bracket_id}/start')
            assert started.status_code == 200
            assert started.json()['state'] == 'active'

            entries = (await c.get(f'/api/brackets/{bracket_id}/entries')).json()
            entry_by_entrant = {e['entrant_id']: e['id'] for e in entries}

            open_matches = (await c.get(f'/api/brackets/{bracket_id}/open-matches')).json()
            assert len(open_matches) == 1
            match_id = open_matches[0]['id']
            winner_entry_id = entry_by_entrant[e1['id']]

            reported = await c.post(f'/api/brackets/matches/{match_id}/result', json={
                'winner_entry_id': winner_entry_id,
            })
            assert reported.status_code == 200
            assert reported.json()['winner_id'] == winner_entry_id
            assert reported.json()['state'] == 'complete'

            # The final resolving auto-completes the single-elim stage.
            final = await c.get(f'/api/brackets/{bracket_id}')
            assert final.json()['state'] == 'complete'

    async def test_report_result_with_scores_and_forfeit(self, db, app):
        _, staff = await _staff_token()
        t = await _tournament()
        async with client_for(app, staff) as c:
            bracket_id = (await c.post('/api/brackets', json={
                'tournament_id': t.id, 'name': 'Main', 'format': 'single_elim',
            })).json()['id']
            for name in ('Alice', 'Bob'):
                e = (await c.post('/api/brackets/entrants', json={
                    'tournament_id': t.id, 'display_name': name,
                })).json()
                await c.post(f'/api/brackets/{bracket_id}/entries', json={
                    'entrant_id': e['id'],
                })
            await c.post(f'/api/brackets/{bracket_id}/start')

            open_matches = (await c.get(f'/api/brackets/{bracket_id}/open-matches')).json()
            match_id = open_matches[0]['id']
            winner_entry_id = open_matches[0]['entry1_id']

            reported = await c.post(f'/api/brackets/matches/{match_id}/result', json={
                'winner_entry_id': winner_entry_id,
                'entry1_score': 3, 'entry2_score': 1,
            })
            assert reported.status_code == 200
            body = reported.json()
            assert body['entry1_score'] == 3
            assert body['entry2_score'] == 1
            assert body['forfeit'] is False

    async def test_report_result_rejects_lower_winner_score(self, db, app):
        _, staff = await _staff_token()
        t = await _tournament()
        async with client_for(app, staff) as c:
            bracket_id = (await c.post('/api/brackets', json={
                'tournament_id': t.id, 'name': 'Main', 'format': 'single_elim',
            })).json()['id']
            for name in ('Alice', 'Bob'):
                e = (await c.post('/api/brackets/entrants', json={
                    'tournament_id': t.id, 'display_name': name,
                })).json()
                await c.post(f'/api/brackets/{bracket_id}/entries', json={
                    'entrant_id': e['id'],
                })
            await c.post(f'/api/brackets/{bracket_id}/start')
            open_matches = (await c.get(f'/api/brackets/{bracket_id}/open-matches')).json()
            match_id = open_matches[0]['id']
            winner_entry_id = open_matches[0]['entry1_id']

            # Winner with the lower score is rejected unless forfeit is set.
            bad = await c.post(f'/api/brackets/matches/{match_id}/result', json={
                'winner_entry_id': winner_entry_id,
                'entry1_score': 0, 'entry2_score': 2,
            })
            assert bad.status_code == 400

            ff = await c.post(f'/api/brackets/matches/{match_id}/result', json={
                'winner_entry_id': winner_entry_id,
                'entry1_score': 0, 'entry2_score': 2, 'forfeit': True,
            })
            assert ff.status_code == 200
            assert ff.json()['forfeit'] is True


class TestDerivedStatus:
    """Match payloads carry the cross-surface derived status (U1/U2).

    The point of the shared vocabulary is that an API consumer, the web bracket,
    and a Discord DM all say the same word about the same matchup — so the API
    reporting it is part of the contract, not a convenience.
    """

    @staticmethod
    async def _started_bracket(c, tournament_id: int) -> int:
        bracket_id = (await c.post('/api/brackets', json={
            'tournament_id': tournament_id, 'name': 'Main', 'format': 'single_elim',
        })).json()['id']
        for name in ('Alice', 'Bob'):
            entrant = (await c.post('/api/brackets/entrants', json={
                'tournament_id': tournament_id, 'display_name': name,
            })).json()
            await c.post(f'/api/brackets/{bracket_id}/entries', json={
                'entrant_id': entrant['id'],
            })
        await c.post(f'/api/brackets/{bracket_id}/start')
        return bracket_id

    async def test_an_unbooked_matchup_reports_unscheduled(self, db, app):
        _, staff = await _staff_token()
        t = await _tournament()
        async with client_for(app, staff) as c:
            bracket_id = await self._started_bracket(c, t.id)

            matches = (await c.get(f'/api/brackets/{bracket_id}/matches')).json()
            assert [m['status'] for m in matches] == ['unscheduled']
            open_matches = (
                await c.get(f'/api/brackets/{bracket_id}/open-matches')
            ).json()
            assert [m['status'] for m in open_matches] == ['unscheduled']

    async def test_a_settled_matchup_reports_complete(self, db, app):
        _, staff = await _staff_token()
        t = await _tournament()
        async with client_for(app, staff) as c:
            bracket_id = await self._started_bracket(c, t.id)
            match_id = (
                await c.get(f'/api/brackets/{bracket_id}/open-matches')
            ).json()[0]['id']
            entries = (await c.get(f'/api/brackets/{bracket_id}/entries')).json()
            await c.post(f'/api/brackets/matches/{match_id}/result', json={
                'winner_entry_id': entries[0]['id'],
            })

            matches = (await c.get(f'/api/brackets/{bracket_id}/matches')).json()
            assert [m['status'] for m in matches] == ['complete']


class TestFeatureGate:
    async def test_disabled_feature_404s(self, db, app):
        """With BRACKETS not enabled for the tenant, the router 404s."""
        await TenantFeatureFlag.filter(tenant_id=1, flag=FeatureFlag.BRACKETS.value).update(
            enabled=False,
        )
        _, staff = await _staff_token()
        async with client_for(app, staff) as c:
            resp = await c.get('/api/brackets?tournament_id=1')
            assert resp.status_code == 404


# --- Cross-tenant isolation -----------------------------------------------


class TestTenantIsolation:
    @pytest.fixture
    async def two(self, db, app):
        from models import Tenant

        a = await Tenant.get(id=1)
        b = await Tenant.create(name='Beta', slug='beta')
        with tenant_scope(a.id):
            user_a, _ = await _staff_token('a-staff')
            ta = await _tournament('A Cup')
            bracket_a = await create_bracket_via_service(user_a, ta.id)
        with tenant_scope(b.id):
            await enable_all_features(b.id)
            _, staff_b = await create_user_token(username='b-staff', roles=[Role.STAFF])
        return {'app': app, 'token_b': staff_b, 'bracket_a': bracket_a}

    async def test_get_other_tenant_404(self, two):
        async with client_for(two['app'], two['token_b']) as c:
            resp = await c.get(f"/api/brackets/{two['bracket_a'].id}")
            assert resp.status_code == 404


async def create_bracket_via_service(actor, tournament_id):
    from application.services import BracketService

    return await BracketService().create_bracket(
        actor, tournament_id=tournament_id, name='Main', format=BracketFormat.SINGLE_ELIM,
    )


class TestSeriesEndpoints:
    """best_of override + server-assigned game scheduling."""

    async def _linked_bracket(self, c, tournament):
        """A started Bo1 bracket whose entrants are linked to real users."""
        bracket_id = (await c.post('/api/brackets', json={
            'tournament_id': tournament.id, 'name': 'Main', 'format': 'single_elim',
        })).json()['id']
        for i, name in enumerate(('Alice', 'Bob'), start=1):
            user = await make_member(discord_id=9100 + i, username=name.lower())
            e = (await c.post('/api/brackets/entrants', json={
                'tournament_id': tournament.id, 'display_name': name,
                'user_id': user.id,
            })).json()
            await c.post(f'/api/brackets/{bracket_id}/entries', json={
                'entrant_id': e['id'],
            })
        await c.post(f'/api/brackets/{bracket_id}/start')
        open_matches = (await c.get(f'/api/brackets/{bracket_id}/open-matches')).json()
        return bracket_id, open_matches[0]['id']

    async def test_set_best_of_and_schedule_assigns_game_numbers(self, db, app):
        _, staff = await _staff_token()
        t = await _tournament()
        async with client_for(app, staff) as c:
            _, match_id = await self._linked_bracket(c, t)

            r = await c.patch(
                f'/api/brackets/matches/{match_id}/best-of', json={'best_of': 3},
            )
            assert r.status_code == 200
            assert r.json()['best_of'] == 3

            for expected in (1, 2, 3):
                r = await c.post(
                    f'/api/brackets/matches/{match_id}/games',
                    json={'scheduled_date': '2026-06-12', 'scheduled_time': '14:30'},
                )
                assert r.status_code == 201
                games = r.json()['games']
                assert len(games) == expected
                assert games[-1]['game_number'] == expected
                assert games[-1]['state'] == 'scheduled'

            r = await c.post(
                f'/api/brackets/matches/{match_id}/games',
                json={'scheduled_date': '2026-06-12', 'scheduled_time': '14:30'},
            )
            assert r.status_code == 400

    async def test_even_best_of_rejected(self, db, app):
        _, staff = await _staff_token()
        t = await _tournament()
        async with client_for(app, staff) as c:
            _, match_id = await self._linked_bracket(c, t)
            r = await c.patch(
                f'/api/brackets/matches/{match_id}/best-of', json={'best_of': 2},
            )
            assert r.status_code == 400

    async def test_read_only_token_cannot_schedule(self, db, app):
        _, staff = await _staff_token()
        t = await _tournament()
        async with client_for(app, staff) as c:
            _, match_id = await self._linked_bracket(c, t)

        _, readonly = await create_user_token(
            username='ro', roles=[Role.STAFF], read_only=True,
        )
        async with client_for(app, readonly) as c:
            r = await c.post(
                f'/api/brackets/matches/{match_id}/games',
                json={'scheduled_date': '2026-06-12', 'scheduled_time': '14:30'},
            )
            assert r.status_code == 403

    async def test_unauthenticated_is_rejected(self, db, app):
        async with client_for(app, None) as c:
            r = await c.post(
                '/api/brackets/matches/1/games',
                json={'scheduled_date': '2026-06-12', 'scheduled_time': '14:30'},
            )
            assert r.status_code == 401

    async def test_missing_match_is_404(self, db, app):
        _, staff = await _staff_token()
        async with client_for(app, staff) as c:
            r = await c.patch(
                '/api/brackets/matches/999999/best-of', json={'best_of': 3},
            )
            assert r.status_code == 404
