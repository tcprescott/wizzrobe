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

    # Only tenant A defines a station pool: a community with no stations keeps the
    # historical free-text station field, and tenant B is that fixture.
    if tenant.slug == "default":
        # Sided and numbered so the check-in draw has a room to work with:
        # six a side, neighbours one apart, more than the live matches
        # occupy. Station 13 has no layout — the "no side set" fixture.
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
    # System configuration — every key SystemConfigService reads, because a
    # key the seed omits is a Settings-tab field that reads as "not
    # configured" in dev and a code path (venue hours, the reminder lead, the
    # station-label format) that only ever runs on its fallback.
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
        # Tenant A's stations are the numbered pool seeded below, so it
        # enforces numeric labels; tenant B has no station pool and keeps the
        # free-text default — the two halves of the same setting.
        ("station_format", "numeric" if tenant.slug == "default" else "free"),
        # Both halves of Discord auto-join, the same way: tenant B lets its
        # server in, tenant A only offers the invite. ``outsider`` is in no mock
        # server (mock_discord_data.MOCK_NON_GUILD_MEMBERS), so it still meets
        # each door and reads both versions of its copy.
        ("discord_auto_join", "true" if tenant.slug == "second" else "false"),
        # And tenant B takes no join requests, so its door is the
        # Discord-only one: the case the switch exists for. Tenant A keeps
        # the default request form.
        ("join_requests_enabled", "false" if tenant.slug == "second" else "true"),
        ("discord_invite_url", f"https://discord.gg/wizzrobe-{tenant.slug}"),
    ]
    # The event's own settings go on the default event; the rest stay rows.
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
