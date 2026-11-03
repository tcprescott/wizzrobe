"""Schemas for ``/api/graphql``. Documentation only: the endpoint parses its own
body so it can refuse batches and oversized queries before parsing them."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class GraphQLRequest(BaseModel):
    query: str = Field(..., description='One GraphQL query document.')
    variables: Optional[Dict[str, Any]] = None
    operationName: Optional[str] = Field(None, description='Which operation to run when the document has several.')


class GraphQLErrorOut(BaseModel):
    message: str
    path: Optional[List[Any]] = Field(None, description='The field that failed; the rest of `data` still answers.')
    extensions: Dict[str, Any] = Field(
        ..., description='`code` is `NOT_FOUND`, `FORBIDDEN`, `BAD_USER_INPUT`, `GRAPHQL_VALIDATION_FAILED`, '
        '`GRAPHQL_PARSE_FAILED`, `QUERY_TOO_COMPLEX`, `QUERY_TOO_LARGE`, `TIMEOUT`, `BAD_REQUEST` or `INTERNAL`.',
    )


class GraphQLResponse(BaseModel):
    data: Optional[Dict[str, Any]] = Field(None, description='Null when the query did not validate.')
    errors: Optional[List[GraphQLErrorOut]] = None
