import { describe, expect, it } from 'vitest'
import { holds, missing, type Part } from './visibility'

function part(key: string, scope: string, questions: Partial<Part['questions'][number]>[]): Part {
  return {
    key,
    title: key,
    description: '',
    reason: '',
    icon: 'help',
    scope,
    shows_if: [],
    questions: questions.map((q, i) => ({
      id: i + 1,
      prompt: 'Q',
      kind: 'choice_one',
      options: null,
      required: false,
      other_option: false,
      none_option: null,
      min_picks: null,
      max_picks: null,
      long_answer: false,
      shows_if: [],
      ...q,
    })),
    answers: {},
    answered: 0,
    showing: 0,
    shown: true,
    seconds: 10,
  }
}

describe('survey visibility', () => {
  const everyone = part('1|', '', [{ id: 10 }, { id: 11, shows_if: [{ question_id: 10, values: ['No'] }] }])
  const tourney = part('2|t:5', 't:5', [{ id: 20, shows_if: [{ question_id: 10, values: ['No'] }] }])
  const parts = [everyone, tourney]

  it('follows an earlier answer, falling back to the unscoped part', () => {
    expect(holds([{ question_id: 10, values: ['No'] }], parts, { '1|': { 10: 'No' } }, 't:5')).toBe(true)
    expect(holds([{ question_id: 10, values: ['No'] }], parts, { '1|': { 10: 'Yes' } }, 't:5')).toBe(false)
    expect(holds([{ question_id: 10, values: ['No'] }], parts, {}, '')).toBe(false)
  })

  it('matches any pick of a pick-many, and unwraps an Other answer', () => {
    expect(holds([{ question_id: 10, values: ['B'] }], parts, { '1|': { 10: ['A', 'B'] } }, '')).toBe(true)
    expect(holds([{ question_id: 10, values: ['Other'] }], parts, { '1|': { 10: { value: 'Other', other: 'x' } } }, '')).toBe(
      true,
    )
  })

  it('counts only required questions that are showing', () => {
    const req = part('3|', '', [{ id: 30, required: true }, { id: 31, required: true, shows_if: [{ question_id: 30, values: ['x'] }] }])
    expect(missing([req], {}).map((m) => m.question.id)).toEqual([30])
    expect(missing([req], { '3|': { 30: 'x' } }).map((m) => m.question.id)).toEqual([31])
  })
})
