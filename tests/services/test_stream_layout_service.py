"""StreamLayoutService: layouts, art, stage overlays, pins, and the overlay feed."""

import json
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest
from PIL import Image

from application.errors import FeatureDisabledError, NotFoundError
from application.event_context import event_scope
from application.services import stream_layout_service as sls
from application.services.stream_layout_rules import clean_box, clean_boxes
from application.services.stream_layout_service import StreamLayoutService, prepare_art
from application.services.stream_layout_starters import STARTERS, STARTERS_BY_KEY
from application.tenant_context import tenant_scope
from models import (
    AuditLog,
    Commentator,
    Event,
    FeatureFlag,
    Match,
    MatchPlayers,
    Role,
    Stage,
    StageOverlay,
    StreamLayout,
    TenantFeatureFlag,
    UserRole,
)
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.factories import make_member
from tests.services._match_service_setup import make_tournament


@pytest.fixture(autouse=True)
def _fresh_cache():
    sls._feed_cache.clear()
    yield
    sls._feed_cache.clear()


@pytest.fixture
async def event(db):
    ev = await Event.get(tenant_id=DEFAULT_TEST_TENANT_ID, is_default=True)
    with event_scope(ev.id):
        yield ev


async def _staff(discord_id=1, name='staff'):
    user = await make_member(discord_id=discord_id, username=name)
    await UserRole.create(user=user, role=Role.STAFF, tenant_id=DEFAULT_TEST_TENANT_ID)
    return user


async def _tech(discord_id=2, name='tech'):
    user = await make_member(discord_id=discord_id, username=name)
    await UserRole.create(user=user, role=Role.STREAM_MANAGER, tenant_id=DEFAULT_TEST_TENANT_ID)
    return user


def _png(width=1280, height=720, transparent=True) -> bytes:
    img = Image.new('RGBA', (width, height), (40, 10, 5, 255))
    if transparent:
        for x in range(10, 200):
            for y in range(10, 100):
                img.putpixel((x, y), (0, 0, 0, 0))
    out = BytesIO()
    img.save(out, 'PNG')
    return out.getvalue()


def _name_box(which=1, **extra) -> dict:
    return {'kind': 'field', 'field': 'player_name', 'which': which,
            'x': 100, 'y': 20, 'w': 300, 'h': 40, 'size': 24, **extra}


async def _match_on(stage: Stage, *names: str, minutes=0, started=False) -> Match:
    tournament = await make_tournament(name='ALTTPR Open')
    match = await Match.create(
        tournament=tournament, stage=stage,
        scheduled_at=datetime.now(timezone.utc) + timedelta(minutes=minutes),
        started_at=datetime.now(timezone.utc) if started else None,
    )
    for i, name in enumerate(names):
        user = await make_member(discord_id=100 + i + minutes * 10, username=name)
        await MatchPlayers.create(match=match, user=user)
    return match


class TestRules:
    def test_box_is_clamped_onto_the_canvas(self):
        box = clean_box(_name_box(x=1900, w=500), 1920, 1080)
        assert box['w'] == 500 and box['x'] == 1420

    def test_unknown_field_and_bad_colour_are_refused(self):
        with pytest.raises(ValueError):
            clean_box(_name_box(field='password'), 1920, 1080)
        with pytest.raises(ValueError):
            clean_box(_name_box(color='red;background:url(x)'), 1920, 1080)

    def test_unknown_font_falls_back_and_text_is_bounded(self):
        assert clean_box(_name_box(font='Comic</style>'), 1920, 1080)['font'] == 'Inter'
        with pytest.raises(ValueError):
            clean_box({**_name_box(), 'field': 'custom_text', 'text': 'x' * 201}, 1920, 1080)

    def test_duplicate_ids_are_renamed(self):
        boxes = clean_boxes([_name_box(id='aa'), _name_box(id='aa')], 1920, 1080)
        assert boxes[0]['id'] != boxes[1]['id']

    def test_art_is_reencoded_keeping_its_size(self):
        content, w, h = prepare_art(_png())
        assert (w, h) == (1280, 720)
        assert content.startswith(b'\x89PNG')

    def test_art_must_be_an_image(self):
        with pytest.raises(ValueError):
            prepare_art(b'<svg onload=alert(1)>')


class TestLayouts:
    async def test_staff_and_broadcast_tech_manage_others_cannot(self, event):
        service = StreamLayoutService()
        layout = await service.create_layout(await _staff(), '2-player race')
        await service.rename_layout(await _tech(), layout.id, 'Race')
        player = await make_member(discord_id=9, username='player')
        with pytest.raises(PermissionError):
            await service.list_layouts(player)
        assert (await StreamLayout.get(id=layout.id)).event_id == event.id
        assert await AuditLog.filter(action='stream_layout.created').count() == 1

    async def test_names_are_unique_per_event(self, event):
        staff = await _staff()
        await StreamLayoutService().create_layout(staff, 'Race')
        with pytest.raises(ValueError):
            await StreamLayoutService().create_layout(staff, 'race')

    async def test_save_refuses_a_stale_copy(self, event):
        staff = await _staff()
        service = StreamLayoutService()
        layout = await service.create_layout(staff, 'Race')
        first = layout.updated_at
        await service.save_boxes(staff, layout.id, [_name_box()], first)
        with pytest.raises(ValueError, match='Someone else saved'):
            await service.save_boxes(staff, layout.id, [], first)

    async def test_new_art_sets_the_canvas_and_scales_boxes(self, event):
        staff = await _staff()
        service = StreamLayoutService()
        layout = await service.create_layout(staff, 'Race')
        await service.save_boxes(staff, layout.id, [_name_box()])
        layout, transparent = await service.set_art(staff, layout.id, _png(960, 540))
        assert transparent and (layout.width, layout.height) == (960, 540)
        assert layout.boxes[0]['x'] == 50 and layout.boxes[0]['w'] == 150
        assert layout.art_sha256

    async def test_flag_off_refuses(self, event):
        await TenantFeatureFlag.filter(
            tenant_id=DEFAULT_TEST_TENANT_ID, flag=FeatureFlag.STREAM_LAYOUTS.value,
        ).update(enabled=False)
        with pytest.raises(FeatureDisabledError):
            await StreamLayoutService().list_layouts(await _staff())


class TestOverlay:
    async def _setup(self):
        staff = await _staff()
        service = StreamLayoutService()
        stage = await Stage.create(name='Main Stage', stream_url='https://twitch.tv/speedgaming')
        layout = await service.create_layout(staff, 'Race')
        await service.save_boxes(staff, layout.id, [
            _name_box(1, id='p1'), _name_box(2, id='p2'),
            {'kind': 'field', 'field': 'channel', 'id': 'ch', 'x': 0, 'y': 0, 'w': 200, 'h': 30},
            {'kind': 'field', 'field': 'commentators', 'id': 'cm', 'x': 0, 'y': 40, 'w': 200, 'h': 30},
            {'kind': 'field', 'field': 'up_next', 'id': 'un', 'x': 0, 'y': 80, 'w': 200, 'h': 30},
            {'kind': 'hole', 'id': 'h1', 'x': 0, 'y': 200, 'w': 400, 'h': 300},
        ])
        await service.set_stage_layout(staff, stage.id, layout.id)
        overlay = await StageOverlay.get(stage_id=stage.id)
        return staff, service, stage, overlay

    async def test_feed_follows_the_stage(self, event):
        staff, service, stage, overlay = await self._setup()
        live = await _match_on(stage, 'Wavedash', 'Moonlit', started=True)
        await Commentator.create(match=live, user=staff, approved=True)
        await _match_on(stage, 'Kestrel', 'Juno', minutes=30)
        feed = await service.overlay_feed(await StreamLayoutService.resolve_token(overlay.token))
        assert feed.values['p1'] == 'Wavedash' and feed.values['p2'] == 'Moonlit'
        assert feed.values['ch'] == 'twitch.tv/speedgaming'
        assert feed.values['cm'] == 'staff'
        assert 'Kestrel vs Juno' in feed.values['un']
        assert 'h1' not in feed.values

    async def test_pin_holds_a_match_until_unpinned(self, event):
        staff, service, stage, overlay = await self._setup()
        await _match_on(stage, 'Wavedash', 'Moonlit', started=True)
        later = await _match_on(stage, 'Kestrel', 'Juno', minutes=30)
        await service.pin_match(await _tech(), later.id)
        feed = await service.overlay_feed(await StreamLayoutService.resolve_token(overlay.token))
        assert feed.values['p1'] == 'Kestrel'
        assert await service.pin_state(staff, later.id) is True
        await service.unpin(staff, stage.id)
        sls._feed_cache.clear()
        feed = await service.overlay_feed(await StreamLayoutService.resolve_token(overlay.token))
        assert feed.values['p1'] == 'Wavedash'
        assert await AuditLog.filter(action='stage_overlay.pinned').count() == 1

    async def test_renewed_link_retires_the_old_one(self, event):
        staff, service, stage, overlay = await self._setup()
        await service.renew_link(staff, stage.id)
        assert await StreamLayoutService.resolve_token(overlay.token) is None

    async def test_another_communitys_overlay_is_not_served(self, two_tenants):
        tenant_a, tenant_b = two_tenants
        ev = await Event.get(tenant_id=tenant_a.id, is_default=True)
        with event_scope(ev.id):
            _, service, _, overlay = await self._setup()
        ev_b = await Event.get(tenant_id=tenant_b.id, is_default=True)
        with tenant_scope(tenant_b.id), event_scope(ev_b.id):
            with pytest.raises(NotFoundError):
                await service.overlay_feed(await StreamLayoutService.resolve_token(overlay.token))


class TestStarters:
    async def test_starter_brings_boxes_and_a_plain_background(self, event):
        staff = await _staff()
        service = StreamLayoutService()
        layout = await service.create_layout(staff, 'Race', starter='sg_2p_trackers', background='#14213d')
        assert (layout.width, layout.height) == (1920, 1080)
        assert sum(b['kind'] == 'hole' for b in layout.boxes) == 7
        assert layout.art_sha256
        art = await StreamLayoutService.art_bytes(layout.tenant_id, layout.id, layout.art_sha256)
        assert Image.open(BytesIO(art)).getpixel((5, 5)) == (0x14, 0x21, 0x3D, 255)

    async def test_every_starter_fits_its_canvas(self, event):
        staff = await _staff()
        service = StreamLayoutService()
        for i, starter in enumerate(STARTERS):
            layout = await service.create_layout(staff, f'S{i}', starter=starter.key)
            assert len(layout.boxes) == len(starter.boxes)
            for clean, raw in zip(layout.boxes, starter.boxes, strict=True):
                assert (clean['x'], clean['y'], clean['w'], clean['h']) == (raw['x'], raw['y'], raw['w'], raw['h'])

    async def test_unknown_starter_and_bad_colour_are_refused(self, event):
        staff = await _staff()
        with pytest.raises(ValueError):
            await StreamLayoutService().create_layout(staff, 'Race', starter='nope')
        with pytest.raises(ValueError):
            await StreamLayoutService().create_layout(staff, 'Race', background='red')

    async def test_plain_background_replaces_art_and_keeps_boxes(self, event):
        staff = await _staff()
        service = StreamLayoutService()
        layout = await service.create_layout(staff, 'Race', starter='ffr_1p')
        await service.set_art(staff, layout.id, _png(1920, 1080))
        layout = await service.set_background(staff, layout.id, '#000000')
        assert len(layout.boxes) == len(STARTERS_BY_KEY['ffr_1p'].boxes)
        art = await StreamLayoutService.art_bytes(layout.tenant_id, layout.id, layout.art_sha256)
        assert Image.open(BytesIO(art)).getpixel((300, 300)) == (0, 0, 0, 255)

    async def test_plain_background_keeps_the_old_arts_transparent_areas(self, event):
        staff = await _staff()
        service = StreamLayoutService()
        layout = await service.create_layout(staff, 'Race')
        await service.set_art(staff, layout.id, _png(1920, 1080))
        layout = await service.set_background(staff, layout.id, '#14213D')
        art = Image.open(BytesIO(await StreamLayoutService.art_bytes(layout.tenant_id, layout.id, layout.art_sha256)))
        assert art.getpixel((50, 50))[3] == 0
        assert art.getpixel((500, 500)) == (0x14, 0x21, 0x3D, 255)


class TestObsScenes:
    async def test_collection_nests_shared_feeds_under_the_overlay(self, event):
        staff = await _staff()
        service = StreamLayoutService()
        one = await Stage.create(name='Stage 1')
        two = await Stage.create(name='Stage 2')
        race = await service.create_layout(staff, 'Race', starter='sg_2p_trackers')
        cams = await service.create_layout(staff, 'Cams', starter='sg_2p_cams')
        await service.set_stage_layout(staff, one.id, race.id)
        await service.set_stage_layout(staff, two.id, cams.id)

        filename, data = await service.obs_scene_collection(staff)
        collection = json.loads(data)
        assert filename.endswith('-obs-scenes.json')
        by_name = {s['name']: s for s in collection['sources']}
        stage1 = by_name['Stage 1 · Race']['settings']['items']
        assert stage1[-1]['name'].startswith('Wizzrobe overlay')
        overlay = by_name[stage1[-1]['name']]
        token = (await StageOverlay.get(stage_id=one.id)).token
        assert overlay['settings']['url'].endswith(f'/overlay/{token}')
        game = next(i for i in stage1 if i['name'] == 'Feed · Player 1 game')
        assert (game['pos']['x'], game['pos']['y']) == (26, 96)
        assert (game['bounds']['x'], game['bounds']['y']) == (846, 679)
        # Both layouts nest the same Player 1 game feed, sized to the bigger hole.
        stage2 = by_name['Stage 2 · Cams']['settings']['items']
        assert any(i['source_uuid'] == game['source_uuid'] for i in stage2)
        placeholder = by_name['Feed · Player 1 game']['settings']['items'][0]
        assert by_name[placeholder['name']]['settings']['width'] == 886
        assert game['crop_right'] == 1920 - 886

    async def test_one_stage_without_a_layout_is_refused(self, event):
        staff = await _staff()
        stage = await Stage.create(name='Stage 1')
        with pytest.raises(ValueError, match='no layout'):
            await StreamLayoutService().obs_scene_collection(staff, stage.id)
