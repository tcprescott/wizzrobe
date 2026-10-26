"""Dev seed: a stream layout, its art, and every stage's OBS overlay.

The art is drawn here (an ember gradient with two game frames, three camera
frames and name bars) and run through the same ``prepare_art`` an upload goes
through. The game and player-camera frames are transparent in the art; the
commentary camera is a solid panel with a Video hole box over it, so both ways
of cutting a hole are on show. Stage 1 shows the layout and Stage 2 has it
pinned to its first booked match; Stage 3 has an overlay with no layout.

Idempotent: the layout is keyed by ``(event, name)``, overlays by stage.
"""

import hashlib
import secrets
from io import BytesIO

from PIL import Image, ImageDraw

from application.services.stream_layout_rules import clean_boxes
from application.services.stream_layout_service import TOKEN_PREFIX, prepare_art
from models import Match, Stage, StageOverlay, StreamLayout, StreamLayoutArt, Tenant, User

W, H = 1920, 1080
_GAMES = [(27, 96, 846, 675), (1050, 96, 846, 675)]
_CAMS = [(27, 777, 483, 219), (1413, 777, 483, 219)]
_COMMS = (738, 777, 447, 219)
_BARS = [(282, 33, 462, 54), (753, 33, 418, 54), (1180, 33, 462, 54)]


def _art() -> bytes:
    img = Image.new('RGBA', (W, H))
    draw = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        draw.line([(0, y), (W, y)], fill=(int(70 - 50 * t), int(14 + 4 * t), int(8 + 6 * t), 255))
    for x, y, w, h in _BARS:
        draw.rectangle([x, y, x + w, y + h], fill=(12, 6, 4, 255), outline=(232, 146, 52, 255), width=3)
    draw.rectangle([_COMMS[0], _COMMS[1], _COMMS[0] + _COMMS[2], _COMMS[1] + _COMMS[3]],
                   fill=(255, 255, 255, 255))
    for x, y, w, h in _GAMES + _CAMS:
        draw.rectangle([x - 3, y - 3, x + w + 3, y + h + 3], outline=(232, 146, 52, 255), width=3)
        draw.rectangle([x, y, x + w, y + h], fill=(0, 0, 0, 0))
    out = BytesIO()
    img.save(out, 'PNG')
    return out.getvalue()


def _text(field: str, box: tuple, size: int, **extra) -> dict:
    x, y, w, h = box
    return {'kind': 'field', 'field': field, 'x': x, 'y': y, 'w': w, 'h': h, 'size': size,
            'font': 'Inter', 'weight': 800, 'color': '#FFFFFF', 'align': 'center',
            'upper': True, 'shadow': True, **extra}


def _boxes() -> list[dict]:
    return clean_boxes([
        _text('player_name', _BARS[0], 33, which=1),
        _text('tournament_round', _BARS[1], 24, color='#F6B04A'),
        _text('player_name', _BARS[2], 33, which=2),
        _text('player_pronouns', (27, 33, 246, 54), 22, which=1, upper=False),
        _text('player_pronouns', (1651, 33, 245, 54), 22, which=2, upper=False),
        _text('commentators', (516, 777, 216, 219), 24),
        _text('up_next', (1191, 777, 216, 219), 20),
        _text('seed_code', (27, 1011, 1866, 51), 22, upper=False),
        {'kind': 'hole', 'x': _COMMS[0], 'y': _COMMS[1], 'w': _COMMS[2], 'h': _COMMS[3], 'radius': 12,
         'label': 'Comms cam'},
    ], W, H)


async def seed_stream_layouts_for_tenant(tenant: Tenant, staff: User) -> None:
    from application.services.event_service import EventService

    event_id = await EventService.default_event_id(tenant.id)
    if event_id is None:
        return
    layout, _ = await StreamLayout.get_or_create(
        tenant=tenant, event_id=event_id, name='2-player race',
        defaults={'width': W, 'height': H, 'boxes': _boxes(), 'created_by': staff},
    )
    if not await StreamLayoutArt.exists(layout_id=layout.id):
        content, _w, _h = prepare_art(_art())
        sha256 = hashlib.sha256(content).hexdigest()
        await StreamLayoutArt.create(tenant=tenant, layout=layout, content=content, sha256=sha256)
        layout.art_sha256 = sha256
        await layout.save(update_fields=['art_sha256'])

    stages = await Stage.filter(tenant=tenant).order_by('name')
    for i, stage in enumerate(stages[:3]):
        defaults: dict = {'event_id': stage.event_id, 'token': TOKEN_PREFIX + secrets.token_urlsafe(24),
                          'layout': layout if i < 2 else None}
        if i == 1:
            booked = await Match.filter(tenant=tenant, stage=stage, finished_at=None) \
                .order_by('scheduled_at').first()
            if booked is not None:
                defaults.update(pinned_match=booked, pinned_by=staff)
        await StageOverlay.get_or_create(tenant=tenant, stage=stage, defaults=defaults)
    print(f"    [{tenant.slug}] stream layout ok")
