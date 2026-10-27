"""The read-only GraphQL API at ``/api/graphql``.

Another way in to the same data REST serves: one community per token, every
resolver over the gated service layer. See ``docs/features/graphql-api.md``.
"""

from api.graphql.router import router
from api.graphql.schema import schema

__all__ = ['router', 'schema']
