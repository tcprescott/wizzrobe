import type { Schemas } from '../../api/client'

export type Survey = Schemas['RespondentSurvey']
export type Part = Schemas['RespondentPart']
export type Question = Schemas['RespondentQuestion']
export type ShowsIf = Schemas['ShowsIf']
/** Part key → question id → the answer as `save_part` takes it. */
export type Answers = Record<string, Record<number, unknown>>

/** An answer's value: a choice answered "Other" is saved as `{value, other}`. */
export function bare(answer: unknown): unknown {
  if (answer && typeof answer === 'object' && !Array.isArray(answer) && 'value' in answer) {
    return (answer as { value: unknown }).value
  }
  return answer
}

export function blank(answer: unknown): boolean {
  const v = bare(answer)
  if (v === undefined || v === null || v === '') return true
  if (Array.isArray(v)) return v.length === 0
  if (typeof v === 'object') return Object.keys(v as object).length === 0
  return false
}

/** The answer to `questionId` in this part's scope, else in the part with no scope. */
export function lookup(parts: Part[], answers: Answers, questionId: number, scope: string): unknown {
  const inScope = parts.find((p) => p.scope === scope && p.questions.some((q) => q.id === questionId))
  const scoped = inScope ? answers[inScope.key]?.[questionId] : undefined
  if (scoped !== undefined) return bare(scoped)
  const plain = parts.find((p) => p.scope === '' && p.questions.some((q) => q.id === questionId))
  return plain ? bare(answers[plain.key]?.[questionId]) : undefined
}

/** Whether every condition holds; a pick-many answer matches when any picked choice is listed. */
export function holds(conditions: ShowsIf[], parts: Part[], answers: Answers, scope: string): boolean {
  return conditions.every((c) => {
    const answer = lookup(parts, answers, c.question_id, scope)
    if (answer === undefined || answer === null || !c.values.length) return false
    if (Array.isArray(answer)) return answer.some((a) => c.values.includes(a))
    return c.values.includes(answer)
  })
}

export function partShown(part: Part, parts: Part[], answers: Answers): boolean {
  return holds(part.shows_if ?? [], parts, answers, part.scope)
}

export function showing(part: Part, parts: Part[], answers: Answers): Question[] {
  return part.questions.filter((q) => holds(q.shows_if ?? [], parts, answers, part.scope))
}

/** Required questions showing now and still blank, across every shown part. */
export function missing(parts: Part[], answers: Answers): { part: Part; question: Question }[] {
  const found: { part: Part; question: Question }[] = []
  for (const part of parts) {
    if (!partShown(part, parts, answers)) continue
    for (const q of showing(part, parts, answers)) {
      if (!q.required) continue
      const a = bare(answers[part.key]?.[q.id])
      const statementsLeft =
        q.kind === 'agree_scale' && (q.options ?? []).some((s) => !(a && typeof a === 'object' && s in (a as object)))
      if (blank(a) || statementsLeft) found.push({ part, question: q })
    }
  }
  return found
}

export function minutes(seconds: number): string {
  const m = Math.max(1, Math.round(seconds / 60))
  return `about ${m} min`
}
