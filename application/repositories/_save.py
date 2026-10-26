"""Persist only the columns a write names.

A bare ``obj.save()`` writes every column from the instance it is handed. When
that instance was loaded earlier (an edit dialog's tournament, a page's
``user``, a bracket match read before another result advanced into it), the
save puts back whatever anyone else changed since. Naming the fields keeps the
write to what this caller meant to change. ``check_bare_save`` blocks new bare
saves outside create paths.
"""

from tortoise.models import Model


async def save_fields(obj: Model, *fields: str) -> None:
    """Save ``fields`` (model attribute names; FKs by relation name) plus ``auto_now`` stamps.

    A row that has never been saved is inserted whole, as Tortoise ignores
    ``update_fields`` on insert.
    """
    fmap = obj._meta.fields_map
    columns = list(dict.fromkeys(getattr(fmap.get(k), 'source_field', None) or k for k in fields))
    columns += [n for n, f in fmap.items() if getattr(f, 'auto_now', False) and n not in columns]
    if columns:
        await obj.save(update_fields=columns)
