"""What every resolver can read about the request it answers."""

from dataclasses import dataclass
from typing import Optional

from strawberry.fastapi import BaseContext

from application.api_scopes import ApiScope, parse_scope
from models import ApiToken, ApiTokenOrigin, User


@dataclass
class GraphContext(BaseContext):
    actor: User
    token: ApiToken

    @property
    def scopes(self) -> Optional[frozenset[ApiScope]]:
        """The app grant's scopes, or None for an unscoped personal token."""
        if self.token.origin == ApiTokenOrigin.PAT.value:
            return None
        return parse_scope(self.token.scope)
