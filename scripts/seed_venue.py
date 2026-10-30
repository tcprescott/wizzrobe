"""Dev-seed fixtures for one tenant's venue: stages, stations, system config.

Split out of ``seed_dev.py`` when that module crossed the 800-line budget again,
following the ``seed_equipment_for_tenant`` / ``seed_volunteers_for_tenant``
convention already used there: one module per domain, called from inside the
caller's ``tenant_scope``. Idempotent and tenant-stamped like every other seeded
row.

The three fixtures belong together because they describe the same room: what the
matches run on, where the players sit, and the hours and limits the schedule is
checked against. Tenant A gets the numbered station pool and enforces numeric
labels; tenant B has no pool at all and keeps the historical free-text station
field, which is the other half of that setting.
"""

import json
from datetime import date, timedelta

from application.services.system_config_service import EVENT_CONFIG_KEYS, EVENT_DATE_FIELDS
from models import Event, Stage, Station, StationSide, SystemConfiguration, Tenant


async def seed_venue_for_tenant(
    tenant: Tenant, today: date, event_days: list[date]
) -> None:
    """Seed one tenant's stages, station pool and system configuration.

    ``today`` and ``event_days`` come from the caller because the venue hours,
    the volunteer fixtures and player availability all have to describe the same
    three-day window.
    """
    for name, url in [
        ("Stage 1", "https://twitch.tv/wizzrobe"),
        ("Stage 2", "https://twitch.tv/wizzrobe2"),
        ("Stage 3", "https://twitch.tv/wizzrobe3"),
    ]:
        await Stage.get_or_create(
            name=name, tenant=tenant,
            defaults={"stream_url": url, "is_active": True},
        )
    print(f"    [{tenant.slug}] stages ok")

    if tenant.slug == "default":
        layout = (
            [(f"{n}", "North wall", StationSide.LEFT, n) for n in range(1, 7)]
            + [(f"{n}", "South wall", StationSide.RIGHT, n - 6) for n in range(7, 13)]
            + [("13", "Overflow", None, None)]
        )
        for idx, (nm, section, side, pos) in enumerate(layout):
            await Station.get_or_create(
                name=nm, tenant=tenant,
                defaults={"section": section, "side": side,
                          "position": pos, "sort_order": idx},
            )
        print(f"    [{tenant.slug}] stations ok")
    else:
        print(f"    [{tenant.slug}] stations skipped (free-text fallback fixture)")
    venue_hours = {
        event_days[0].isoformat(): {"open": "10:00", "close": "23:00"},
        event_days[1].isoformat(): {"open": "09:00", "close": "23:00"},
        event_days[2].isoformat(): {"open": "09:00", "close": "20:00"},
    }
    config_specs = [
        ("event_start_date", today.isoformat()),
        ("event_end_date", (today + timedelta(days=2)).isoformat()),
        ("max_concurrent_players", "12"),
        ("max_concurrent_stages", "3"),
        ("volunteer_reminder_lead_minutes", "90"),
        ("volunteer_comp_tiers", "8, 12, 16"),
        ("tournament_hours_by_date", json.dumps(venue_hours, sort_keys=True)),
        ("station_format", "numeric" if tenant.slug == "default" else "free"),
        ("discord_auto_join", "true" if tenant.slug == "second" else "false"),
        ("join_requests_enabled", "false" if tenant.slug == "second" else "true"),
        ("discord_invite_url", f"https://discord.gg/wizzrobe-{tenant.slug}"),
    ]
    default_event = await Event.get(tenant=tenant, is_default=True)
    event_config = dict(default_event.config or {})
    for key, val in config_specs:
        if key in EVENT_DATE_FIELDS:
            column = EVENT_DATE_FIELDS[key]
            if getattr(default_event, column) is None:
                setattr(default_event, column, date.fromisoformat(val))
        elif key in EVENT_CONFIG_KEYS:
            event_config.setdefault(key, val)
        else:
            await SystemConfiguration.get_or_create(
                name=key, tenant=tenant, defaults={"value": val},
            )
    default_event.config = event_config
    await default_event.save(update_fields=["config", "starts_on", "ends_on"])
    print(f"    [{tenant.slug}] system config ok")
