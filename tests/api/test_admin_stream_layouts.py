"""REST tests for the app's Admin → Stream Layouts screen (api/routers/admin_stream_layouts.py)."""

from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest
from PIL import Image

from application.services import stream_layout_service as sls
from models import Match, MatchPlayers, Role, Stage
from tests.api_helpers import client_for, create_user_token
from tests.factories import make_member
from tests.services._match_service_setup import make_tournament


@pytest.fixture(autouse=True)
def _fresh_cache():
    sls._feed_cache.clear()
    yield
    sls._feed_cache.clear()


def _png(width=1280, height=720) -> bytes:
    out = BytesIO()
    Image.new('RGBA', (width, height), (40, 10, 5, 255)).save(out, 'PNG')
    return out.getvalue()


_ids = iter(range(500, 10_000))


async def _match_on(stage: Stage, *names: str, started=False) -> Match:
    tournament = await make_tournament(name='ALTTPR Open')
    match = await Match.create(
        tournament=tournament, stage=stage, scheduled_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        started_at=datetime.now(timezone.utc) if started else None,
    )
    for name in names:
        await MatchPlayers.create(match=match, user=await make_member(discord_id=next(_ids), username=name))
    return match


class TestStreamLayoutsScreen:
    async def test_board_lists_stages_and_layouts(self, db, app):
        stage = await Stage.create(name='Main Stage', stream_url='https://twitch.tv/speedgaming')
        await _match_on(stage, 'Dana', 'Kai', started=True)
        _, raw = await create_user_token(username='tech', roles=[Role.STREAM_MANAGER])
        async with client_for(app, raw) as c:
            r = await c.post('/api/admin/stream-layouts/layouts', json={'name': '2P trackers', 'starter': 'sg_2p_trackers'})
            assert r.status_code == 201, r.text
            layout_id = r.json()['layout']['id']
            assert (await c.put(f'/api/admin/stream-layouts/stages/{stage.id}/layout', json={'layout_id': layout_id})).status_code == 204
            body = (await c.get('/api/admin/stream-layouts/board')).json()
        main = body['stages'][0]
        assert (main['name'], main['channel'], main['layout_name']) == ('Main Stage', 'twitch.tv/speedgaming', '2P trackers')
        assert main['showing']['who'] == 'Dana vs Kai' and main['pinned'] is False
        assert '/overlay/ovl_' in main['obs_url']
        assert body['layouts'][0]['stages'] == ['Main Stage']

    async def test_pin_and_unpin_a_match(self, db, app):
        stage = await Stage.create(name='Main Stage')
        await _match_on(stage, 'Dana', 'Kai', started=True)
        later = await _match_on(stage, 'Morgan', 'Riley')
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            options = (await c.get(f'/api/admin/stream-layouts/stages/{stage.id}/matches')).json()
            assert later.id in [m['id'] for m in options]
            assert (await c.put(f'/api/admin/stream-layouts/stages/{stage.id}/pin', json={'match_id': later.id})).status_code == 204
            row = (await c.get('/api/admin/stream-layouts/board')).json()['stages'][0]
            assert (row['showing']['id'], row['pinned']) == (later.id, True)
            assert (await c.delete(f'/api/admin/stream-layouts/stages/{stage.id}/pin')).status_code == 204
            row = (await c.get('/api/admin/stream-layouts/board')).json()['stages'][0]
            assert row['pinned'] is False

    async def test_pinning_another_stages_match_is_refused(self, db, app):
        one = await Stage.create(name='Stage 1')
        two = await Stage.create(name='Stage 2')
        match = await _match_on(two, 'Dana', 'Kai')
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            r = await c.put(f'/api/admin/stream-layouts/stages/{one.id}/pin', json={'match_id': match.id})
        assert r.status_code in (400, 422), r.text

    async def test_edit_boxes_art_and_preview(self, db, app):
        stage = await Stage.create(name='Main Stage')
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            created = (await c.post('/api/admin/stream-layouts/layouts', json={'name': 'Blank'})).json()
            layout_id, stamp = created['layout']['id'], created['layout']['updated_at']
            box = {'kind': 'field', 'field': 'custom_text', 'text': 'Hello', 'x': 10, 'y': 10, 'w': 300, 'h': 40}
            saved = await c.put(f'/api/admin/stream-layouts/layouts/{layout_id}/boxes', json={'boxes': [box], 'expected_updated_at': stamp})
            assert saved.status_code == 200, saved.text
            box_id = saved.json()['boxes'][0]['id']
            stale = await c.put(f'/api/admin/stream-layouts/layouts/{layout_id}/boxes', json={'boxes': [], 'expected_updated_at': stamp})
            assert stale.status_code in (400, 409, 422)
            preview = (await c.post(f'/api/admin/stream-layouts/layouts/{layout_id}/preview', json={})).json()
            assert preview['values'][box_id] == 'Hello'
            art = await c.put(f'/api/admin/stream-layouts/layouts/{layout_id}/art', content=_png(), headers={'content-type': 'image/png'})
            assert art.status_code == 200, art.text
            assert art.json()['layout']['art_url'] and art.json()['layout']['width'] == 1280
            await c.put(f'/api/admin/stream-layouts/stages/{stage.id}/layout', json={'layout_id': layout_id})
            shown = (await c.get(f'/api/admin/stream-layouts/stages/{stage.id}/preview')).json()
            assert shown['values'][box_id] == 'Hello' and shown['art_url']
            scenes = (await c.get('/api/admin/stream-layouts/obs-scenes')).json()
            assert scenes['filename'].endswith('.json') and 'Main Stage' in scenes['content']
            assert (await c.delete(f'/api/admin/stream-layouts/layouts/{layout_id}')).status_code == 204

    async def test_a_plain_member_is_refused(self, db, app):
        _, raw = await create_user_token(username='plain')
        async with client_for(app, raw) as c:
            assert (await c.get('/api/admin/stream-layouts/board')).status_code == 403
