import { useState } from 'react'
import { api, unwrap } from '../../api/client'
import { Banner, Button, Panel, PanelRow, RowMenu, Spinner } from '../../components'
import { done, message } from '../../me/data'
import { useAdminQuery } from '../kit'

/** People who review this qualifier only; Staff and Qualifier Admins review every one. */
export function ReviewersPage({ qualifierId, onChange }: { qualifierId: number; onChange: () => void }) {
  const data = useAdminQuery(`qualifiers/${qualifierId}/reviewers`, () =>
    api.GET('/api/admin/qualifiers/{qualifier_id}/reviewers', { params: { path: { qualifier_id: qualifierId } } }).then(unwrap),
  )
  const [query, setQuery] = useState('')
  const [note, setNote] = useState<{ tone: 'info' | 'error'; text: string } | null>(null)
  const [busy, setBusy] = useState(false)

  async function act(call: () => Promise<unknown>, text: string) {
    setBusy(true)
    setNote(null)
    try {
      await call()
      setNote({ tone: 'info', text })
      data.reload()
      onChange()
    } catch (err) {
      setNote({ tone: 'error', text: message(err) })
    } finally {
      setBusy(false)
    }
  }

  if (data.error && !data.data) return <Banner tone="error">{data.error.message}</Banner>
  if (!data.data) return <Spinner label="Loading reviewers" />
  const current = new Set(data.data.reviewers.map((r) => r.id))
  const q = query.trim().toLowerCase()
  const matches = q
    ? data.data.members.filter((m) => !current.has(m.id) && (m.name.toLowerCase().includes(q) || (m.username ?? '').toLowerCase().includes(q))).slice(0, 8)
    : []

  return (
    <>
      {note && (
        <Banner tone={note.tone} action={<Button variant="ghost" onClick={() => setNote(null)}>Dismiss</Button>}>
          {note.text}
        </Banner>
      )}
      <Panel title={`Reviewers · ${data.data.reviewers.length}`}>
        {data.data.reviewers.length === 0 && (
          <PanelRow>
            <span className="t-row__sub">Only Staff and Qualifier Admins review this one. Add someone below to let them review it too.</span>
          </PanelRow>
        )}
        {data.data.reviewers.map((r) => (
          <PanelRow key={r.id}>
            <div className="t-row">
              <span className="t-row__text">
                <span className="t-row__title">{r.name}</span>
                {r.username && r.username !== r.name && <span className="t-row__sub">@{r.username}</span>}
              </span>
              <RowMenu
                label={`Actions for ${r.name}`}
                items={[
                  {
                    label: 'Remove as reviewer',
                    icon: 'person_remove',
                    destructive: true,
                    onSelect: () =>
                      void act(
                        async () => done(await api.DELETE('/api/async-qualifiers/{qualifier_id}/admins/{user_id}', { params: { path: { qualifier_id: qualifierId, user_id: r.id } } })),
                        `${r.name} no longer reviews this qualifier.`,
                      ),
                  },
                ]}
              />
            </div>
          </PanelRow>
        ))}
      </Panel>
      <Panel title="Add a reviewer">
        <PanelRow>
          <label className="field" style={{ width: '100%' }}>
            <span className="field__label">Find a member</span>
            <input className="input" type="search" value={query} placeholder="Name or username" onChange={(e) => setQuery(e.target.value)} />
          </label>
        </PanelRow>
        {q && matches.length === 0 && (
          <PanelRow>
            <span className="t-row__sub">No members match.</span>
          </PanelRow>
        )}
        {matches.map((m) => (
          <PanelRow key={m.id}>
            <div className="t-row">
              <span className="t-row__text">
                <span className="t-row__title">{m.name}</span>
                {m.username && m.username !== m.name && <span className="t-row__sub">@{m.username}</span>}
              </span>
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() =>
                  void act(async () => {
                    await api.POST('/api/async-qualifiers/{qualifier_id}/admins', { params: { path: { qualifier_id: qualifierId } }, body: { user_id: m.id } }).then(unwrap)
                    setQuery('')
                  }, `${m.name} can review this qualifier now.`)
                }
              >
                Add
              </Button>
            </div>
          </PanelRow>
        ))}
      </Panel>
    </>
  )
}
