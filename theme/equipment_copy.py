"""What the asset page says to a viewer it offers no buttons to.

The check-in gate is asymmetric on purpose: ``can_checkout_equipment`` admits any
signed-in member, borrowing to themselves — the QR code on a loaner is scanned by
whoever is holding it, usually a player with no roles — while
``can_checkin_equipment`` is manager-only, so a return is recorded by someone
accountable for the item coming back. That is settled. What was missing is any
*explanation* — a volunteer scanning the label of the cable in their own hand got
a name, a status badge, a holder line and no controls at all, with nothing to
distinguish "you are not the one who records returns" from "this page is broken".

Pure so it can be tested without a client: it takes only the predicates the page
has already computed and returns the one line to render, or ``None`` when the
viewer has a button and needs no prose.
"""

__all__ = ['CHECKIN_GUIDANCE', 'HOLDER_PREFIX', 'RETIRED_GUIDANCE', 'equipment_guidance']

CHECKIN_GUIDANCE = (
    'Returns are recorded by staff or an equipment manager — hand it to them to '
    'check in.'
)
HOLDER_PREFIX = 'You have this checked out.'
RETIRED_GUIDANCE = 'This asset is retired, so it cannot be checked out.'
NOT_BORROWABLE_GUIDANCE = (
    'This account cannot borrow equipment — ask staff or an equipment manager '
    'if you need this.'
)


def equipment_guidance(
    *,
    status: str,
    is_checked_out: bool,
    viewer_is_holder: bool,
    can_checkout: bool,
    can_checkin: bool,
    can_manage: bool,
) -> str | None:
    """The guidance line for this viewer, or ``None`` if they have a control.

    ``status`` is the raw ``EquipmentStatus`` value. ``is_checked_out`` comes from
    the open loan rather than the status field, because the loan is what decides
    whether a check-in button appears.
    """
    if can_manage:
        return None
    if is_checked_out and not can_checkin:
        if viewer_is_holder:
            return f'{HOLDER_PREFIX} {CHECKIN_GUIDANCE}'
        return CHECKIN_GUIDANCE
    if is_checked_out:
        return None
    if status == 'retired':
        return RETIRED_GUIDANCE
    if not can_checkout:
        return NOT_BORROWABLE_GUIDANCE
    return None
