## Summary

<!-- What changed, and why. One or two sentences is usually enough. -->

## Acceptance

<!-- Built from a spec in docs/specs/? Copy its acceptance items here and tick each one with how it was checked. A PR doesn't leave draft with an unticked item. Delete this section otherwise. -->

## Testing

<!-- How you verified this: commands run, pages exercised, what you checked by hand. -->

## Screenshots

<!-- UI change (pages/ or theme/)? Drop before/after screenshots here. Delete this section otherwise. -->

## Checklist

- [ ] Model change: migration generated (`poetry run aerich migrate && poetry run aerich upgrade`) and committed, and `docs/reference/data-model.md` updated
- [ ] Layer boundaries respected: no ORM writes in `pages/`/`theme/`/`api/`/`discordbot/`/`mcpserver/`; business logic in services, data access in repositories
- [ ] Tenant scoping: new/changed queries are scoped (`scoped(...)` reads, `tenant_id=` on writes), and a leak test covers any new tenant-scoped model
- [ ] Docs updated under `docs/`, and `scripts/seed_dev.py` extended so the change is visible in a dev environment
- [ ] User-visible change: a `application/changelog/changes/YYYY-MM-DD-<topic>.md` file added (or none needed: refactor, tests, CI, docs)
