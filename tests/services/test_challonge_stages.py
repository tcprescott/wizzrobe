"""Stage control on a linked Challonge bracket (``ChallongeStageMixin``).

Challonge has no webhooks, so Wizzrobe advancing the stage itself (and
re-syncing in the same step) is what makes the bracket after the groups
bookable without waiting for someone to press Sync.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from models import Tournament
from tests.services.test_challonge_service import make_service, make_user

pytestmark = pytest.mark.usefixtures("bypass_auth")


class TestStageActions:
    @pytest.mark.parametrize('state,group,expected', [
        ('pending', False, ['start']),
        ('pending', True, ['start_group_stage']),
        ('group_stages_underway', True, ['advance_to_bracket']),
        ('group_stages_finalized', True, ['start_bracket']),
        ('underway', False, []),
        ('awaiting_review', False, ['finalize']),
        ('complete', False, []),
        (None, False, []),
        ('something_new', False, []),
    ])
    def test_actions_follow_the_cached_state(self, state, group, expected):
        from application.services._challonge_stages import stage_actions
        assert [a.key for a in stage_actions(state, group)] == expected

    def test_error_detail_from_a_live_scope_refusal(self):
        from application.services._challonge_stages import _challonge_error_detail
        from application.utils.clients.challonge_client import ChallongeAPIError
        e = ChallongeAPIError(
            'Challonge API error (403): {"errors":{"detail":"Request requires one of '
            'the following scopes: tournaments:write","status":"403"}}', status=403,
        )
        assert _challonge_error_detail(e) == (
            'Request requires one of the following scopes: tournaments:write.'
        )


class TestAdvanceStage:
    async def _setup(self, state='group_stages_underway', scopes='me tournaments:write'):
        from models import ChallongeConnection
        actor = await make_user(1, 'staff')
        await ChallongeConnection.create(access_token='t', scopes=scopes)
        tournament = await Tournament.create(
            name='T', challonge_tournament_id='T1', challonge_community='speedgaming',
            challonge_state=state, challonge_group_stage=True,
        )
        api = MagicMock()
        api.change_state = AsyncMock(return_value={})
        api.get_tournament_full = AsyncMock(return_value={
            'tournament': {'id': 'T1', 'name': 'T', 'url': None,
                           'state': 'underway', 'group_stage': True},
            'participants': [], 'matches': [],
        })
        return make_service(api=api), api, actor, tournament

    async def test_groups_to_bracket_runs_both_steps_then_syncs(self, db):
        service, api, actor, tournament = await self._setup()
        await service.advance_stage(tournament.id, 'advance_to_bracket', actor)
        assert [c.args[1] for c in api.change_state.await_args_list] == [
            'finalize_group_stage', 'start',
        ]
        assert api.change_state.await_args.kwargs == {'community': 'speedgaming'}
        await tournament.refresh_from_db()
        assert tournament.challonge_state == 'underway'

    async def test_audited(self, db):
        from models import AuditLog
        service, _, actor, tournament = await self._setup()
        await service.advance_stage(tournament.id, 'advance_to_bracket', actor)
        import json
        log = await AuditLog.get(action='challonge.stage_advanced')
        details = json.loads(log.details)
        assert details['steps'] == ['finalize_group_stage', 'start']
        assert details['from_state'] == 'group_stages_underway'

    async def test_second_step_failure_resyncs_and_says_so(self, db):
        from application.utils.clients.challonge_client import ChallongeAPIError
        service, api, actor, tournament = await self._setup()
        api.change_state.side_effect = [
            {}, ChallongeAPIError('Challonge API error (422): {"errors":[{"detail":"Nope"}]}', status=422),
        ]
        api.get_tournament_full.return_value['tournament']['state'] = 'group_stages_finalized'
        with pytest.raises(ValueError, match=r"finished 'finalize group stage' but refused the next step: Nope\."):
            await service.advance_stage(tournament.id, 'advance_to_bracket', actor)
        await tournament.refresh_from_db()
        assert tournament.challonge_state == 'group_stages_finalized'

    async def test_first_step_failure_audits_nothing_but_resyncs(self, db):
        from application.utils.clients.challonge_client import ChallongeAPIError
        from models import AuditLog
        service, api, actor, tournament = await self._setup()
        api.get_tournament_full.return_value['tournament']['state'] = 'group_stages_underway'
        api.change_state.side_effect = ChallongeAPIError(
            'Challonge API error (422): {"errors":{"detail":"All group matches must be complete"}}', status=422,
        )
        with pytest.raises(ValueError, match=(
            r'refused to finalize groups and start bracket: All group matches must be complete\. '
            r'The bracket has been re-synced'
        )):
            await service.advance_stage(tournament.id, 'advance_to_bracket', actor)
        assert not await AuditLog.filter(action='challonge.stage_advanced').exists()
        await tournament.refresh_from_db()
        assert tournament.challonge_state == 'group_stages_underway'

    async def test_stage_already_moved_on_challonge_catches_up(self, db):
        from application.utils.clients.challonge_client import ChallongeAPIError
        service, api, actor, tournament = await self._setup()
        api.change_state.side_effect = ChallongeAPIError(
            'Challonge API error (422): {"errors":{"detail":"Invalid state transition"}}', status=422,
        )
        with pytest.raises(ValueError, match='re-synced'):
            await service.advance_stage(tournament.id, 'advance_to_bracket', actor)
        await tournament.refresh_from_db()
        assert tournament.challonge_state == 'underway'
        assert service.stage_actions_for(tournament) == []

    async def test_refusal_still_reported_when_the_resync_fails(self, db):
        from application.utils.clients.challonge_client import ChallongeAPIError
        service, api, actor, tournament = await self._setup()
        api.change_state.side_effect = ChallongeAPIError(
            'Challonge API error (422): {"errors":{"detail":"Nope"}}', status=422,
        )
        api.get_tournament_full.side_effect = ChallongeAPIError('Challonge API error (503): down', status=503)
        with pytest.raises(ValueError) as exc:
            await service.advance_stage(tournament.id, 'advance_to_bracket', actor)
        assert str(exc.value) == 'Challonge refused to finalize groups and start bracket: Nope.'

    async def test_needs_the_write_scope(self, db):
        service, api, actor, tournament = await self._setup(scopes='me tournaments:read')
        with pytest.raises(ValueError, match='Reconnect Challonge'):
            await service.advance_stage(tournament.id, 'advance_to_bracket', actor)
        api.change_state.assert_not_awaited()

    async def test_refuses_a_stale_action(self, db):
        service, api, actor, tournament = await self._setup(state='underway')
        with pytest.raises(ValueError, match="isn't the next step"):
            await service.advance_stage(tournament.id, 'advance_to_bracket', actor)
        api.change_state.assert_not_awaited()

    async def test_sync_caches_the_remote_state(self, db):
        service, _, actor, tournament = await self._setup(state=None)
        await service.sync_bracket(tournament.id, actor, force=True)
        await tournament.refresh_from_db()
        assert (tournament.challonge_state, tournament.challonge_group_stage) == ('underway', True)

    async def test_unlink_clears_the_cached_state(self, db):
        service, _, actor, tournament = await self._setup()
        await service.unlink_tournament(tournament.id, actor)
        await tournament.refresh_from_db()
        assert tournament.challonge_state is None and not tournament.challonge_group_stage


class TestClientChangeState:
    async def test_path_and_body(self):
        from application.utils.clients.challonge_client import ChallongeClient
        client = ChallongeClient('id', 'secret', token_provider=AsyncMock(return_value='tok'))
        client._authed_request = AsyncMock(return_value={'data': {
            'id': '1', 'attributes': {'state': 'underway', 'group_stage_enabled': True},
        }})
        result = await client.change_state('1', 'start', community='speedgaming')
        method, path = client._authed_request.call_args.args
        assert (method, path) == ('PUT', '/communities/speedgaming/tournaments/1/change_state.json')
        assert client._authed_request.call_args.kwargs['json'] == {
            'data': {'type': 'TournamentState', 'attributes': {'state': 'start'}},
        }
        assert result['state'] == 'underway' and result['group_stage'] is True
