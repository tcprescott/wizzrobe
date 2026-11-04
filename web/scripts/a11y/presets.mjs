/** a11y checks for the preset library, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'presets', path: '/t/sgl/presets/11' },
  { name: 'presets-list', path: '/t/sgl/presets' },
  { name: 'presets-copy', path: '/t/sgl/presets/11', act: async (p) => { await p.getByRole('button', { name: 'Actions' }).last().click(); await p.getByRole('menuitem', { name: /Copy to SpeedGaming/ }).click() }, wait: 'role=dialog' },
  { name: 'presets-copy-changed', path: '/t/sgl/presets/12' },
  { name: 'presets-mine', path: '/t/sgl/presets/15' },
  { name: 'presets-new', path: '/t/sgl/presets', act: async (p) => { await p.getByRole('button', { name: 'New preset' }).click() }, wait: 'role=dialog' },
  { name: 'presets-edit', path: '/t/sgl/presets/15', act: async (p) => { await p.getByRole('button', { name: 'Actions' }).last().click(); await p.getByRole('menuitem', { name: 'Edit…' }).click() }, wait: 'role=dialog' },
  { name: 'presets-copy-mine', path: '/t/sgl/presets/13', act: async (p) => { await p.getByRole('button', { name: 'Actions' }).last().click(); await p.getByRole('menuitem', { name: /Copy to my presets/ }).click() }, wait: 'role=dialog' },
  { name: 'presets-space', path: '/t/sgl/presets', act: async (p) => { await p.getByRole('button', { name: 'Actions' }).first().click(); await p.getByRole('menuitem', { name: /My preset space/ }).click() }, wait: 'role=dialog' },
]

const row = (over) => ({
  randomizer: 'alttpr',
  randomizer_name: 'A Link to the Past',
  rollable: true,
  can_manage: true,
  can_edit: false,
  description: null,
  space_kind: 'community',
  mine: false,
  listed: true,
  roll_count: 0,
  updated_at: '{{now-3d}}',
  copied_from: null,
  copied_from_id: null,
  changed_since_copy: false,
  ...over,
})

const library = [
  row({ id: 10, name: 'sgl2026', reference: 'sgl/sgl2026', space: 'sgl', space_name: 'SpeedGaming Live', mine: true, roll_count: 214, description: 'Open, fast Ganon, hints off.' }),
  row({ id: 12, name: 'crosskeys (from league)', reference: 'sgl/crosskeys (from league)', space: 'sgl', space_name: 'SpeedGaming Live', mine: true, listed: false, copied_from: 'alttprleague/crosskeys', copied_from_id: 11, changed_since_copy: true }),
  row({ id: 11, name: 'crosskeys', reference: 'alttprleague/crosskeys', space: 'alttprleague', space_name: 'ALTTPR League', roll_count: 5120, description: 'Crossworld entrance with keysanity.' }),
  row({ id: 13, name: 'tournament_full', reference: 'smleague/tournament_full', space: 'smleague', space_name: 'SM League', randomizer: 'sm', randomizer_name: 'Super Metroid', roll_count: 61 }),
  row({ id: 15, name: 'fastboots', reference: 'tom/fastboots', space: 'tom', space_name: 'Tom', space_kind: 'user', can_edit: true, roll_count: 7, description: 'Open, fast Ganon, pseudoboots.', copied_from: 'alttprleague/crosskeys', copied_from_id: 11, changed_since_copy: true }),
  row({ id: 14, name: 's8', reference: 'oot/s8', space: 'oot', space_name: 'OoT Randomizer', randomizer: 'ootr', randomizer_name: 'Ocarina of Time', rollable: false, roll_count: 17 }),
]

const settings = { goal: 'fast_ganon', crystals: { tower: '7', ganon: '7' }, mode: 'open', weapons: 'randomized', hints: 'off', entrances: 'crossed', dungeon_items: 'full' }

export const fixtures = {
  '/api/preset-library': library,
  '/api/preset-library/11': { ...library[2], settings },
  '/api/preset-library/12': { ...library[1], settings: { ...settings, entrances: 'none' } },
  '/api/preset-library/13': { ...library[3], settings: { mode: 'full' } },
  '/api/preset-library/15': { ...library[4], settings: { ...settings, pseudoboots: true } },
  '/api/preset-library/spaces': [
    { id: 3, slug: 'tom', owner: { id: 1, name: 'Tom' }, is_owner: true, preset_count: 4, max_presets: 50, editors: [{ id: 7, name: 'Ann' }], max_editors: 10 },
    { id: 9, slug: 'league-crew', owner: { id: 8, name: 'Bea' }, is_owner: false, preset_count: 12, max_presets: 50, editors: [], max_editors: 10 },
  ],
  '/api/preset-library/randomizers': [
    { slug: 'alttpr', name: 'A Link to the Past', available: true, paste_key: null, paste_label: null, paste_hint: null },
    { slug: 'z1r', name: 'Zelda 1 Randomizer', available: false, paste_key: 'flags', paste_label: 'Flags', paste_hint: 'Each roll adds a fresh seed number to these flags.' },
  ],
}
