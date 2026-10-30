"""The words the ADA accommodation surfaces use, in one place.

The profile card and the staff panel both describe a request's status and both
show the privacy disclaimer; keeping that copy here means the requester and
staff never read the same row two different ways. Pure, so it is testable
without a client.
"""

from models import AccommodationStatus

__all__ = ['STATUS_CHIP', 'STATUS_LABELS', 'privacy_disclaimer']

STATUS_LABELS: dict[AccommodationStatus, str] = {
    AccommodationStatus.NEW: 'New',
    AccommodationStatus.ACKNOWLEDGED: 'Acknowledged',
    AccommodationStatus.ARRANGED: 'Arranged',
    AccommodationStatus.WITHDRAWN: 'Withdrawn',
}

STATUS_CHIP: dict[AccommodationStatus, str] = {
    AccommodationStatus.NEW: 'wiz-chip--pending',
    AccommodationStatus.ACKNOWLEDGED: 'wiz-chip--live',
    AccommodationStatus.ARRANGED: 'wiz-chip--ok',
    AccommodationStatus.WITHDRAWN: 'wiz-chip--neutral',
}


def privacy_disclaimer(community: str) -> str:
    """The warning shown wherever accommodation details are written or read.

    Names the one wider audience on purpose: proctors see that a request is
    arranged (an "ADA:" line on the run sheets, and a button in the match's
    Ready to play? checklist that tells them to ask staff), never its text. A
    disclaimer that left them out would be promising less exposure than there is.
    """
    staff = f'{community} staff' if community else 'community staff'
    return (
        f'Only {staff} can read these notes. Once a request is arranged, '
        'proctors see an "ADA:" line with the player\'s name on their match '
        'run sheets, and a button in the match\'s Ready to play? checklist, '
        'but not what the notes say. We can\'t guarantee notes stay '
        'private, so please don\'t put anything sensitive or personal here. If '
        f'it is sensitive, contact {staff} directly instead.'
    )
