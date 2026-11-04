/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'admin-group-races', path: '/t/sgl/admin/group-races/matches/41', wait: 'text=Entrants · 5' },
  { name: 'admin-group-races-list', path: '/t/sgl/admin/group-races', wait: 'text=ALTTPR Daily — Open' },
  {
    name: 'admin-group-races-new',
    path: '/t/sgl/admin/group-races',
    act: (p) => p.getByRole('button', { name: 'New group race' }).click(),
    wait: 'role=dialog',
  },
]

const race = (over) => ({
  kind: 'match',
  id: 40,
  title: 'ALTTPR Daily (Co-op)',
  series: 'ALTTPR Daily Races',
  series_id: 2,
  from_speedgaming: true,
  scheduled_at: '{{now-3h}}',
  opens_at: '{{now-4h}}',
  seed_at: '{{now-190m}}',
  state: 'scheduled',
  lock_at_seed: false,
  channel_id: '222',
  role_id: '333',
  seed_note: "Rolled from the tournament's preset",
  room_url: null,
  opened_at: null,
  announced_at: null,
  seed_posted_at: null,
  locked_at: null,
  finishers: 0,
  ...over,
})

const open = race({
  id: 41,
  title: 'ALTTPR Daily — Open',
  scheduled_at: '{{now+50m}}',
  opens_at: '{{now-10m}}',
  seed_at: '{{now+40m}}',
  state: 'open',
  room_url: 'https://racetime.gg/alttpr/clever-pidgey-4127',
  opened_at: '{{now-10m}}',
  announced_at: '{{now-10m}}',
})

export const fixtures = {
  '/api/group-races': {
    zone: 'America/New_York',
    races: [
      open,
      race({
        kind: 'qualifier',
        id: 7,
        title: 'Main Tournament Live Qualifier #3',
        series: 'SGL27 ALTTPR Qualifier',
        series_id: 7,
        from_speedgaming: false,
        scheduled_at: '{{now+2h}}',
        opens_at: '{{now+1h}}',
        seed_at: '{{now+110m}}',
        lock_at_seed: true,
        seed_note: 'The Pool A permalink',
      }),
      race({ id: 42, title: 'ALTTPR Daily — Standard', scheduled_at: '{{now+1d}}', opens_at: '{{now+23h}}', seed_at: '{{now+1430m}}' }),
    ],
  },
  '/api/group-races/matches/41': {
    ...open,
    entrants: [
      { name: 'Andy', linked: true, place: null, finish_seconds: null, status: 'ready' },
      { name: 'bigdeal', linked: true, place: null, finish_seconds: null, status: 'ready' },
      { name: 'Hycutype', linked: false, place: null, finish_seconds: null, status: 'not_ready' },
      { name: 'kirbymastah', linked: true, place: null, finish_seconds: null, status: 'ready' },
      { name: 'zaruvi', linked: false, place: null, finish_seconds: null, status: 'not_ready' },
    ],
    entrants_problem: null,
  },
  '/api/group-races/series': [
    { kind: 'tournament', id: 2, name: 'ALTTPR Daily Races', permalinks: [] },
    { kind: 'pool', id: 3, name: 'SGL27 ALTTPR Qualifier · Pool A', permalinks: [{ id: '9', name: 'Open, standard' }] },
  ],
}
