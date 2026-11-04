"""Serializing preset-library views, shared by the REST router and the MCP tools."""

from typing import Any, Dict, List

import yaml

from application.services import LibraryPreset, PresetLibraryService


def library_row(view: LibraryPreset) -> dict:
    """One library preset as the REST and MCP list rows show it."""
    p = view.preset
    original = p.copied_from
    return {
        'id': p.id,
        'reference': view.reference,
        'name': p.name,
        'randomizer': p.randomizer,
        'randomizer_name': view.randomizer_name,
        'rollable': view.rollable,
        'can_manage': view.can_manage,
        'can_edit': view.can_edit,
        'description': p.description,
        'space': view.space_slug,
        'space_kind': view.space_kind.value,
        'space_name': view.space_name,
        'mine': view.mine,
        'listed': p.listed,
        'roll_count': p.roll_count,
        'updated_at': p.updated_at,
        'copied_from': f"{original.space.slug}/{original.name}" if original is not None and original.space else None,
        'copied_from_id': original.id if original is not None else None,
        'changed_since_copy': view.changed_since_copy,
    }


def odds_rows(rows: list) -> List[Dict[str, Any]]:
    return [
        {'setting': r.setting, 'options': [{'value': v, 'percent': p} for v, p in r.options]} for r in rows
    ]


def library_detail(view: LibraryPreset) -> dict:
    """One library preset with its settings, and a mystery weightset's YAML and odds."""
    detail = {**library_row(view), 'settings': view.preset.settings}
    mystery = PresetLibraryService.describe_mystery(view.preset)
    if mystery is not None:
        detail['odds'] = odds_rows(mystery.odds)
        detail['settings_yaml'] = yaml.safe_dump(view.preset.settings, sort_keys=False, allow_unicode=True)
    return detail
