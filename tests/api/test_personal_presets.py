"""REST tests for personal preset spaces (api/routers/preset_library.py).

Any member keeps presets in a space of their own, made on their first save and
named after them. Everything in it shows in the library. The owner renames the
space and picks co-editors; co-editors edit presets but can't rename the space
or change who edits it. A tournament can never point at a personal preset.
"""

import pytest

from application.services import TournamentService
from application.services.personal_preset_service import MAX_PRESETS_PER_SPACE, WRITES_PER_WINDOW
from models import Preset, PresetSpace, PresetSpaceEditor, Role, Tenant
from tests.api_helpers import client_for, create_user_token


@pytest.fixture(autouse=True)
def _mock_seedgen(monkeypatch):
    monkeypatch.setenv('MOCK_SEEDGEN', '1')


async def _save(c, name='fastboots', randomizer='alttpr', settings=None, **extra):
    return await c.post('/api/preset-library/personal', json={
        'name': name, 'randomizer': randomizer, 'settings': settings or {'mode': 'open'}, **extra,
    })


async def test_first_save_makes_a_space_named_after_the_player(db, app):
    _, raw = await create_user_token(username='Tom Rando')
    async with client_for(app, raw) as c:
        assert (await c.get('/api/preset-library/spaces')).json() == []
        resp = await _save(c)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        spaces = (await c.get('/api/preset-library/spaces')).json()
        library = (await c.get('/api/preset-library')).json()
    assert body['reference'] == 'tom-rando/fastboots'
    assert body['space_kind'] == 'user'
    assert body['can_edit'] is True and body['listed'] is True
    assert spaces[0]['slug'] == 'tom-rando' and spaces[0]['is_owner'] is True
    assert spaces[0]['preset_count'] == 1
    assert 'tom-rando/fastboots' in {p['reference'] for p in library}
    preset = await Preset.get(id=body['id'])
    assert preset.tenant_id is None


async def test_another_player_sees_and_rolls_but_cannot_edit(db, app):
    _, owner = await create_user_token(username='tom')
    _, other = await create_user_token(username='ann')
    async with client_for(app, owner) as c:
        preset_id = (await _save(c)).json()['id']
    async with client_for(app, other) as c:
        row = (await c.get(f'/api/preset-library/{preset_id}')).json()
        assert row['can_edit'] is False
        assert (await c.post(f'/api/preset-library/{preset_id}/roll')).status_code == 200
        assert (await c.patch(f'/api/preset-library/personal/{preset_id}', json={'name': 'mine'})).status_code == 404
        assert (await c.delete(f'/api/preset-library/personal/{preset_id}')).status_code == 404


async def test_slugs_and_names_are_plain_and_unique(db, app):
    _, raw = await create_user_token(username='tom')
    _, other = await create_user_token(username='ann')
    async with client_for(app, other) as c:
        await _save(c)
    async with client_for(app, raw) as c:
        assert (await _save(c, name='Fast Boots!')).status_code == 400
        assert (await _save(c, name='x')).status_code == 400
        space_id = (await _save(c)).json() and (await c.get('/api/preset-library/spaces')).json()[0]['id']
        assert (await _save(c)).status_code == 400
        taken = await c.patch(f'/api/preset-library/spaces/{space_id}', json={'slug': 'ann'})
        assert taken.status_code == 400 and 'taken' in taken.json()['detail']
        assert (await c.patch(f'/api/preset-library/spaces/{space_id}', json={'slug': 'Bad Slug'})).status_code == 400
        ok = await c.patch(f'/api/preset-library/spaces/{space_id}', json={'slug': 'tommy'})
        assert ok.status_code == 200 and ok.json()['slug'] == 'tommy'
        refs = {p['reference'] for p in (await c.get('/api/preset-library')).json()}
    assert 'tommy/fastboots' in refs


async def test_coeditor_edits_presets_but_not_the_space(db, app):
    _, owner_raw = await create_user_token(username='tom')
    helper, helper_raw = await create_user_token(username='ann')
    async with client_for(app, owner_raw) as c:
        preset_id = (await _save(c)).json()['id']
        space_id = (await c.get('/api/preset-library/spaces')).json()[0]['id']
        added = await c.post(f'/api/preset-library/spaces/{space_id}/editors', json={'user_id': helper.id})
        assert added.status_code == 200, added.text
        assert [e['name'] for e in added.json()['editors']] == ['ann']
    async with client_for(app, helper_raw) as c:
        spaces = (await c.get('/api/preset-library/spaces')).json()
        assert [(s['slug'], s['is_owner']) for s in spaces] == [('tom', False)]
        edit = await c.patch(f'/api/preset-library/personal/{preset_id}', json={'settings': {'mode': 'inverted'}})
        assert edit.status_code == 200 and edit.json()['settings']['mode'] == 'inverted'
        made = await _save(c, name='helper-made', space_id=space_id)
        assert made.json()['reference'] == 'tom/helper-made'
        assert (await c.patch(f'/api/preset-library/spaces/{space_id}', json={'slug': 'annspace'})).status_code == 403
        other, _ = await create_user_token(username='bob')
        add = await c.post(f'/api/preset-library/spaces/{space_id}/editors', json={'user_id': other.id})
        assert add.status_code == 403
        left = await c.delete(f'/api/preset-library/spaces/{space_id}/editors/{helper.id}')
        assert left.status_code == 200
        assert (await c.get('/api/preset-library/spaces')).json() == []


async def test_coeditor_must_be_a_member_here(db, app):
    _, owner_raw = await create_user_token(username='tom')
    stranger, _ = await create_user_token(username='stranger', member=False)
    async with client_for(app, owner_raw) as c:
        await _save(c)
        space_id = (await c.get('/api/preset-library/spaces')).json()[0]['id']
        resp = await c.post(f'/api/preset-library/spaces/{space_id}/editors', json={'user_id': stranger.id})
    assert resp.status_code == 404
    assert not await PresetSpaceEditor.exists(user_id=stranger.id)


async def test_space_caps_and_write_limit(db, app, monkeypatch):
    from application.services import personal_preset_service as pps

    _, raw = await create_user_token(username='tom')
    async with client_for(app, raw) as c:
        assert (await _save(c, name='p0')).status_code == 201
        space = await PresetSpace.get(slug='tom')
        for i in range(1, MAX_PRESETS_PER_SPACE):
            await Preset.create(space_id=space.id, tenant_id=None, name=f'p{i}', randomizer='alttpr', settings={})
        full = await _save(c, name='one-more')
        assert full.status_code == 400 and 'at most' in full.json()['detail']

    monkeypatch.setattr(pps, 'MAX_PRESETS_PER_SPACE', 10 ** 6)
    _, raw2 = await create_user_token(username='ann')
    async with client_for(app, raw2) as c:
        for i in range(WRITES_PER_WINDOW):
            assert (await _save(c, name=f'q{i}')).status_code == 201
        limited = await _save(c, name='too-fast')
    assert limited.status_code == 400 and 'Try again' in limited.json()['detail']


async def test_settings_are_size_capped(db, app):
    _, raw = await create_user_token(username='tom')
    async with client_for(app, raw) as c:
        resp = await _save(c, settings={'blob': 'x' * (65 * 1024)})
    assert resp.status_code == 400


async def test_copy_any_preset_into_my_space_and_update_it(db, app):
    _, owner_raw = await create_user_token(username='tom')
    _, copier_raw = await create_user_token(username='ann')
    async with client_for(app, owner_raw) as c:
        original_id = (await _save(c)).json()['id']
    async with client_for(app, copier_raw) as c:
        copy = await c.post(f'/api/preset-library/{original_id}/copy-to-mine', json={})
        assert copy.status_code == 201, copy.text
        copy_id = copy.json()['id']
        assert copy.json()['reference'] == 'ann/fastboots'
        assert copy.json()['copied_from'] == 'tom/fastboots'
    async with client_for(app, owner_raw) as c:
        await c.patch(f'/api/preset-library/personal/{original_id}', json={'settings': {'mode': 'inverted'}})
    async with client_for(app, copier_raw) as c:
        row = (await c.get(f'/api/preset-library/{copy_id}')).json()
        assert row['changed_since_copy'] is True and row['settings'] == {'mode': 'open'}
        assert (await c.post(f'/api/preset-library/{copy_id}/update-copy')).status_code == 200
        assert (await c.get(f'/api/preset-library/{copy_id}')).json()['settings'] == {'mode': 'inverted'}
    async with client_for(app, owner_raw) as c:
        assert (await c.post(f'/api/preset-library/{copy_id}/update-copy')).status_code == 404


async def test_a_tournament_cannot_use_a_personal_preset(db, app):
    user, raw = await create_user_token(username='tom', roles=[Role.STAFF])
    async with client_for(app, raw) as c:
        preset_id = (await _save(c)).json()['id']
    with pytest.raises(ValueError, match=r'[Pp]reset'):
        await TournamentService().create_tournament(name='Open', actor=user, preset_id=preset_id)


async def test_spaces_and_library_need_a_signed_in_member(db, app):
    async with client_for(app, None) as c:
        assert (await c.get('/api/preset-library/spaces')).status_code == 401
        assert (await c.post('/api/preset-library/personal', json={})).status_code in (401, 422)
    assert await Tenant.exists(id=1)


async def test_member_search_only_finds_this_communitys_members(db, app):
    _, raw = await create_user_token(username='tom')
    await create_user_token(username='annie')
    await create_user_token(username='anna-outside', member=False)
    async with client_for(app, raw) as c:
        names = [m['name'] for m in (await c.get('/api/preset-library/members', params={'q': 'ann'})).json()]
        assert (await c.get('/api/preset-library/members', params={'q': 'a'})).json() == []
    assert names == ['annie']
