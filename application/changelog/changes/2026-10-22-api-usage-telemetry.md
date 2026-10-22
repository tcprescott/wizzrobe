### New

- Staff can see who uses the MCP connector and the REST API, and for what. Ask the connector for `telemetry_top` with `api` (most-called tools and routes) or `api_users` (who calls them, how often, and when they last did), or read the same from `/api/telemetry/top`. The telemetry summary now also counts distinct MCP and REST users.
