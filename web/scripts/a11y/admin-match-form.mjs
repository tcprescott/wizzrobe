/** a11y checks for a tournament's Match form tab and the player's "Before your match" card. See ../a11y.mjs. */
export const targets = [
  { name: 'admin-match-form', path: '/t/sgl/admin/tournaments/1/match-form', wait: 'text=Used by rule 1' },
  { name: 'admin-match-form-choice', path: '/t/sgl/admin/tournaments/1/match-form/g3', wait: 'text=Who answers' },
  { name: 'home-match-form', path: '/t/sgl/home?form=101', wait: 'text=Before your match' },
]

const yesNo = [
  { value: 'yes', label: 'Yes', preset: null },
  { value: 'no', label: 'No', preset: null },
]

export const fixtures = {
  '/api/admin/tournaments/1/match-form': {
    tournament_id: 1,
    questions: [
      {
        key: 'hard',
        kind: 'yes_no',
        prompt: 'Play Hard mode?',
        help: 'Only if every player says yes.',
        choices: yesNo,
        config: {},
        who: 'each_player',
        ask_on: 'every_match',
        visibility: 'never',
        used_by: [1],
      },
      {
        key: 'g3',
        kind: 'choice_one',
        prompt: 'Game 3 settings',
        help: "You lost game 2, so this one's your call.",
        choices: [
          { value: 'open', label: 'ALTTPR Open', preset: 1 },
          { value: 'std', label: 'ALTTPR Standard', preset: 2 },
          { value: 'boots', label: 'Casual Boots', preset: null },
        ],
        config: {},
        who: 'previous_loser',
        ask_on: 'when_used',
        visibility: 'at_roll',
        used_by: [],
      },
    ],
  },
  '/api/matches/101/form': {
    match_id: 101,
    locked: false,
    questions: [
      {
        key: 'hard',
        kind: 'yes_no',
        prompt: 'Play Hard mode?',
        help: 'Only if every player says yes.',
        choices: yesNo,
        config: {},
        visibility: 'never',
        answer: 'yes',
        others: [],
      },
      {
        key: 'g3',
        kind: 'choice_one',
        prompt: 'Game 3 settings',
        help: "You lost game 2, so this one's your call.",
        choices: [
          { value: 'open', label: 'ALTTPR Open', preset: null },
          { value: 'std', label: 'ALTTPR Standard', preset: null },
          { value: 'boots', label: 'Casual Boots', preset: null },
        ],
        config: {},
        visibility: 'at_roll',
        answer: 'std',
        others: [],
      },
      {
        key: 'hints',
        kind: 'number',
        prompt: 'How many hint tiles?',
        help: null,
        choices: [],
        config: { min: 0, max: 5 },
        visibility: 'right_away',
        answer: null,
        others: [{ name: 'Kaizo', value: 2 }],
      },
    ],
  },
}
