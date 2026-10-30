"""An OBS scene collection for a set of stage overlays, so a broadcaster
imports the scenes instead of building them (Scene Collection → Import).

Pure: takes plain :class:`ObsScene` specs, returns the collection as a dict
OBS 28+ reads. Every video hole name becomes one **feed scene** ("Feed ·
Player 1 game") holding a placeholder the size of that hole; each stage scene
nests the feeds into its holes, under a Browser source for the Wizzrobe
overlay. The broadcaster puts each capture into its feed scene once, and every
layout that names a hole the same way picks it up.

A nested scene is canvas-sized, so the feed's placeholder sits at its
top-left and each stage scene crops the nested feed to that rectangle, then
fits it to the hole with "scale to inner bounds". Holes of slightly different
sizes across layouts therefore share a feed without stretching.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

FEED_PREFIX = 'Feed · '
OVERLAY_SOURCE = 'Wizzrobe overlay'
_NAMESPACE = uuid.UUID('6f1c7d0e-5b8a-4b8e-9a51-2f0d1d9b7c3a')
# OBS packs colours as 0xAABBGGRR.
_PLACEHOLDER_COLOUR = 0xFF505050
_ALIGN_TOP_LEFT = 5
_BOUNDS_NONE, _BOUNDS_SCALE_INNER = 0, 2


@dataclass(frozen=True)
class ObsHole:
    label: str
    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True)
class ObsScene:
    name: str
    overlay_url: str
    width: int
    height: int
    holes: tuple[ObsHole, ...] = field(default_factory=tuple)


def _uuid(*parts: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, '\x1f'.join(parts)))


def _source(kind: str, name: str, settings: dict) -> dict:
    return {
        'id': kind, 'versioned_id': kind, 'name': name, 'uuid': _uuid('source', name),
        'settings': settings, 'enabled': True, 'flags': 0, 'mixers': 0, 'muted': False,
        'volume': 1.0, 'balance': 0.5, 'sync': 0, 'monitoring_type': 0, 'hotkeys': {},
        'private_settings': {}, 'deinterlace_mode': 0, 'deinterlace_field_order': 0,
        'push-to-mute': False, 'push-to-talk': False, 'filters': [],
    }


def _item(item_id: int, source: dict, x: int, y: int, *, bounds: tuple[int, int] | None = None,
          crop: tuple[int, int] = (0, 0)) -> dict:
    return {
        'id': item_id, 'name': source['name'], 'source_uuid': source['uuid'],
        'visible': True, 'locked': False, 'rot': 0.0,
        'pos': {'x': float(x), 'y': float(y)}, 'scale': {'x': 1.0, 'y': 1.0},
        'align': _ALIGN_TOP_LEFT,
        'bounds_type': _BOUNDS_SCALE_INNER if bounds else _BOUNDS_NONE,
        'bounds_align': 0,
        'bounds': {'x': float(bounds[0]), 'y': float(bounds[1])} if bounds else {'x': 0.0, 'y': 0.0},
        'crop_left': 0, 'crop_top': 0, 'crop_right': crop[0], 'crop_bottom': crop[1],
        'scale_filter': 'disable', 'blend_method': 'default', 'blend_type': 'normal',
        'group_item_backup': False, 'private_settings': {},
        'show_transition': {'duration': 0}, 'hide_transition': {'duration': 0},
    }


def _scene(name: str, items: list[dict]) -> dict:
    return _source('scene', name, {'custom_size': False, 'id_counter': len(items), 'items': items})


def _hole_labels(scene: ObsScene) -> list[str]:
    """Each hole's feed name; an unnamed hole gets a feed of its own."""
    return [h.label or f'{scene.name} hole {i + 1}' for i, h in enumerate(scene.holes)]


def build_collection(name: str, scenes: list[ObsScene]) -> dict:
    """The scene collection JSON. Stage scenes first (in order), then the feeds."""
    if not scenes:
        raise ValueError('No stage has a layout yet, so there are no scenes to make.')
    canvas_w, canvas_h = scenes[0].width, scenes[0].height

    feed_size: dict[str, tuple[int, int]] = {}
    for scene in scenes:
        for label, hole in zip(_hole_labels(scene), scene.holes, strict=True):
            w, h = feed_size.get(label, (0, 0))
            if hole.w * hole.h > w * h:
                feed_size[label] = (hole.w, hole.h)

    sources: list[dict] = []
    feed_sources: dict[str, dict] = {}
    for label, (w, h) in feed_size.items():
        placeholder = _source('color_source_v3', f'{label} · replace with your capture',
                              {'color': _PLACEHOLDER_COLOUR, 'width': w, 'height': h})
        feed = _scene(FEED_PREFIX + label, [_item(1, placeholder, 0, 0)])
        sources += [placeholder, feed]
        feed_sources[label] = feed

    stage_scenes = []
    for scene in scenes:
        overlay = _source('browser_source', f'{OVERLAY_SOURCE} · {scene.name}', {
            'url': scene.overlay_url, 'width': scene.width, 'height': scene.height,
            'fps_custom': False, 'reroute_audio': False, 'shutdown': False,
            'restart_when_active': False, 'css': '',
        })
        items: list[dict] = []
        for label, hole in zip(_hole_labels(scene), scene.holes, strict=True):
            fw, fh = feed_size[label]
            items.append(_item(
                len(items) + 1, feed_sources[label], hole.x, hole.y,
                bounds=(hole.w, hole.h), crop=(max(0, canvas_w - fw), max(0, canvas_h - fh)),
            ))
        items.append(_item(len(items) + 1, overlay, 0, 0))
        sources.append(overlay)
        stage_scenes.append(_scene(scene.name, items))

    sources = stage_scenes + sources
    first = stage_scenes[0]['name']
    return {
        'name': name,
        'current_scene': first, 'current_program_scene': first,
        'scene_order': [{'name': s['name']} for s in stage_scenes]
        + [{'name': f['name']} for f in feed_sources.values()],
        'sources': sources, 'groups': [],
        'current_transition': 'Fade', 'transition_duration': 300, 'transitions': [],
        'quick_transitions': [], 'saved_projectors': [], 'modules': {},
        'preview_locked': False, 'scaling_enabled': False, 'scaling_level': 0,
        'scaling_off_x': 0.0, 'scaling_off_y': 0.0,
    }
