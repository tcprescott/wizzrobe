/** a11y checks for a tournament's Seed rules tab, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'admin-seed-rules', path: '/t/sgl/admin/tournaments/1/seed-rules', wait: 'text=Otherwise' },
  { name: 'admin-seed-rule-weighted', path: '/t/sgl/admin/tournaments/1/seed-rules/mys123', wait: 'text=Weighted' },
  { name: 'admin-seed-rule-preview', path: '/t/sgl/admin/tournaments/1/seed-rules/dec456', wait: 'text=fires' },
  {
    name: 'admin-seed-rules-text',
    path: '/t/sgl/admin/tournaments/1/seed-rules',
    act: async (p) => {
      await p.getByRole('button', { name: 'Actions' }).last().click()
      await p.getByRole('menuitem', { name: 'View as text' }).click()
    },
    wait: 'role=dialog',
  },
]

export const fixtures = {
  '/api/admin/tournaments/1/seed-rules': {
    tournament_id: 1,
    randomizer: 'alttpr',
    max_rules: 50,
    default_preset: { id: 1, name: 'ALTTPR Open', randomizer: 'alttpr' },
    presets: [
      { id: 1, name: 'ALTTPR Open', randomizer: 'alttpr' },
      { id: 2, name: 'ALTTPR Standard', randomizer: 'alttpr' },
      { id: 3, name: 'ALTTPR Inverted', randomizer: 'alttpr' },
    ],
    rounds: [
      { key: 'w2', label: 'Winners semifinals' },
      { key: 'w1', label: 'Winners final' },
      { key: 'gf', label: 'Grand final' },
    ],
    matches: [{ id: 214, label: 'Kai vs Morgan Wu', scheduled_at: '{{now+2h}}' }],
    rules: [
      {
        id: 'dec456',
        name: 'Top 8 decider',
        when: { round: ['w2', 'w1', 'gf'], game: ['last'] },
        then: { preset: 2 },
        tweaks: {},
      },
      {
        id: 'mys123',
        name: 'Grand final mystery',
        when: { round: ['gf'] },
        then: {
          weighted: [
            { preset: 1, weight: 50 },
            { preset: 2, weight: 30 },
            { preset: 3, weight: 20 },
          ],
        },
        tweaks: { hints: 'off' },
      },
      {
        id: 'swd789',
        name: 'Game 2 swords',
        when: { game: [2] },
        then: { preset: 2 },
        tweaks: { 'item.swords': 'assured' },
      },
    ],
  },
  '/api/admin/tournaments/1/seed-rules/preview': {
    source: 'rule',
    rule_id: 'dec456',
    rule_number: 1,
    rule_name: 'Top 8 decider',
    outcomes: [{ id: 2, name: 'ALTTPR Standard', randomizer: 'alttpr', weight: 1, percent: 100 }],
    tweaks: {},
    settings: { mode: 'standard' },
    facts: { round: 'gf', game: 3, best_of: 3, title: null, on_stage: true, scheduled_at: null },
    match_label: 'Kai vs Morgan Wu',
  },
  '/api/admin/tournaments/1/seed-rules/text': {
    text: 'rules:\n- name: Top 8 decider\n  when:\n    round: [w2, w1, gf]\n    game: [last]\n  then:\n    preset: ALTTPR Standard\n',
  },
}
