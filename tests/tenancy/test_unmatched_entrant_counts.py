"""The Tournaments board's "N unmatched" counts stay inside one community."""

from application.repositories import ChallongeRepository, StartggRepository
from application.services import bracket_sources
from application.tenant_context import tenant_scope
from models import ChallongeParticipant, StartggEntrant, Tournament
from tests.factories import make_user


async def test_unmatched_counts_cover_only_entrants_without_a_member(two_tenants):
    a, b = two_tenants
    player = await make_user(7201, 'player')
    made = {}
    for tenant in (a, b):
        with tenant_scope(tenant.id):
            ch = await Tournament.create(name=f'{tenant.slug} CH', tenant=tenant, challonge_tournament_id=f'c-{tenant.slug}')
            sg = await Tournament.create(name=f'{tenant.slug} SG', tenant=tenant,
                                         startgg_event_slug=f'tournament/{tenant.slug}/event/x')
            await ChallongeParticipant.create(tenant=tenant, tournament=ch, challonge_participant_id='1', name='A', user=player)
            await ChallongeParticipant.create(tenant=tenant, tournament=ch, challonge_participant_id='2', name='B')
            await ChallongeParticipant.create(tenant=tenant, tournament=ch, challonge_participant_id='3', name='C')
            await StartggEntrant.create(tenant=tenant, tournament=sg, entrant_id='1', name='A', user=player)
            await StartggEntrant.create(tenant=tenant, tournament=sg, entrant_id='2', name='B')
            made[tenant.id] = (ch, sg)

    (ch_a, sg_a), (ch_b, sg_b) = made[a.id], made[b.id]
    with tenant_scope(a.id):
        assert await ChallongeRepository.count_unmatched_participants([ch_a.id, ch_b.id]) == {ch_a.id: 2}
        assert await StartggRepository.count_unmatched_entrants([sg_a.id, sg_b.id]) == {sg_a.id: 1}
        assert await ChallongeRepository.count_unmatched_participants([]) == {}
        counts = await bracket_sources.unmatched_entrant_counts([ch_a, sg_a])
        assert counts == {ch_a.id: 2, sg_a.id: 1}
