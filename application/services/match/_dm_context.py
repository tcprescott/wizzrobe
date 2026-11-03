"""Lazy service→service DM context helpers, shared across the schedule split.

``bracket_dm_context`` reaches into ``BracketService`` and ``_community_name``
into ``TenantService`` — both lazily, both non-raising, because a match DM must
never be lost over its subtitle or its footer. They live here rather than in
either half of ``match_schedule_service`` so the lifecycle module and the
notification mixin can share them without a cycle.
"""


async def bracket_dm_context(match_id: int) -> tuple[str, bool]:
    """``(bracket_line, frees_a_slot)`` for a match's DMs, or ``('', False)``.

    Service→service into ``BracketService`` (the same seam ``confirm_match`` uses
    for ``advance_if_linked``), kept here as a one-liner so the notification
    builders below — and ``CancellationMixin`` — don't each import the bracket
    service. Never raises: a match DM must not be lost over its subtitle.
    """
    from application.services.bracket_service import BracketService

    line, frees_slot = await BracketService().match_dm_context(match_id)
    if line:
        return line, frees_slot
    try:
        from application.repositories.match_repository import MatchRepository
        from application.services.bracket_set_service import BracketSetService

        match = await MatchRepository.get_by_id(match_id, prefetch_relations=False)
        label = await BracketSetService().label_for(match) if match is not None else None
    except Exception:
        label = None
    return (label or '', frees_slot)


async def bracket_line_for(match_id: int) -> str:
    """Just the series-context line — the transition DMs' half of the context."""
    line, _ = await bracket_dm_context(match_id)
    return line


async def _community_name() -> str:
    """The current tenant's community name for the embed footer, or '' if none.

    Thin alias over ``TenantService.current_community_name`` (non-raising,
    best-effort): a missing tenant or DB-less context just omits the footer
    rather than breaking a best-effort DM. The functions that need a tenant for
    their queries still enforce it with ``require_tenant_id`` as before.
    """
    from application.services.tenant_service import TenantService
    return await TenantService.current_community_name()
