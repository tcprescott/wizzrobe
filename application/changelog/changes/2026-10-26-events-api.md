### New

- The REST API and the Wizzrobe MCP connector work in any event: pass `event` with its link name (`?event=sgl27`, or an `event` argument on a tool), or leave it out for the default event. A call about one match or shift works in that row's own event. New `/api/events` routes and event tools list, create, copy, go live, archive and configure events, and `whoami` lists each community's events.
- The audit log, sent messages and engagement telemetry can be narrowed to one event over the API and the connector.
- Webhooks can be limited to some events through the API. Existing webhooks keep sending every event's updates, except SG Schedule Update, which sends SGL 2026's only.
- A new staff help article, "Running more than one event", covers starting next year's event, going live, the default event and archiving. Its help icon is on Admin → Events.

### Fixed

- An export started while looking at another event, or at an archived one, now exports that event's tournaments and volunteers instead of the default event's.
