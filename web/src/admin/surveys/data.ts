import type { Schemas } from '../../api/client'
import type { Tone } from '../../components'

export type SurveyRow = Schemas['SurveySummaryResponse']
export type SurveyFull = Schemas['SurveyResponseModel']
export type Section = Schemas['SurveySectionResponse']
export type QuestionRow = Schemas['SurveyQuestionResponse']
export type Builder = Schemas['BuilderResponse']

const STATUSES: Record<string, { label: string; tone: Tone }> = {
  draft: { label: 'Draft', tone: 'pending' },
  open: { label: 'Open', tone: 'live' },
  closed: { label: 'Closed', tone: 'neutral' },
}
export const statusOf = (s: string) => STATUSES[s] ?? { label: s, tone: 'neutral' as Tone }

export const KINDS: { value: string; label: string; icon: string }[] = [
  { value: 'rating', label: 'Rating 1–5', icon: 'star' },
  { value: 'nps', label: 'Recommend 0–10', icon: 'thumb_up' },
  { value: 'yes_no', label: 'Yes / no', icon: 'help' },
  { value: 'choice_one', label: 'Pick one', icon: 'radio_button_checked' },
  { value: 'choice_many', label: 'Pick many', icon: 'check_box' },
  { value: 'agree_scale', label: 'Agree scale', icon: 'table_rows' },
  { value: 'comment', label: 'Comment', icon: 'chat' },
]

export const AUDIENCES: { value: string; label: string }[] = [
  { value: 'everyone', label: 'Everyone' },
  { value: 'each_tournament', label: 'Each tournament they played' },
  { value: 'each_tournament_run', label: 'Each tournament they ran' },
  { value: 'each_position', label: 'Each position they worked' },
  { value: 'crew', label: 'Crew' },
  { value: 'commentators', label: 'Commentators' },
  { value: 'trackers', label: 'Trackers' },
  { value: 'broadcasters', label: 'Broadcasters' },
  { value: 'checked_in', label: 'Checked in' },
]

export const kindOf = (kind: string) => KINDS.find((k) => k.value === kind) ?? { value: kind, label: kind, icon: 'help' }
export const audienceLabel = (a: string) => AUDIENCES.find((x) => x.value === a)?.label ?? a

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`
}

export function mins(seconds: number): string {
  return `${Math.max(1, Math.round(seconds / 60))} min`
}

/** "Rating 1–5 · required · shows after an answer". */
export function questionLine(q: QuestionRow): string {
  const bits = [kindOf(q.kind).label]
  if (q.options?.length && q.kind !== 'agree_scale') bits.push(plural(q.options.length, 'choice'))
  if (q.kind === 'agree_scale') bits.push(plural(q.options?.length ?? 0, 'statement'))
  if (q.none_option) bits.push(q.none_option)
  bits.push(q.required ? 'required' : 'optional')
  const conditions = q.conditions ?? []
  if (conditions.length) bits.push(conditions.some((c) => c.key === 'answer') ? 'follow-up' : 'only some people')
  return bits.join(' · ')
}

export function rowLine(s: SurveyRow): string {
  if (s.status === 'draft') return 'Nothing sent yet'
  return `${s.answered} of ${s.recipients} answered`
}
