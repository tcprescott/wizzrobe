"""Match form questions and answers never cross communities.

Answers decide what a match rolls, so a cross-tenant read would let one
community's answers fire another's seed rule.
"""

from application.repositories import MatchFormRepository
from application.tenant_context import tenant_scope
from models import Match, MatchFormAnswer, MatchFormQuestion, Tournament
from tests.factories import make_user


async def test_match_form_is_isolated(two_tenants):
    a, b = two_tenants
    player = await make_user(discord_id=7101, username='answerer')
    ids = {}
    for tenant in (a, b):
        with tenant_scope(tenant.id):
            t = await Tournament.create(name=f'{tenant.slug} Cup', tenant=tenant)
            m = await Match.create(tournament=t, tenant=tenant)
            q = await MatchFormQuestion.create(
                tenant=tenant, tournament=t, key='hard', kind='yes_no', prompt='Hard?',
                choices=[{'value': 'yes', 'label': 'Yes'}, {'value': 'no', 'label': 'No'}],
            )
            await MatchFormAnswer.create(tenant=tenant, match=m, user=player, question=q, value={'v': 'yes'})
            ids[tenant.id] = (t.id, m.id)

    (ta, ma), (tb, mb) = ids[a.id], ids[b.id]
    with tenant_scope(a.id):
        assert await MatchFormRepository.list_questions(tb) == []
        assert await MatchFormRepository.answers_for_match(mb) == []
        assert [r.value for r in await MatchFormRepository.answers_for_match(ma)] == ['yes']
    with tenant_scope(b.id):
        assert await MatchFormRepository.list_questions(ta) == []
        assert [q.key for q in await MatchFormRepository.list_questions(tb)] == ['hard']
