# GraphQL API (`/api/graphql`)

A read-only GraphQL endpoint beside the REST API and the MCP server. Same data, same tokens, same community walls; the difference is that a client asks for a nested view (an event's matches with their players, stage and seed) in one round trip.

The graph covers core event data: `viewer`, `events`, `event(slug)` with its tournaments, stages and scheduled matches, each tournament's bracket, and `tournament(id)` / `stage(id)` / `match(id)` lookups. Volunteers, check-in, qualifiers, race rooms and staff reads are later phases; the plan is in the design notes the GraphQL thread keeps in the project files (`graphql-api/design.md`).

```graphql
query RunSheet {
  event {                        # the default event; or event(slug: "sgl26")
    matches(first: 50, start: "2026-10-23T14:00:00Z") {
      nodes {
        id scheduledAt
        tournament { name shortName }
        stage { name }
        seed { url info }
        players { user { displayName } station finishRank }
        crew { role user { displayName } }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
```

## Shape

| Piece | Where |
|---|---|
| Endpoint (`POST /api/graphql`) and GraphiQL (`GET /api/graphql`) | `api/graphql/router.py` |
| Root `Query` and the schema | `api/graphql/schema.py` |
| Object types | `api/graphql/types.py` |
| Bracket types (generated from the REST bracket schemas) | `api/graphql/brackets.py` |
| Relay cursors | `api/graphql/pagination.py` |
| Per-request context (`actor`, `token`, `scopes`) | `api/graphql/context.py` |
| Limits (depth, tokens, aliases, row estimate, body size, timeout) | `api/graphql/limits.py` |
| Service exceptions → `extensions.code` | `api/graphql/errors.py` |
| Committed schema (the contract) | `docs/reference/schema.graphql` |
| OpenAPI request/response models | `api/schemas/graphql.py` |

It lives under `api/`, so `enforce_architecture.py` treats it as presentation: resolvers call services and may not import repositories.

## The door

The endpoint is a plain FastAPI route on the API router, not Strawberry's `GraphQLRouter`, so it gets exactly what every REST route gets:

- `rate_limit` and `tenant_context_scope` from the aggregating router;
- `resolve_token`: personal and app tokens accepted, MCP OAuth tokens refused with `401`, inactive community or lapsed membership `403`, tenant and default event bound;
- the app-scope check: `POST /graphql` is under `schedule:read` in `api/scopes.py`, listed in `READ_ONLY_POSTS` so the scope test knows it is a read;
- the per-app rate limit.

Personal tokens are unscoped; a read-only PAT works because every operation is a query. Fields that show more than a member sees will carry their own scope check as they're added. No WebSocket, no queries over `GET`, no batched operations (a JSON array body is a `400`).

GraphiQL at `GET /api/graphql` is a static page (listed under `PUBLIC`); its queries go back to the `POST` with whatever token the viewer pastes into the Headers pane.

## Events

`event(slug)` with no slug is the community's default event. A draft the viewer can't open (`EventService.can_view`) and a slug from another community are both `NOT_FOUND`, reported at the field's path. `events` is `EventService.visible_events`.

Each object carries its `event_id` (a `strawberry.Private` field) and its child resolvers run in `event_scope(parent.event_id)`. They can't rely on a contextvar set by a parent resolver, because sibling fields resolve concurrently and a contextvar set in one never reaches another.

`tournament(id)`, `stage(id)` and `match(id)` work in the row's own event (`EventService.call_event_id` with the row), so a match in another live event answers without naming it, and one in a draft the viewer can't open is `NOT_FOUND`. Another community's id is `NOT_FOUND` too: the services' reads are tenant-scoped.

## Types and where they come from

| Field | Service read | Visibility it inherits |
|---|---|---|
| `Event.tournaments`, `Query.tournament` | `TournamentService.list_for_viewer` / `get_for_viewer` | inactive tournaments only for `can_view_admin` |
| `Event.stages`, `Query.stage` | `StageService.get_all_stages` / `get_stage_by_id` | any member |
| `Event/Tournament/Stage.matches`, `Query.match` | `MatchService.schedule_page` / `get_by_id` | any member; crew via `MatchService.approved_crew`, so pending signups never show |
| `Tournament.bracket` | `BracketOverviewService.get_tournament_bracket` | unpublished stages hidden, `userId` only for staff and the tournament's admins, the bracket source's feature flag |

`TournamentBracket` and its children are generated from `api/schemas/bracket_overview.py` with Strawberry's pydantic integration, so the bracket in GraphQL is the REST/MCP payload field for field, descriptions included.

## Pagination

Match lists are Relay connections: `first` (default 50, at most 100) and `after`, answering `edges { cursor node }`, `nodes` and `pageInfo { hasNextPage endCursor }`. The cursor is the last row's `(scheduled_at, id)` keyset, so a match booked into an earlier page while a client pages forward neither repeats nor shifts later pages. Unscheduled matches are left out: a NULL sorts first on SQLite and last on Postgres, so it can't hold a keyset.

A page is one match query plus one batched query per prefetched relation (tournament, stage, seed, players, three crew tables), whatever its size; `test_a_page_costs_the_same_queries_for_3_or_12_matches` holds that. A connection nested under a list (each tournament's matches) runs one page per parent, which the row estimate bounds.

## Limits

All checked before any resolver runs.

| Limit | Value | Code when refused |
|---|---|---|
| Body size | 16 KB | `QUERY_TOO_LARGE` (HTTP 413) |
| Tokens | 2,000 | `GRAPHQL_PARSE_FAILED` |
| Depth | 7 | `GRAPHQL_VALIDATION_FAILED` |
| Aliases | 30 | `GRAPHQL_VALIDATION_FAILED` |
| Estimated rows | 5,000 | `QUERY_TOO_COMPLEX` |
| Run time | 10 s | `TIMEOUT` |

The row estimate (`RowEstimateRule`) multiplies each field's `first:` down its path, through inline fragments and fragment spreads. A field that takes `first` but isn't given it counts as 50; a variable counts as the 100 maximum, since validation runs before variables are known.

## Errors

A resolver lets the service's exception propagate. The field resolves to `null`, the error carries its `path`, and the rest of the query still answers.

| Raised | `extensions.code` |
|---|---|
| `NotFoundError` (a disabled feature included) | `NOT_FOUND` |
| `PermissionError` | `FORBIDDEN` |
| `ValueError` | `BAD_USER_INPUT` |
| anything else | `INTERNAL`, message masked, logged for Sentry |

HTTP is `200` when the query ran (errors or not), `400` when it didn't validate (`data` is `null`), and the REST `401`/`403`/`429` at the door. `WizzrobeSchema.process_errors` is silenced so expected refusals don't reach Sentry.

## Telemetry

One `category='api'` row per operation, `event_type='graphql.query'` (`TelemetryEventType.GRAPHQL_QUERY`, surface `graphql`). The target is `graphql <root fields>`, e.g. `graphql events viewer`, taken from the schema's root fields rather than the client's operation name. `telemetry_summary` reports `graphql_users`.

## The contract

`docs/reference/schema.graphql` is printed from the code; `tests/api/test_graphql.py::test_the_committed_schema_matches_the_code` fails when they disagree. Regenerate with:

```
poetry run python -m api.graphql.export > docs/reference/schema.graphql
```

Removing or renaming a field breaks clients: deprecate it (`strawberry.field(deprecation_reason=...)`) instead. Row ids are `Int`, matching REST; times are UTC; Discord ids are strings, matching MCP.

## Adding a type

1. Add the type to `types.py` with a classmethod that builds it from a service result.
2. Resolve it through a service method that takes the viewer as `actor`, so the service's visibility rules apply, inside `event_scope` of the parent's event.
3. Paginate lists as Relay connections taking `first` / `after` (keyset), so the row estimate sees them. Prefetch to-one relations in the page's query rather than resolving them one row at a time.
4. Regenerate `schema.graphql` and add a cross-community leak test for each new root field.
