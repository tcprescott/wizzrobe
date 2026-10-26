"""Fill the dev database with an event's worth of rows, plus years of history.

The dev seed covers states, not volume (docs/reference/dev-seed.md §6). This is
the volume half: a busy four-day event in one community and the records that
pile up over the app's lifespan, so every list, board, report and worker can be
timed against the row counts production will actually reach.

What it adds, at ``--scale 1``:

* **The event** in the target community (``--tenant``, default ``default``):
  2,000 members, 14 tournaments, ~1,300 matches spread from 36 hours ago to 60
  hours ahead (finished, racing, checked in and scheduled by the clock), crew
  and tracker signups, watchers, comments, acknowledgments, seeds, reschedule
  requests, availability, volunteer shifts and assignments, a check-in roster,
  equipment loans, community events, feedback and staff DMs.
* **The lifespan**: four past-year communities with their own event each, and
  the time-series tables that only ever grow — audit log, engagement telemetry,
  webhook deliveries — at several years' depth.

Rows go in with ``COPY``, so a full run takes a minute or two rather than an
hour. It is additive and **not** idempotent: run it once against a freshly
seeded dev database (``seed_dev.py`` first, it needs ``staff_user`` and the
default tenant's webhook). Every generated username starts ``scale_`` and every
past-year community slug starts ``scale-`` so the rows are easy to spot.

    poetry run python scripts/loadtest/seed_scale.py            # scale 1
    poetry run python scripts/loadtest/seed_scale.py --scale 2  # twice the event

Never point this at production.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tortoise import Tortoise, connections

RNG = random.Random(20261022)
DISCORD_BLOCK = 4_000_000_000_000_000_000

ROUNDS = (
    'Winners Round 1', 'Winners Round 2', 'Winners Round 3', 'Losers Round 1',
    'Losers Round 2', 'Losers Round 3', 'Winners Quarterfinal', 'Losers Quarterfinal',
    'Winners Semifinal', 'Losers Semifinal', 'Winners Final', 'Losers Final', 'Grand Final',
)
GAMES = (
    ('A Link to the Past Randomizer', 'ALTTPR', 'alttpr', 48),
    ('Ocarina of Time Randomizer', 'OoTR', 'ootr', 32),
    ('Super Metroid Randomizer', 'SMR', 'sm', 24),
    ('Final Fantasy Randomizer', 'FFR', 'ffr', 24),
    ('Zelda 1 Randomizer', 'Z1R', 'z1r', 24),
    ('Super Mario 64 Any%', 'SM64', None, 16),
    ('Donkey Kong 64 Randomizer', 'DK64', 'dk64', 16),
    ('Map Rando', 'Map Rando', 'smmaprando', 16),
    ('Link to the Past Door Rando', 'Doors', 'alttpr', 16),
    ('Wind Waker Randomizer', 'WWR', 'wwr', 16),
    ('Pokemon Red Randomizer', 'PKMN', None, 16),
    ('Castlevania SotN Rando', 'SotN', None, 16),
    ('Best of NES', 'BoNES', None, 16),
    ('SMZ3 Combo Randomizer', 'SMZ3', 'smz3', 16),
)
PAGES = (
    '/t/{slug}/', '/t/{slug}/home/schedule', '/t/{slug}/home/player', '/t/{slug}/home/my-schedule',
    '/t/{slug}/admin/schedule', '/t/{slug}/admin/users', '/t/{slug}/admin/tournaments',
    '/t/{slug}/volunteer/proctor-station', '/t/{slug}/live', '/t/{slug}/event-info',
    '/t/{slug}/admin/reports', '/t/{slug}/checkin', '/t/{slug}/brackets', '/t/{slug}/help',
)
INTERACTIONS = (
    ('interaction', 'ui.click'), ('interaction', 'ui.dialog_opened'),
    ('interaction', 'ui.dialog_closed'), ('interaction', 'tab.view'),
    ('domain', 'match.created'), ('domain', 'match.finished'), ('domain', 'crew.signed_up'),
)
AUDIT_ACTIONS = (
    'match.created', 'match.updated', 'match.seated', 'match.started', 'match.finished',
    'match.confirmed', 'match.seed_rolled', 'match.stage_assigned', 'crew.signed_up',
    'crew.approved', 'user.profile_updated', 'user.role_granted', 'check_in_entrant.checked_in',
    'equipment.checked_out', 'equipment.checked_in', 'volunteer.assigned', 'tournament.updated',
)
COMMENT_BODIES = (
    'Running a few minutes late, sorry!', 'Can we swap to station 12? Controller issue.',
    'Seed rolled, good luck both.', 'Please check in at the proctor desk.',
    'Restream wants this one on Stage 2 if possible.', 'GG!',
)


def utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc)


class Copier:
    """Thin COPY helper over Tortoise's asyncpg pool, with a running tally."""

    def __init__(self) -> None:
        self.client = connections.get('default')
        self.tally: dict[str, int] = {}

    async def copy(self, table: str, columns: Sequence[str], rows: Iterable[tuple]) -> int:
        rows = list(rows)
        if not rows:
            return 0
        async with self.client.acquire_connection() as conn:
            for start in range(0, len(rows), 50_000):
                await conn.copy_records_to_table(table, records=rows[start:start + 50_000], columns=list(columns))
        self.tally[table] = self.tally.get(table, 0) + len(rows)
        return len(rows)

    async def fetch(self, sql: str, *args: Any) -> list:
        async with self.client.acquire_connection() as conn:
            return await conn.fetch(sql, *args)

    async def fetchval(self, sql: str, *args: Any) -> Any:
        async with self.client.acquire_connection() as conn:
            return await conn.fetchval(sql, *args)

    async def next_ids(self, table: str, n: int) -> list[int]:
        """Reserve ``n`` ids from ``table``'s serial so FKs can be built offline."""
        seq = await self.fetchval("SELECT pg_get_serial_sequence($1, 'id')", f'"{table}"')
        rows = await self.fetch(f"SELECT nextval('{seq}') FROM generate_series(1, $1)", n)
        return [r[0] for r in rows]


async def make_users(cp: Copier, count: int, now: datetime) -> list[int]:
    start = await cp.fetchval("SELECT count(*) FROM \"user\" WHERE username LIKE 'scale\\_%'")
    ids = await cp.next_ids('user', count)
    tzs = (None, None, 'America/New_York', 'America/Chicago', 'America/Los_Angeles', 'Europe/London')
    rows = []
    for n, uid in enumerate(ids):
        i = start + n
        created = now - timedelta(days=RNG.randint(1, 1500))
        rows.append((
            uid, DISCORD_BLOCK + i, created, created, f'scale_{i:05d}', f'Runner {i:05d}',
            True, True, False, False, RNG.choice(tzs), True, False, False,
        ))
    await cp.copy('user', (
        'id', 'discord_id', 'created_at', 'updated_at', 'username', 'display_name',
        'is_active', 'dm_notifications', 'is_system', 'is_placeholder', 'timezone',
        'dm_match_comments', 'email_notifications', 'telegram_notifications',
    ), rows)
    return ids


async def make_tournaments(tenant: Any, year_offset: int, now: datetime) -> list[Any]:
    from models import Tournament

    out = []
    for name, short, gen, _ in GAMES:
        label = name if year_offset == 0 else f'{name} {now.year - year_offset}'
        out.append(await Tournament.create(
            tenant=tenant, name=f'{label} (scale)', short_name=short, seed_generator=gen,
            is_active=year_offset == 0, players_per_match=2, average_match_duration=90,
            max_match_duration=150, tournament_format='Double elimination, best of 1',
            required_commentators=2, required_trackers=1, allow_reschedule_requests=True,
            allow_player_match_requests=True, rules_url='https://example.invalid/rules',
            event_start_date=(now - timedelta(days=1)).date(), event_end_date=(now + timedelta(days=3)).date(),
        ))
    return out


async def _event_id(tenant: Any) -> int:
    """The community's default event, which every scale row lands in."""
    from application.services.event_service import EventService

    return (await EventService.ensure_first_event(tenant)).id


async def make_event(
    cp: Copier, tenant: Any, users: list[int], *, start: datetime, hours: int,
    match_target: int, now: datetime, staff_ids: list[int], live: bool,
) -> dict[str, Any]:
    """One event's matches and everything hanging off them."""
    from models import Stage

    tid = tenant.id
    eid = await _event_id(tenant)
    tournaments = await make_tournaments(tenant, 0 if live else max(1, now.year - start.year), now)
    stages = [await Stage.create(tenant=tenant, name=f'Scale Stage {n}', stream_url=f'https://example.invalid/stage{n}')
              for n in range(1, 5)]

    weights = [g[3] for g in GAMES]
    entrants: dict[int, list[int]] = {}
    tp_rows = []
    for t, (_, _, _, size) in zip(tournaments, GAMES, strict=True):
        pool = RNG.sample(users, min(len(users), size * 6))
        entrants[t.id] = pool
        tp_rows += [(t.id, u, tid, now, now) for u in pool]
    await cp.copy('tournamentplayers', ('tournament_id', 'user_id', 'tenant_id', 'created_at', 'updated_at'), tp_rows)

    match_ids = await cp.next_ids('match', match_target)
    seed_ids = await cp.next_ids('generatedseeds', match_target)
    m_rows, mp_rows, seed_rows, ack_rows = [], [], [], []
    by_match: dict[int, dict[str, Any]] = {}
    span = hours * 3600
    for n, mid in enumerate(match_ids):
        t = RNG.choices(tournaments, weights=weights)[0]
        at = start + timedelta(seconds=int(span * n / match_target) + RNG.randint(0, 900))
        at = at.replace(second=0, microsecond=0, minute=(at.minute // 15) * 15)
        on_stage = RNG.random() < 0.22
        stage_id = RNG.choice(stages).id if on_stage else None
        seated = started = finished = confirmed = None
        if now >= at + timedelta(minutes=95):
            seated, started, finished = at - timedelta(minutes=10), at, at + timedelta(minutes=RNG.randint(60, 120))
            confirmed = finished + timedelta(minutes=5) if RNG.random() < 0.9 else None
        elif now >= at:
            seated, started = at - timedelta(minutes=10), at
        elif now >= at - timedelta(minutes=15):
            seated = at - timedelta(minutes=5)
        seed_id = None
        if started is not None or RNG.random() < 0.15:
            seed_id = seed_ids[n]
            seed_rows.append((seed_id, f'https://example.invalid/seed/{mid}', 'Scale seed', at, at, tid,
                              'alttpr', json.dumps({'hash': [RNG.randint(0, 31) for _ in range(5)]})))
        title = f'{RNG.choice(ROUNDS)} — Match {n % 40 + 1}'
        created = at - timedelta(days=RNG.randint(1, 14))
        m_rows.append((mid, at, seated, started, finished, confirmed, None, on_stage, title, created, created,
                       seed_id, stage_id, t.id, tid, False, eid))
        size = 4 if RNG.random() < 0.08 else 2
        players = RNG.sample(entrants[t.id], size)
        for rank, uid in enumerate(players, 1):
            mp_rows.append((mid, uid, tid, rank if finished else None, f'{RNG.randint(1, 60)}' if seated else None,
                            created, created))
            if RNG.random() < 0.75:
                ack_rows.append((mid, uid, tid, created + timedelta(hours=2), False, created, created))
        by_match[mid] = {'at': at, 'on_stage': on_stage, 'players': players, 'created': created}

    await cp.copy('generatedseeds', ('id', 'seed_url', 'seed_info', 'created_at', 'updated_at', 'tenant_id',
                                     'randomizer', 'seed_code'), seed_rows)
    await cp.copy('match', ('id', 'scheduled_at', 'seated_at', 'started_at', 'finished_at', 'confirmed_at',
                            'comment', 'is_stream_candidate', 'title', 'created_at', 'updated_at',
                            'generated_seed_id', 'stage_id', 'tournament_id', 'tenant_id', 'needs_review', 'event_id'), m_rows)
    await cp.copy('matchplayers', ('match_id', 'user_id', 'tenant_id', 'finish_rank', 'assigned_station',
                                   'created_at', 'updated_at'), mp_rows)
    await cp.copy('matchacknowledgment', ('match_id', 'user_id', 'tenant_id', 'acknowledged_at',
                                          'auto_acknowledged', 'created_at', 'updated_at'), ack_rows)

    crew_pool = users[: max(50, len(users) // 8)]
    comm_rows, trk_rows, watch_rows, msv_rows, cmt_rows, rr_rows = [], [], [], [], [], []
    for mid, info in by_match.items():
        c = info['created']
        n_crew = RNG.randint(1, 4) if info['on_stage'] else (1 if RNG.random() < 0.1 else 0)
        for uid in RNG.sample(crew_pool, n_crew):
            comm_rows.append((mid, uid, tid, RNG.random() < 0.6, RNG.choice(staff_ids), c, c))
        if info['on_stage'] and RNG.random() < 0.7:
            trk_rows.append((mid, RNG.choice(crew_pool), tid, RNG.random() < 0.6, RNG.choice(staff_ids), c, c))
            if RNG.random() < 0.4:
                msv_rows.append((mid, RNG.choice(crew_pool), tid, c, c))
        for uid in set(RNG.sample(users, RNG.randint(0, 9))) - set(info['players']):
            watch_rows.append((mid, uid, tid, c, c))
        for _ in range(RNG.choice((0, 0, 1, 2, 3, 5))):
            kind = RNG.choice(('player', 'staff_to_players', 'staff_private'))
            author = RNG.choice(info['players']) if kind == 'player' else RNG.choice(staff_ids)
            cmt_rows.append((mid, author, tid, kind, RNG.choice(COMMENT_BODIES), c + timedelta(hours=1), c))
        if RNG.random() < 0.2:
            status = RNG.choice(('pending', 'approved', 'declined', 'withdrawn', 'superseded'))
            rr_rows.append((mid, info['players'][0], tid, 'reschedule', info['at'] + timedelta(hours=2),
                            'Flight lands late.', info['at'], status, c, c))
    await cp.copy('commentator', ('match_id', 'user_id', 'tenant_id', 'approved', 'approved_by_id',
                                  'created_at', 'updated_at'), comm_rows)
    await cp.copy('tracker', ('match_id', 'user_id', 'tenant_id', 'approved', 'approved_by_id',
                              'created_at', 'updated_at'), trk_rows)
    await cp.copy('matchwatcher', ('match_id', 'user_id', 'tenant_id', 'created_at', 'updated_at'), watch_rows)
    await cp.copy('matchstreamvolunteer', ('match_id', 'user_id', 'tenant_id', 'created_at', 'updated_at'), msv_rows)
    await cp.copy('matchcomment', ('match_id', 'author_id', 'tenant_id', 'kind', 'body', 'created_at',
                                   'updated_at'), cmt_rows)
    await cp.copy('matchreschedulerequest', ('match_id', 'requested_by_id', 'tenant_id', 'kind', 'proposed_at',
                                             'reason', 'original_scheduled_at', 'status', 'created_at',
                                             'updated_at'), rr_rows)
    return {'tournaments': tournaments, 'stages': stages, 'match_ids': match_ids}


async def make_members(cp: Copier, tenant_id: int, users: list[int], now: datetime, *, staff: int,
                       proctors: int, volunteers: int) -> list[int]:
    rows = [(tenant_id, u, now - timedelta(days=RNG.randint(0, 60)), RNG.choice(('discord_import', 'open', 'join_request')))
            for u in users]
    await cp.copy('tenantmembership', ('tenant_id', 'user_id', 'created_at', 'source'), rows)
    roles = []
    staff_ids = users[:staff]
    roles += [(tenant_id, u, 'staff', 'manual', now) for u in staff_ids]
    roles += [(tenant_id, u, 'proctor', 'manual', now) for u in users[staff:staff + proctors]]
    roles += [(tenant_id, u, 'volunteer', 'volunteer', now) for u in users[staff + proctors:staff + proctors + volunteers]]
    roles += [(tenant_id, u, 'stream_manager', 'manual', now) for u in users[staff:staff + 10]]
    await cp.copy('userrole', ('tenant_id', 'user_id', 'role', 'source', 'created_at'), roles)
    return staff_ids


async def make_volunteering(cp: Copier, tenant: Any, users: list[int], staff_ids: list[int], start: datetime,
                            days: int, now: datetime) -> None:
    from models import VolunteerPosition

    tid = tenant.id
    eid = await _event_id(tenant)
    names = ('Proctor', 'Check-in Desk', 'Admin Desk', 'Stage Manager', 'Runner', 'Tech Support',
             'Merch', 'Setup', 'Teardown', 'Restream Tech', 'Commentary Wrangler', 'Info Booth')
    positions = [await VolunteerPosition.create(tenant=tenant, name=f'{n} (scale)', display_order=i,
                                                shift_length_minutes=120) for i, n in enumerate(names)]
    vols = users[30:30 + 400]
    shift_rows, shift_meta = [], []
    shift_ids = await cp.next_ids('volunteershift', days * 7 * len(positions))
    k = 0
    for d in range(days):
        for slot in range(7):
            s = (start + timedelta(days=d)).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(hours=9 + slot * 2)
            for p in positions:
                need = RNG.randint(1, 5)
                shift_rows.append((shift_ids[k], s, s + timedelta(hours=2), need, p.id, tid, now, now, eid))
                shift_meta.append((shift_ids[k], s, need))
                k += 1
    await cp.copy('volunteershift', ('id', 'starts_at', 'ends_at', 'slots_needed', 'position_id', 'tenant_id',
                                     'created_at', 'updated_at', 'event_id'), shift_rows)
    asg = []
    for sid, s, need in shift_meta:
        for uid in RNG.sample(vols, RNG.randint(0, need)):
            checked = s if s < now and RNG.random() < 0.8 else None
            asg.append((sid, uid, tid, False, RNG.choice(staff_ids), checked, now, now))
    await cp.copy('volunteerassignment', ('shift_id', 'user_id', 'tenant_id', 'auto_generated', 'assigned_by_id',
                                          'checked_in_at', 'created_at', 'updated_at'), asg)
    va, pa = [], []
    for uid in vols:
        for _ in range(RNG.randint(1, 6)):
            s = start + timedelta(hours=RNG.randint(0, days * 24))
            va.append((s, s + timedelta(hours=RNG.randint(1, 6)), RNG.choice(('preferred', 'available', 'unavailable')),
                       uid, tid, now, now, eid))
    for uid in RNG.sample(users, min(len(users), 900)):
        for _ in range(RNG.randint(1, 3)):
            s = start + timedelta(hours=RNG.randint(0, days * 24))
            pa.append((s, s + timedelta(hours=RNG.randint(1, 8)), 'unavailable', uid, tid, now, now, eid))
    cols = ('starts_at', 'ends_at', 'status', 'user_id', 'tenant_id', 'created_at', 'updated_at', 'event_id')
    await cp.copy('volunteeravailability', cols, va)
    await cp.copy('playeravailability', cols, pa)


async def make_check_in(cp: Copier, tenant: Any, users: list[int], staff_ids: list[int], now: datetime) -> None:
    from models import CheckInEvent, CheckInTier

    tid = tenant.id
    event = await CheckInEvent.create(tenant=tenant, name='Scale Event — Check-in', status='open',
                                      sync_interval_minutes=10, comp_volunteers=True, comp_roles=['staff'])
    tier = await CheckInTier.create(tenant=tenant, event=event, matcherino_pass_id=990001, title='Weekend Badge',
                                    amount_cents=6500)
    ent_ids = await cp.next_ids('checkinentrant', len(users))
    ents, passes = [], []
    for n, (eid, uid) in enumerate(zip(ent_ids, users, strict=False)):
        linked = uid if RNG.random() < 0.85 else None
        checked = now - timedelta(hours=RNG.randint(0, 40)) if RNG.random() < 0.6 else None
        ents.append((eid, 'matcherino', f'mx{tid}-{n}', f'Runner {n:05d}', now, linked, checked,
                     RNG.choice(staff_ids) if checked else None, event.id, tid, now, now))
        passes.append((990000 + n + tid * 100000, f'mx{tid}-{n}', f'CODE{tid}{n:06d}', now, eid, event.id, tid, tier.id,
                       now, now))
    await cp.copy('checkinentrant', ('id', 'source', 'matcherino_user_id', 'display_name', 'registered_at', 'user_id',
                                     'checked_in_at', 'checked_in_by_id', 'event_id', 'tenant_id', 'created_at',
                                     'updated_at'), ents)
    await cp.copy('checkinpass', ('matcherino_purchase_id', 'buyer_matcherino_user_id', 'code', 'purchased_at',
                                  'entrant_id', 'event_id', 'tenant_id', 'tier_id', 'created_at', 'updated_at'), passes)


async def make_equipment_and_misc(cp: Copier, tenant: Any, users: list[int], staff_ids: list[int],
                                  now: datetime) -> None:
    tid = tenant.id
    eid = await _event_id(tenant)
    base = await cp.fetchval('SELECT coalesce(max(asset_number), 0) FROM equipment WHERE tenant_id = $1', tid)
    eq_ids = await cp.next_ids('equipment', 300)
    eq = [(e, base + n + 1, f'Scale Console {n + 1}', RNG.choice(('available', 'checked_out', 'available')),
           tid, now, now) for n, e in enumerate(eq_ids)]
    await cp.copy('equipment', ('id', 'asset_number', 'name', 'status', 'tenant_id', 'created_at', 'updated_at'), eq)
    loans = []
    for e in eq_ids:
        for _ in range(RNG.randint(0, 8)):
            out = now - timedelta(hours=RNG.randint(1, 96))
            loans.append((out, out + timedelta(hours=3), RNG.choice(users), RNG.choice(staff_ids),
                          RNG.choice(staff_ids), e, tid))
    await cp.copy('equipmentloan', ('checked_out_at', 'checked_in_at', 'borrower_id', 'checked_in_by_id',
                                    'checked_out_by_id', 'equipment_id', 'tenant_id'), loans)

    ce_ids = await cp.next_ids('community_event', 80)
    ce = [(c, f'Meetup {n + 1}', 'Come hang out.', 'Hall B', now + timedelta(hours=RNG.randint(-48, 72)), 60,
           RNG.choice(('approved', 'pending', 'approved')), RNG.choice(users), tid, now, now, True, eid)
          for n, c in enumerate(ce_ids)]
    await cp.copy('community_event', ('id', 'title', 'description', 'location', 'starts_at', 'duration_minutes',
                                      'status', 'host_id', 'tenant_id', 'created_at', 'updated_at', 'rsvp_enabled', 'event_id'), ce)
    subs = {(c, u) for c in ce_ids for u in RNG.sample(users, RNG.randint(0, 40))}
    await cp.copy('community_event_subscription', ('event_id', 'user_id', 'tenant_id', 'created_at', 'going'),
                  [(c, u, tid, now, True) for c, u in subs])

    await cp.copy('feedback', ('category', 'message', 'page_url', 'status', 'user_id', 'tenant_id', 'created_at',
                               'updated_at'),
                  [(RNG.choice(('bug', 'suggestion', 'praise', 'other')), 'The schedule is great.',
                    f'/t/{tenant.slug}/home', RNG.choice(('new', 'reviewed')), RNG.choice(users), tid, now, now)
                   for _ in range(500)])
    await cp.copy('staff_request_dm', ('kind', 'object_id', 'channel_id', 'message_id', 'tenant_id', 'user_id',
                                       'created_at'),
                  [(RNG.choice(('join_request', 'reschedule_request', 'ada_request', 'community_event')),
                    RNG.randint(1, 5000), 5_000_000_000 + n, 6_000_000_000 + n, tid, RNG.choice(staff_ids), now)
                   for n in range(5000)])
    await cp.copy('tenantjoinrequest', ('status', 'message', 'tenant_id', 'user_id', 'created_at', 'updated_at'),
                  [(RNG.choice(('pending', 'approved', 'denied')), 'Let me in!', tid, u, now, now)
                   for u in RNG.sample(users, 300)])
    await cp.copy('webpushsubscription', ('endpoint', 'p256dh', 'auth', 'user_id', 'created_at'),
                  [(f'https://example.invalid/push/{tid}/{u}', 'k', 'a', u, now) for u in RNG.sample(users, 800)])


async def make_history(cp: Copier, tenant: Any, users: list[int], *, since: datetime, until: datetime,
                       audit: int, telemetry: int, webhooks: int) -> None:
    """The append-only tables: audit log, telemetry, webhook deliveries.

    Two thirds of each lands in the last ``(until - since) / 20`` — an event is a
    spike, not a flat line — and the rest spreads back to ``since``.
    """
    tid = tenant.id
    span = (until - since).total_seconds()

    def when() -> datetime:
        if RNG.random() < 0.66:
            return until - timedelta(seconds=RNG.random() * span / 20)
        return since + timedelta(seconds=RNG.random() * span)

    for start in range(0, audit, 100_000):
        rows = []
        for _ in range(min(100_000, audit - start)):
            uid = RNG.choice(users)
            rows.append((RNG.choice(AUDIT_ACTIONS), json.dumps({'actor_username': f'u{uid}', 'match_id': RNG.randint(1, 9999),
                                                                'tenant_id': tid}), when(), uid, tid))
        await cp.copy('auditlog', ('action', 'details', 'created_at', 'user_id', 'tenant_id'), rows)

    pages = [p.format(slug=tenant.slug) for p in PAGES]
    for start in range(0, telemetry, 200_000):
        rows = []
        for _ in range(min(200_000, telemetry - start)):
            if RNG.random() < 0.6:
                cat, typ = 'page', 'page.view'
            else:
                cat, typ = RNG.choice(INTERACTIONS)
            rows.append((cat, typ, RNG.choice(pages), f'sess-{RNG.randint(1, 40000)}', None, when(),
                         RNG.choice(users) if RNG.random() < 0.8 else None, tid,
                         RNG.choice(('phone', 'desktop', 'tablet')), RNG.choice((None, 'schedule', 'signup', 'save'))))
        await cp.copy('telemetryevent', ('category', 'event_type', 'path', 'session_id', 'details', 'created_at',
                                         'user_id', 'tenant_id', 'device', 'target'), rows)

    hook = await cp.fetchval('SELECT id FROM webhook WHERE tenant_id = $1 LIMIT 1', tid)
    if hook is None:
        from models import Webhook
        hook = (await Webhook.create(tenant=tenant, name='Scale hook', url='https://example.invalid/hook',
                                     secret='s', event_types=['match.created'])).id
    payload = json.dumps({'event': 'match.updated', 'data': {'id': 1, 'title': 'Winners Round 1 — Match 1'}})
    await cp.copy('webhookdelivery', ('event_type', 'payload', 'response_status', 'attempt_count', 'success',
                                      'created_at', 'delivered_at', 'webhook_id', 'tenant_id'),
                  [('match.updated', payload, 200, 1, True, w, w, hook, tid) for w in (when() for _ in range(webhooks))])


async def run(scale: float, tenant_slug: str, past_years: int) -> None:
    from dotenv import load_dotenv
    load_dotenv()
    from migrations.tortoise_config import TORTOISE_ORM
    from models import Tenant

    await Tortoise.init(config=TORTOISE_ORM)
    from scripts.seed_events import install_event_stamping
    await install_event_stamping()
    try:
        cp = Copier()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        t0 = time.perf_counter()
        tenant = await Tenant.get(slug=tenant_slug)
        members = int(2000 * scale)
        users = await make_users(cp, members + int(1500 * scale * (past_years > 0)), now)
        event_users = users[:members]
        staff_ids = await make_members(cp, tenant.id, event_users, now, staff=20, proctors=50, volunteers=300)
        start = now - timedelta(hours=36)
        await make_event(cp, tenant, event_users, start=start, hours=96, match_target=int(1300 * scale),
                         now=now, staff_ids=staff_ids, live=True)
        await make_volunteering(cp, tenant, event_users, staff_ids, start, 4, now)
        await make_check_in(cp, tenant, event_users, staff_ids, now)
        await make_equipment_and_misc(cp, tenant, event_users, staff_ids, now)
        await make_history(cp, tenant, event_users, since=now - timedelta(days=365 * 4), until=now,
                           audit=int(300_000 * scale), telemetry=int(1_500_000 * scale), webhooks=int(100_000 * scale))
        print(f'  [{tenant_slug}] event seeded in {time.perf_counter() - t0:.0f}s')

        for y in range(1, past_years + 1):
            year = now.year - y
            slug = f'scale-{year}'
            past = await Tenant.get_or_none(slug=slug) or await Tenant.create(
                name=f'Scale Live {year}', slug=slug, config={}, is_active=True)
            pool = RNG.sample(users, int(1800 * scale))
            p_staff = await make_members(cp, past.id, pool, now, staff=15, proctors=40, volunteers=200)
            ev_start = datetime(year, 10, 20, 13, tzinfo=timezone.utc)
            await make_event(cp, past, pool, start=ev_start, hours=96, match_target=int(1300 * scale), now=now,
                             staff_ids=p_staff, live=False)
            await make_volunteering(cp, past, pool, p_staff, ev_start, 4, now)
            await make_history(cp, past, pool, since=ev_start - timedelta(days=60), until=ev_start + timedelta(days=4),
                               audit=int(100_000 * scale), telemetry=int(300_000 * scale), webhooks=int(30_000 * scale))
            print(f'  [{slug}] past event seeded ({time.perf_counter() - t0:.0f}s)')

        async with cp.client.acquire_connection() as conn:
            await conn.execute('ANALYZE')
        print(f'Done in {time.perf_counter() - t0:.0f}s. Rows added:')
        for table, n in sorted(cp.tally.items(), key=lambda kv: -kv[1]):
            print(f'  {table:32s} {n:>10,}')
    finally:
        await Tortoise.close_connections()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--scale', type=float, default=1.0, help='multiplier on every volume (default 1)')
    ap.add_argument('--tenant', default='default', help='community slug that gets the live event')
    ap.add_argument('--past-years', type=int, default=4, help='past-year communities to add (default 4)')
    args = ap.parse_args()
    if os.environ.get('ENVIRONMENT') == 'production':
        raise SystemExit('Refusing to run with ENVIRONMENT=production.')
    asyncio.run(run(args.scale, args.tenant, args.past_years))


if __name__ == '__main__':
    main()
