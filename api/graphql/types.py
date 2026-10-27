"""The graph's object types. Each maps a service result, never a raw model
lookup, so the visibility rules the service applies come with it."""

from datetime import date
from typing import List, Optional

import strawberry

from application.services.event_service import EventRef
from models import EventStatus, Role, User

strawberry.enum(EventStatus)
strawberry.enum(Role)


@strawberry.type(name='User', description='A person, as any member of the community sees them.')
class UserNode:
    id: int
    username: str
    display_name: Optional[str]
    pronouns: Optional[str]
    discord_id: Optional[str] = strawberry.field(
        description='Too large for a JSON number, so sent as a string.',
    )

    @classmethod
    def of(cls, user: User) -> 'UserNode':
        return cls(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            pronouns=user.pronouns,
            discord_id=str(user.discord_id) if user.discord_id is not None else None,
        )


@strawberry.type(description='Who this token acts as, in the community it belongs to.')
class Viewer:
    user: UserNode
    roles: List[Role] = strawberry.field(description='Roles held in this community.')
    is_staff: bool
    scopes: Optional[List[str]] = strawberry.field(
        description="An app grant's scopes. Null for a personal token, which is unscoped.",
    )


@strawberry.type(description="One of the community's events, such as an SG Live weekend.")
class Event:
    id: int
    slug: str
    name: str
    status: EventStatus
    is_default: bool = strawberry.field(description='The event a query without a slug uses.')
    starts_on: Optional[date]
    ends_on: Optional[date]

    @classmethod
    def of(cls, ref: EventRef) -> 'Event':
        return cls(
            id=ref.id,
            slug=ref.slug,
            name=ref.name,
            status=ref.status,
            is_default=ref.is_default,
            starts_on=ref.starts_on,
            ends_on=ref.ends_on,
        )
