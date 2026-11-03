"""Limits a query must pass before any resolver runs.

Depth, document size and alias count use Strawberry's own extensions. The row
estimate is ours: it multiplies the ``first:`` argument down each path, so a
query asking for 100 matches with 100 players each is refused at validation
rather than discovered in the database.
"""

from typing import Dict, Optional

from graphql import (
    FieldNode,
    FragmentDefinitionNode,
    FragmentSpreadNode,
    GraphQLError,
    GraphQLObjectType,
    InlineFragmentNode,
    IntValueNode,
    OperationDefinitionNode,
    SelectionSetNode,
    ValidationRule,
    get_named_type,
)
from strawberry.extensions import AddValidationRules, MaxAliasesLimiter, MaxTokensLimiter, QueryDepthLimiter

MAX_BODY_BYTES = 16 * 1024
MAX_DEPTH = 7
MAX_TOKENS = 2000
MAX_ALIASES = 30
MAX_ROWS = 5000
DEFAULT_PAGE = 50
MAX_PAGE = 100
TIMEOUT_SECONDS = 10.0


def _page_size(field: FieldNode, takes_first: bool) -> int:
    if not takes_first:
        return 1
    for arg in field.arguments or ():
        if arg.name.value == 'first':
            if isinstance(arg.value, IntValueNode):
                return max(0, min(int(arg.value.value), MAX_PAGE))
            return MAX_PAGE
    return DEFAULT_PAGE


class RowEstimateRule(ValidationRule):
    def enter_operation_definition(self, node: OperationDefinitionNode, *_args) -> None:
        schema = self.context.schema
        root = schema.get_root_type(node.operation)
        if root is None:
            return
        fragments = {
            d.name.value: d for d in self.context.document.definitions if isinstance(d, FragmentDefinitionNode)
        }
        rows = self._cost(node.selection_set, root, fragments, set())
        if rows > MAX_ROWS:
            self.report_error(GraphQLError(
                f'This query could return about {rows} rows; the limit is {MAX_ROWS}. '
                'Ask for smaller pages with first:.',
                node,
                extensions={'code': 'QUERY_TOO_COMPLEX'},
            ))

    def _cost(
        self,
        selection_set: Optional[SelectionSetNode],
        parent: object,
        fragments: Dict[str, FragmentDefinitionNode],
        seen: set[str],
    ) -> int:
        if selection_set is None or not isinstance(parent, GraphQLObjectType):
            return 0
        total = 0
        for selection in selection_set.selections:
            if isinstance(selection, FieldNode):
                field_def = parent.fields.get(selection.name.value)
                if field_def is None:
                    continue
                size = _page_size(selection, 'first' in field_def.args)
                child = get_named_type(field_def.type)
                total += size * (1 + self._cost(selection.selection_set, child, fragments, seen))
            elif isinstance(selection, InlineFragmentNode):
                target = (
                    self.context.schema.get_type(selection.type_condition.name.value)
                    if selection.type_condition else parent
                )
                total += self._cost(selection.selection_set, target, fragments, seen)
            elif isinstance(selection, FragmentSpreadNode):
                name = selection.name.value
                fragment = fragments.get(name)
                if fragment is None or name in seen:
                    continue
                target = self.context.schema.get_type(fragment.type_condition.name.value)
                total += self._cost(fragment.selection_set, target, fragments, seen | {name})
        return total


def limit_extensions() -> list:
    return [
        lambda: MaxTokensLimiter(max_token_count=MAX_TOKENS),
        lambda: QueryDepthLimiter(max_depth=MAX_DEPTH),
        lambda: MaxAliasesLimiter(max_alias_count=MAX_ALIASES),
        lambda: AddValidationRules([RowEstimateRule]),
    ]
