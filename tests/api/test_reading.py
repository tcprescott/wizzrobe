"""The pages anyone can read, over REST: help, event information, What's new, About, facts.

Public like the website's pages. The community comes from the header; a signed-in
member's token only adds the articles their roles open, and a token that doesn't
resolve is still a 401 so the app refreshes it.
"""

import pytest

from models import FeatureFlag, Role, Tenant, TenantFeatureFlag, UserRole
from tests.api._bracket_api import token_for
from tests.api_helpers import client_for
from tests.factories import make_member, make_user

HEADER = {'X-Wizzrobe-Community': 'default'}


class TestHelp:
    async def test_signed_out_reads_the_index_and_an_article(self, db, app):
        async with client_for(app) as c:
            index = (await c.get('/api/help', headers=HEADER)).json()
            slugs = [a['slug'] for a in index['articles']]
            assert 'getting-started' in slugs
            assert not any(a['for_staff'] for a in index['articles'])
            article = (await c.get('/api/help/crew', headers=HEADER)).json()
        assert article['title'] == 'Signing up as crew'
        assert article['headings'] and article['blocks']
        assert {b['kind'] for b in article['blocks']} >= {'heading', 'para'}

    async def test_staff_articles_only_for_staff(self, db, app):
        async with client_for(app) as c:
            assert (await c.get('/api/help/staff-exports', headers=HEADER)).status_code == 404
        staff = await make_member(discord_id=9601, username='boss')
        await UserRole.create(user=staff, role=Role.STAFF, tenant_id=1)
        async with client_for(app, await token_for(staff)) as c:
            index = (await c.get('/api/help', headers=HEADER)).json()
            assert any(a['slug'] == 'staff-exports' and a['for_staff'] for a in index['articles'])
            assert index['articles'][-1]['for_staff']
            assert (await c.get('/api/help/staff-exports', headers=HEADER)).status_code == 200

    async def test_search_needs_every_word(self, db, app):
        async with client_for(app) as c:
            hits = (await c.get('/api/help', params={'q': 'crew withdraw'}, headers=HEADER)).json()['articles']
        assert 'crew' in [a['slug'] for a in hits]
        assert 'tournament-signup' not in [a['slug'] for a in hits]

    async def test_an_article_for_a_feature_thats_off_is_gone(self, db, app):
        await TenantFeatureFlag.filter(tenant_id=1, flag=FeatureFlag.EQUIPMENT.value).update(enabled=False)
        async with client_for(app) as c:
            slugs = [a['slug'] for a in (await c.get('/api/help', headers=HEADER)).json()['articles']]
            assert 'equipment' not in slugs
            assert (await c.get('/api/help/equipment', headers=HEADER)).status_code == 404

    async def test_a_snippet(self, db, app):
        async with client_for(app) as c:
            body = (await c.get('/api/help/snippets/crew-signup', headers=HEADER)).json()
            assert body['href'].startswith('/help/crew')
            assert body['blocks']
            assert (await c.get('/api/help/snippets/nope', headers=HEADER)).status_code == 404

    async def test_needs_a_community(self, db, app):
        async with client_for(app) as c:
            assert (await c.get('/api/help')).status_code == 400
            missing = await c.get('/api/help', headers={'X-Wizzrobe-Community': 'nowhere'})
            assert missing.status_code == 404

    async def test_a_bad_token_is_a_401_not_signed_out(self, db, app):
        async with client_for(app, 'wizzrobe_pat_nonsense') as c:
            assert (await c.get('/api/help', headers=HEADER)).status_code == 401

    async def test_a_personal_token_reads_its_own_community(self, db, app):
        """The header can't point a personal token at another community."""
        from application.tenant_context import tenant_scope

        other = await Tenant.create(name='Elsewhere', slug='elsewhere')
        someone = await make_user(discord_id=9602, username='someone')
        with tenant_scope(other.id):
            raw = await token_for(someone)
        async with client_for(app, raw) as c:
            assert (await c.get('/api/event-info', headers=HEADER)).status_code == 404
            assert (await c.get('/api/help', headers=HEADER)).status_code == 200


class TestEventInfo:
    async def test_index_and_guide(self, db, app):
        async with client_for(app) as c:
            body = (await c.get('/api/event-info', headers=HEADER)).json()
            assert 'attending' in [a['slug'] for a in body['articles']]
            assert 'proctoring' not in [a['slug'] for a in body['articles']]
            assert body['guide']['filename'].endswith('.pdf')
            pdf = await c.get('/api/event-info/guide.pdf', headers=HEADER)
            assert pdf.status_code == 200
            assert pdf.headers['content-type'] == 'application/pdf'

    async def test_role_pages_for_role_holders(self, db, app):
        async with client_for(app) as c:
            assert (await c.get('/api/event-info/proctoring', headers=HEADER)).status_code == 404
        proctor = await make_member(discord_id=9611, username='proctor')
        await UserRole.create(user=proctor, role=Role.PROCTOR, tenant_id=1)
        async with client_for(app, await token_for(proctor)) as c:
            assert (await c.get('/api/event-info/proctoring', headers=HEADER)).status_code == 200

    async def test_tournament_embed_carries_its_list(self, db, app):
        from tests.test_event_info_service import make_tournament

        await make_tournament(name='ALTTPR Open', is_active=True)
        async with client_for(app) as c:
            body = (await c.get('/api/event-info/attending', headers=HEADER)).json()
        if any(b['kind'] == 'embed' for b in body['blocks']):
            assert [t['name'] for t in body['tournaments']] == ['ALTTPR Open']
            assert body['venue_timezone']
        else:
            assert body['tournaments'] is None

    async def test_off_where_the_feature_is_off(self, db, app):
        await TenantFeatureFlag.filter(tenant_id=1, flag=FeatureFlag.EVENT_INFO.value).update(enabled=False)
        async with client_for(app) as c:
            assert (await c.get('/api/event-info', headers=HEADER)).status_code == 404
            assert (await c.get('/api/event-info/attending', headers=HEADER)).status_code == 404


class TestAboutTheApp:
    async def test_no_community_needed(self, db, app):
        async with client_for(app) as c:
            weeks = (await c.get('/api/changelog')).json()['weeks']
            assert weeks and weeks[0]['sections'][0]['blocks']
            assert weeks[0]['start'] >= weeks[-1]['start']
            about = (await c.get('/api/about')).json()
            assert about['intro'] and about['notice_count'] > 0
            privacy = (await c.get('/api/about/privacy')).json()
            assert privacy['blocks']
            groups = (await c.get('/api/about/notices')).json()['groups']
            key = groups[0]['notices'][0]['key']
            notice = (await c.get('/api/about/notice', params={'key': key})).json()
            assert notice['text']
            assert (await c.get('/api/about/notice', params={'key': 'not-a-thing'})).status_code == 404
            facts = (await c.get('/api/facts')).json()
            assert facts['total'] == sum(len(v) for v in facts['sections'].values())


@pytest.mark.parametrize('path', ['/api/help', '/api/event-info'])
async def test_the_event_query_must_name_a_visible_event(db, app, path):
    async with client_for(app) as c:
        assert (await c.get(path, params={'event': 'nope'}, headers=HEADER)).status_code == 404
