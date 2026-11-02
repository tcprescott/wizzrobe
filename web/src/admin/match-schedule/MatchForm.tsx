import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import { Banner, Button, Spinner } from '../../components'
import { effectiveZone } from '../../lib/time'
import { useMatchForm, useScheduling, type MatchForm as Form, type MatchFormSave } from './data'
import { PeoplePicker } from './PeoplePicker'
import { instantOf, useTimeCheck, whenOf, WhenFields, type When } from './TimeCheck'

interface Props {
  /** Null for a new match. */
  matchId: number | null
  zone: string | null | undefined
  /** Where Cancel and Close go. */
  backTo: string
  onSaved: (matchId: number) => void
  standalone?: boolean
}

/** New match, or Edit match: the form takes the detail pane (its own screen on a phone). */
export function MatchForm(props: Props) {
  const { data, error, loading, reload } = useMatchForm(props.matchId)
  if (error && !data) {
    return (
      <Banner tone="error" action={<Button variant="outline" onClick={reload}>Try again</Button>}>
        {error.message}
      </Banner>
    )
  }
  if (loading || !data) return <Spinner label="Loading the form" />
  return <FormBody key={props.matchId ?? 'new'} form={data} {...props} />
}

function FormBody({ form, matchId, zone: boardZone, backTo, onSaved, standalone }: Props & { form: Form }) {
  const zone = effectiveZone(boardZone)
  const scheduling = useScheduling()
  const v = form.values
  const [tournamentId, setTournamentId] = useState<number | null>(v.tournament_id ?? (form.tournaments.length === 1 ? form.tournaments[0]!.id : null))
  const [stageId, setStageId] = useState<number | null>(v.stage_id ?? null)
  const [players, setPlayers] = useState<number[]>(v.player_ids)
  const [everyone, setEveryone] = useState(false)
  const [when, setWhen] = useState<When>(whenOf(v.scheduled_at, zone))
  const [commentators, setCommentators] = useState<number[]>(v.commentator_ids)
  const [trackers, setTrackers] = useState<number[]>(v.tracker_ids)
  const [broadcasters, setBroadcasters] = useState<number[]>(v.broadcaster_ids)
  const [title, setTitle] = useState(v.title)
  const [comment, setComment] = useState(v.comment)
  const [streamCandidate, setStreamCandidate] = useState(v.is_stream_candidate)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)

  const tournament = form.tournaments.find((t) => t.id === tournamentId) ?? null
  const enrolled = new Set(tournament?.enrolled ?? [])
  const playerOptions = everyone || !tournament ? form.people : form.people.filter((p) => enrolled.has(p.id) || players.includes(p.id))
  const at = instantOf(when, zone)
  const check = useTimeCheck({ at, tournamentId, playerIds: players, matchId })
  const locked = form.synced_from_speedgaming
  const editing = matchId !== null
  const heading = editing ? 'Edit match' : 'New match'

  async function suggest() {
    if (!tournamentId) throw new Error('Choose the tournament first.')
    const { scheduled_at } = await scheduling.suggest({ tournament_id: tournamentId, player_ids: players, match_id: matchId })
    setWhen(whenOf(scheduled_at, zone))
  }

  async function save() {
    setFailure(null)
    if (!tournamentId) return setFailure('Choose the tournament.')
    if (!at) return setFailure('Choose a date and a time.')
    if (players.length === 0 && !title.trim()) return setFailure('Give it a title until its players are set, so people know what the slot is.')
    const body: MatchFormSave = {
      tournament_id: tournamentId,
      stage_id: stageId,
      scheduled_at: at,
      player_ids: players,
      commentator_ids: commentators,
      tracker_ids: trackers,
      broadcaster_ids: form.broadcasters ? broadcasters : null,
      title: title.trim(),
      description: v.description,
      comment: comment.trim(),
      is_stream_candidate: streamCandidate,
    }
    setBusy(true)
    try {
      const saved = editing ? await scheduling.update(matchId, body) : await scheduling.create(body)
      onSaved(saved.match_id)
    } catch (err) {
      setFailure(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const Heading = standalone ? 'h1' : 'h2'
  return (
    <form
      className="ms-pane ms-form"
      aria-labelledby="ms-form-title"
      onSubmit={(e) => {
        e.preventDefault()
        void save()
      }}
    >
      <header className="ms-pane__head">
        <div className="ms-pane__top">
          <Heading className="ms-pane__title ms-form__title" id="ms-form-title">
            {heading}
          </Heading>
          {!standalone && (
            <Button variant="ghost" size="sm" asChild>
              <NavLink to={backTo}>Close</NavLink>
            </Button>
          )}
        </div>
      </header>
      <div className="ms-pane__body ms-form__body">
        {locked && (
          <Banner tone="info">This match comes from SpeedGaming, so its tournament, time and players change there.</Banner>
        )}
        <div className="ms-form__grid">
          <label className="field">
            <span className="field__label">Tournament</span>
            <select
              className="input"
              required
              disabled={locked}
              value={tournamentId ?? ''}
              onChange={(e) => setTournamentId(Number(e.target.value) || null)}
            >
              <option value="">Choose…</option>
              {form.tournaments.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </label>
          {form.stages.length > 0 && (
            <label className="field">
              <span className="field__label">Stage</span>
              <select className="input" value={stageId ?? ''} onChange={(e) => setStageId(Number(e.target.value) || null)}>
                <option value="">No stage</option>
                {form.stages.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
        {!locked && (
          <>
            <PeoplePicker
              label="Players"
              people={playerOptions}
              value={players}
              onChange={setPlayers}
              max={tournament?.players_per_match}
              hint={players.length === 0 ? 'Leave empty to hold the slot; the title shows until players are set.' : undefined}
            />
            {tournament && (
              <label className="t-check">
                <input type="checkbox" checked={everyone} onChange={(e) => setEveryone(e.target.checked)} />
                Offer players not enrolled in this tournament
              </label>
            )}
            <WhenFields when={when} onWhen={setWhen} zone={zone} check={check} onSuggest={tournamentId && players.length > 0 ? suggest : null} />
          </>
        )}
        <div className="ms-form__grid">
          <PeoplePicker label="Commentators" people={form.people} value={commentators} onChange={setCommentators} />
          <PeoplePicker label="Trackers" people={form.people} value={trackers} onChange={setTrackers} />
          {form.broadcasters && (
            <PeoplePicker label="Broadcasters" people={form.people} value={broadcasters} onChange={setBroadcasters} />
          )}
        </div>
        <label className="field">
          <span className="field__label">Title</span>
          <input className="input" maxLength={255} value={title} onChange={(e) => setTitle(e.target.value)} />
          <span className="field__hint">Shown until the players are set, and beside them after.</span>
        </label>
        <label className="field">
          <span className="field__label">Comment</span>
          <textarea className="input" rows={2} maxLength={2000} value={comment} onChange={(e) => setComment(e.target.value)} />
          <span className="field__hint">Players and crew see it on the match.</span>
        </label>
        <label className="t-check">
          <input type="checkbox" checked={streamCandidate} onChange={(e) => setStreamCandidate(e.target.checked)} />
          Stream candidate
        </label>
        {failure && <Banner tone="error">{failure}</Banner>}
      </div>
      <footer className="ms-form__foot">
        <Button variant="ghost" asChild>
          <NavLink to={backTo}>Cancel</NavLink>
        </Button>
        <Button type="submit" busy={busy} busyLabel="Saving…">
          {editing ? 'Save match' : 'Create match'}
        </Button>
      </footer>
    </form>
  )
}
