"""REST tests for the public preset library (api/routers/preset_library.py).

Presets live in spaces named ``space/preset``. Anyone in a community can browse,
read and roll another community's *listed* presets; an unlisted one is visible
only to its own community's staff. A tournament still only takes its own
community's presets, so using someone else's means copying it in.
"""

import pytest

from application.services import PresetLibraryService, TournamentService
from application.services.preset_library_service import ROLLS_PER_WINDOW
from application.tenant_context import tenant_scope
from models import Preset, PresetSpace, Role, Tenant
from tests.api_helpers import client_for, create_user_token


@pytest.fixture(autouse=True)
def _mock_seedgen(monkeypatch):
    monkeypatch.setenv('MOCK_SEEDGEN', '1')


async def _other_community():
    other = await Tenant.create(slug='elsewhere', name='Elsewhere')
    with tenant_scope(other.id):
        shown = await Preset.create(name='crosskeys', randomizer='alttpr', settings={'mode': 'open'}, listed=True)
        hidden = await Preset.create(name='secret', randomizer='alttpr', settings={'mode': 'inverted'}, listed=False)
    return other, shown, hidden


async def test_every_community_preset_gets_its_community_space(db):
    preset = await Preset.create(name='Standard', randomizer='alttpr', settings={})
    space = await PresetSpace.get(id=preset.space_id)
    assert space.tenant_id == preset.tenant_id
    assert space.kind.value == 'community'


async def test_member_sees_listed_presets_from_other_communities_only(db, app):
    _, raw = await create_user_token(username='player')
    await Preset.create(name='mine_hidden', randomizer='alttpr', settings={}, listed=False)
    _, shown, hidden = await _other_community()
    async with client_for(app, raw) as c:
        resp = await c.get('/api/preset-library')
    assert resp.status_code == 200
    refs = {p['reference'] for p in resp.json()}
    assert 'elsewhere/crosskeys' in refs
    assert 'elsewhere/secret' not in refs
    assert not any(r.endswith('/mine_hidden') for r in refs)


async def test_staff_see_their_own_hidden_presets_not_others(db, app):
    _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
    await Preset.create(name='mine_hidden', randomizer='alttpr', settings={}, listed=False)
    await _other_community()
    async with client_for(app, raw) as c:
        resp = await c.get('/api/preset-library')
    rows = {p['reference']: p for p in resp.json()}
    assert rows['default/mine_hidden']['listed'] is False
    assert rows['default/mine_hidden']['mine'] is True
    assert 'elsewhere/secret' not in rows


async def test_reading_a_hidden_preset_from_another_community_is_not_found(db, app):
    _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
    _, shown, hidden = await _other_community()
    async with client_for(app, raw) as c:
        assert (await c.get(f'/api/preset-library/{hidden.id}')).status_code == 404
        resp = await c.get(f'/api/preset-library/{shown.id}')
    assert resp.status_code == 200
    assert resp.json()['settings'] == {'mode': 'open'}


async def test_roll_counts_and_is_limited_per_person(db, app):
    _, raw = await create_user_token(username='player')
    _, shown, _ = await _other_community()
    async with client_for(app, raw) as c:
        first = await c.post(f'/api/preset-library/{shown.id}/roll')
        assert first.status_code == 200, first.text
        assert first.json()['seed_url'].startswith('https://mock.seedgen.local/alttpr/')
        for _ in range(ROLLS_PER_WINDOW - 1):
            assert (await c.post(f'/api/preset-library/{shown.id}/roll')).status_code == 200
        over = await c.post(f'/api/preset-library/{shown.id}/roll')
    assert over.status_code == 400
    assert '10 minutes' in over.json()['detail']
    await shown.refresh_from_db()
    assert shown.roll_count == ROLLS_PER_WINDOW


async def test_copy_needs_preset_manager(db, app):
    _, raw = await create_user_token(username='player')
    _, shown, _ = await _other_community()
    async with client_for(app, raw) as c:
        resp = await c.post(f'/api/preset-library/{shown.id}/copy', json={})
    assert resp.status_code == 403


async def test_copy_lands_hidden_with_provenance_and_updates_on_request(db, app):
    _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
    other, shown, _ = await _other_community()
    async with client_for(app, raw) as c:
        resp = await c.post(f'/api/preset-library/{shown.id}/copy', json={})
        assert resp.status_code == 200, resp.text
        copy_id = resp.json()['id']
        assert resp.json()['listed'] is False

        again = await c.post(f'/api/preset-library/{shown.id}/copy', json={})
        assert again.status_code == 400

        view = (await c.get(f'/api/preset-library/{copy_id}')).json()
        assert view['copied_from'] == 'elsewhere/crosskeys'
        assert view['changed_since_copy'] is False

        with tenant_scope(other.id):
            shown.settings = {'mode': 'standard'}
            await shown.save(update_fields=['settings', 'updated_at'])
        view = (await c.get(f'/api/preset-library/{copy_id}')).json()
        assert view['changed_since_copy'] is True
        assert view['settings'] == {'mode': 'open'}

        updated = await c.post(f'/api/preset-library/{copy_id}/update-copy')
        assert updated.status_code == 200
        assert updated.json()['settings'] == {'mode': 'standard'}
        view = (await c.get(f'/api/preset-library/{copy_id}')).json()
    assert view['changed_since_copy'] is False


async def test_tournament_cannot_take_another_communitys_preset(db):
    staff, _ = await create_user_token(username='staff', roles=[Role.STAFF])
    _, shown, _ = await _other_community()
    with pytest.raises(ValueError):
        await TournamentService().create_tournament(name='Open', preset_id=shown.id, actor=staff)


async def test_service_hides_unlisted_preset_from_another_communitys_staff(db):
    staff, _ = await create_user_token(username='staff', roles=[Role.STAFF])
    _, _, hidden = await _other_community()
    with pytest.raises(ValueError):
        await PresetLibraryService().roll(staff, hidden.id)
