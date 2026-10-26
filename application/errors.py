"""Shared service-layer error types.

These are raised by services and understood by the entry surfaces (web
presentation, REST API, Discord handlers). Keeping them in a leaf module
(no imports of services/repositories) lets every layer depend on them
without creating an import cycle.
"""

from datetime import datetime, timezone
from typing import Optional, TypeVar

T = TypeVar('T')


class NotFoundError(ValueError):
    """Raised by services when a requested entity does not exist.

    Subclasses :class:`ValueError` so existing ``pytest.raises(ValueError)``
    assertions and UI ``except ValueError`` handlers keep working unchanged.
    The REST API maps it to ``404`` ahead of the generic ``ValueError`` → ``400``.
    """


class FeatureDisabledError(NotFoundError):
    """Raised when a service is asked to act on a feature the tenant lacks.

    Subclasses :class:`NotFoundError` (and so ``ValueError``) deliberately: a
    feature a community has not enabled is *hidden*, not forbidden, which is the
    posture every other gate already takes — ``@protected_page(feature=…)`` and
    ``require_feature`` both 404 rather than 403, so role has no bearing and an
    unreleased feature never leaks its existence. Inheriting from
    ``NotFoundError`` means the REST layer maps it to 404 with no new plumbing and
    UI ``except ValueError`` handlers keep showing it as a notification.
    """


class MissingCredentialError(ValueError):
    """Raised when a roll needs a randomizer credential this tenant has not set.

    A plain :class:`ValueError` (400 over REST, a UI notification on the web), and
    a distinct type so ``MatchScheduleService.generate_seed`` can surface *this*
    message while still hiding raw randomizer/HTTP failures behind its generic
    "check the server logs". The distinction is safe to show: it names a
    credential the reader is authorized to configure, never an upstream response.
    """


class AlreadyBookedError(ValueError):
    """Raised when the slot a caller tried to book was booked by someone else first.

    A plain :class:`ValueError` everywhere else (400 over REST, a notification
    in the UI), and a distinct type so a booking dialog can tell "your input was
    wrong, fix it" from "this is stale, close and refresh": the second leaves a
    Schedule button on screen that can only fail again.

    Carries the facts rather than only a sentence, because *when* is a
    wall-clock question with no single answer: ``str()`` states it in UTC (what
    REST and logs promise), and a web surface rebuilds it on the viewer's clock
    with :meth:`describe`.
    """

    def __init__(
        self, *, what: str, scheduled_at: datetime,
        booker_name: Optional[str] = None, booked_by_you: bool = False,
    ) -> None:
        self.what = what
        self.scheduled_at = scheduled_at
        self.booker_name = booker_name
        self.booked_by_you = booked_by_you
        super().__init__(self.describe(
            scheduled_at.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
        ))

    def describe(self, when: str) -> str:
        """The refusal, with ``when`` already formatted for its reader."""
        if self.booked_by_you:
            return f"You already booked {self.what} for {when}."
        if self.booker_name:
            return f"{self.booker_name} already booked {self.what} for {when}."
        return f"{self.what[0].upper()}{self.what[1:]} is already booked for {when}."


def require_found(obj: Optional[T], label: str) -> T:
    """Return ``obj`` if present, otherwise raise :class:`NotFoundError`.

    Collapses the ubiquitous ``x = await repo.get(...); if x is None: raise``
    pattern into a single call. ``label`` names the entity for the message.
    """
    if obj is None:
        raise NotFoundError(f"{label} not found")
    return obj


class EventArchivedError(ValueError):
    """Raised when something tries to change a row of an archived event.

    An archived event is read-only on every surface (``application/event_writes.py``
    enforces it at the ORM). A plain :class:`ValueError` to the web UI, which
    shows it as a notification; REST maps it to ``409`` and MCP to
    ``read_only``, ahead of the generic ``ValueError``.
    """

    def __init__(self, event_name: str) -> None:
        self.event_name = event_name
        super().__init__(f"{event_name} is archived, so nothing in it can change.")
