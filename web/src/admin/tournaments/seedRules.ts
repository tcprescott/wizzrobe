import type { Schemas } from '../../api/client'

export type Rule = Schemas['SeedRule']
export type RuleWhen = Schemas['SeedRuleWhen']
export type SeedEditor = Schemas['SeedRulesEditorResponse']
export type Preview = Schemas['SeedRulesPreviewResponse']
export type PresetRef = Schemas['PresetRef']

export type ConditionKey = 'round' | 'game' | 'title' | 'on_stage' | 'dates'

export const CONDITION_LABEL: Record<ConditionKey, string> = {
  round: 'Round',
  game: 'Game in set',
  title: 'Title contains',
  on_stage: 'Stage',
  dates: 'Date range',
}

const ID_CHARS = 'abcdefghijkmnpqrstuvwxyz23456789'

/** A rule id the server keeps (6 letters and digits), so the open rule's URL survives a save. */
export function newRuleId(taken: Set<string>): string {
  for (;;) {
    let id = ''
    for (let i = 0; i < 6; i++) id += ID_CHARS[Math.floor(Math.random() * ID_CHARS.length)]
    if (!taken.has(id)) return id
  }
}

export function conditionsOf(when: RuleWhen | undefined): ConditionKey[] {
  const w = when ?? {}
  const out: ConditionKey[] = []
  if (w.round != null) out.push('round')
  if (w.game != null) out.push('game')
  if (w.title != null) out.push('title')
  if (w.on_stage != null) out.push('on_stage')
  if (w.from != null || w.to != null) out.push('dates')
  return out
}

function gameLabel(g: number | 'last'): string {
  return g === 'last' ? 'the decider' : `game ${g}`
}

function orList(items: string[]): string {
  if (items.length <= 1) return items[0] ?? ''
  return `${items.slice(0, -1).join(', ')} or ${items[items.length - 1]}`
}

function shortDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

/** The rule's conditions as short phrases, for the list and the preview. */
export function whenPhrases(rule: Rule, rounds: SeedEditor['rounds']): string[] {
  const w = rule.when ?? {}
  const label = (key: string) => rounds.find((r) => r.key === key)?.label ?? key
  const out: string[] = []
  if (w.round?.length) out.push(`Round is ${orList(w.round.map(label))}`)
  if (w.game?.length) out.push(`Is ${orList(w.game.map(gameLabel))}`)
  if (w.title?.length) out.push(`Title has ${orList(w.title.map((t) => `“${t}”`))}`)
  if (w.on_stage != null) out.push(w.on_stage ? 'On a stage' : 'Not on a stage')
  if (w.from && w.to) out.push(`${shortDate(w.from)} to ${shortDate(w.to)}`)
  else if (w.from) out.push(`From ${shortDate(w.from)}`)
  else if (w.to) out.push(`Before ${shortDate(w.to)}`)
  return out
}

/** What the rule rolls, in a few words. */
export function thenPhrase(rule: Rule, presets: PresetRef[]): string {
  const name = (id: number) => presets.find((p) => p.id === id)?.name ?? 'A missing preset'
  const then = rule.then ?? {}
  if (then.weighted?.length) return then.weighted.map((e) => `${name(e.preset)} ${e.weight}`).join(' · ')
  if (then.preset != null) return name(then.preset)
  return 'No preset picked'
}

export function tweakText(value: unknown): string {
  return typeof value === 'string' ? value : JSON.stringify(value)
}

/** A tweak value as typed: JSON when it reads as JSON (true, 3, ["a"]), else the text itself. */
export function parseTweak(text: string): unknown {
  const t = text.trim()
  if (t === '') return ''
  try {
    return JSON.parse(t)
  } catch {
    return text
  }
}

/** Drops the empty pieces the editor leaves behind, so what's sent is what's shown. */
export function tidy(rule: Rule): Rule {
  const w: RuleWhen = {}
  const src = rule.when ?? {}
  if (src.round?.length) w.round = src.round
  if (src.game?.length) w.game = src.game
  if (src.title?.length) w.title = src.title
  if (src.on_stage != null) w.on_stage = src.on_stage
  if (src.from) w.from = src.from
  if (src.to) w.to = src.to
  const then = rule.then?.weighted != null ? { weighted: rule.then.weighted } : { preset: rule.then?.preset ?? null }
  return { id: rule.id, name: rule.name, when: w, then, tweaks: rule.tweaks ?? {} }
}
