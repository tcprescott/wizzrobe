import { useState } from 'react'
import { Banner, Button, FormDialog, Icon, RowMenu, Spinner } from '../../components'
import { formatDayTime } from '../../lib/time'
import { actions, useComments, type Comment, type CommentKind } from './data'

const KIND_NOTE: Record<string, string> = {
  player: 'To staff',
  staff_private: 'Staff only',
  staff_to_players: 'To the players',
}

/** The match's comments, and a box to add a staff note or a message to the players. */
export function Comments({ matchId, zone, canWrite, onChanged }: { matchId: number; zone: string; canWrite: boolean; onChanged: () => void }) {
  const { data, error, loading, reload } = useComments(matchId)
  const [body, setBody] = useState('')
  const [kind, setKind] = useState<CommentKind>('staff_private')
  const [busy, setBusy] = useState<string | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<Comment | null>(null)

  async function act(key: string, call: () => Promise<unknown>) {
    setBusy(key)
    setFailure(null)
    try {
      await call()
      reload()
      onChanged()
      return true
    } catch (err) {
      setFailure(err instanceof Error ? err.message : String(err))
      return false
    } finally {
      setBusy(null)
    }
  }

  async function send() {
    if (!body.trim()) return
    if (await act('send', () => actions.comment(matchId, body.trim(), kind))) setBody('')
  }

  const comments = data ?? []
  return (
    <section aria-labelledby={`ms-comments-${matchId}`}>
      <h3 className="ms-pane__section" id={`ms-comments-${matchId}`}>
        Comments{comments.length > 0 && ` · ${comments.length}`}
      </h3>
      {error && !data && (
        <Banner tone="error" action={<Button variant="outline" onClick={reload}>Try again</Button>}>
          {error.message}
        </Banner>
      )}
      {loading && !data && <Spinner label="Loading comments" />}
      {data && comments.length === 0 && <p className="ms-note">No comments yet.</p>}
      {comments.length > 0 && (
        <ul className="ms-comments">
          {comments.map((c) => (
            <li key={c.id} className={`ms-comment ms-comment--${c.kind}`}>
              <div className="ms-comment__head">
                <span className="ms-comment__who">{c.own ? `${c.author} (you)` : c.author}</span>
                <span className="ms-comment__kind">
                  {c.kind === 'staff_private' && <Icon name="lock" size={14} />}
                  {KIND_NOTE[c.kind] ?? c.kind} · {formatDayTime(c.created_at, zone)}
                </span>
                {c.can_delete && (
                  <RowMenu
                    label={`More for ${c.author}'s comment`}
                    items={[
                      {
                        label: 'Delete comment',
                        icon: 'delete',
                        destructive: true,
                        onSelect: () => setDeleting(c),
                      },
                    ]}
                  />
                )}
              </div>
              <p className="ms-comment__body">{c.body}</p>
            </li>
          ))}
        </ul>
      )}
      {canWrite && (
        <div className="ms-compose">
          <label className="field">
            <span className="field__label">Add a comment</span>
            <textarea
              className="input"
              rows={2}
              maxLength={2000}
              value={body}
              onChange={(e) => setBody(e.target.value)}
            />
          </label>
          <div className="ms-compose__row">
            <div className="segmented" role="radiogroup" aria-label="Who reads it">
              {(['staff_private', 'staff_to_players'] as const).map((k) => (
                <span key={k}>
                  <input type="radio" id={`ms-ck-${matchId}-${k}`} name={`ms-ck-${matchId}`} checked={kind === k} onChange={() => setKind(k)} />
                  <label htmlFor={`ms-ck-${matchId}-${k}`}>{k === 'staff_private' ? 'Staff only' : 'To the players'}</label>
                </span>
              ))}
            </div>
            <Button size="sm" busy={busy === 'send'} busyLabel="Sending…" disabled={!body.trim()} onClick={send}>
              {kind === 'staff_private' ? 'Add note' : 'Send to players'}
            </Button>
          </div>
          {kind === 'staff_to_players' && <p className="ms-note">The players are DMed unless they turned these off.</p>}
        </div>
      )}
      {failure && <Banner tone="error">{failure}</Banner>}
      <FormDialog
        open={deleting !== null}
        onOpenChange={(o) => !o && setDeleting(null)}
        title="Delete this comment?"
        size="sm"
        description={deleting ? `${deleting.author}'s comment is removed for everyone who could read it.` : undefined}
        primaryLabel="Delete comment"
        destructive
        busy={busy === 'delete'}
        busyLabel="Deleting…"
        onPrimary={async () => {
          if (deleting && (await act('delete', () => actions.deleteComment(deleting.id)))) setDeleting(null)
        }}
      >
        {null}
      </FormDialog>
    </section>
  )
}
