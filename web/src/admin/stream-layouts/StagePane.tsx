import { useState } from 'react'
import { api, unwrap } from '../../api/client'
import { ActionsMenu, Banner, Button, Chip, FormDialog, Panel, PanelRow, Spinner, type MenuItem } from '../../components'
import { formatDayTime } from '../../lib/time'
import { usePhone } from '../../lib/usePhone'
import { done, message } from '../../me/data'
import { useAdminQuery } from '../kit'
import { asBoxes, matchLabel, saveFile, type LayoutLine, type StageLine } from './data'
import { OverlayView } from './OverlayView'

interface Props {
  stage: StageLine
  layouts: LayoutLine[]
  zone: string
  onChange: () => void
}

/** One stage: what its overlay shows now, pin or copy its OBS link, then its layout. */
export function StagePane({ stage, layouts, zone, onChange }: Props) {
  const Heading = usePhone() ? 'h1' : 'h2'
  const preview = useAdminQuery(`stream-layouts/stages/${stage.id}/preview@${stage.layout_id}:${stage.showing?.id}:${stage.pinned}`, () =>
    api.GET('/api/admin/stream-layouts/stages/{stage_id}/preview', { params: { path: { stage_id: stage.id } } }).then(unwrap),
  )
  const [note, setNote] = useState<{ tone: 'info' | 'error'; text: string } | null>(null)
  const [dialog, setDialog] = useState<'pin' | 'renew' | null>(null)
  const [busy, setBusy] = useState(false)
  const path = { params: { path: { stage_id: stage.id } } }

  async function run(call: () => Promise<unknown>, text: string) {
    setBusy(true)
    setNote(null)
    try {
      await call()
      setNote({ tone: 'info', text })
      onChange()
      preview.reload()
    } catch (err) {
      setNote({ tone: 'error', text: message(err) })
    } finally {
      setBusy(false)
    }
  }

  async function copyLink() {
    setNote(null)
    try {
      await navigator.clipboard.writeText(stage.obs_url)
      setNote({ tone: 'info', text: `Copied ${stage.name}'s OBS link. Paste it into a Browser source above the video.` })
    } catch {
      setNote({ tone: 'error', text: `Couldn't copy. The link is ${stage.obs_url}` })
    }
  }

  async function downloadScene() {
    setNote(null)
    try {
      const r = await api.GET('/api/admin/stream-layouts/obs-scenes', { params: { query: { stage_id: stage.id } } }).then(unwrap)
      saveFile(r.filename, r.content)
    } catch (err) {
      setNote({ tone: 'error', text: message(err) })
    }
  }

  const menu: MenuItem[] = [{ label: 'Open overlay', icon: 'open_in_new', onSelect: () => window.open(stage.obs_url, '_blank', 'noopener') }]
  if (stage.layout_id) menu.push({ label: 'Download OBS scene', icon: 'download', onSelect: () => void downloadScene() })
  menu.push({ label: 'New link…', icon: 'autorenew', destructive: true, onSelect: () => setDialog('renew') })

  const showing = stage.showing
  const status = stage.pinned ? (
    <Chip tone="pending">Pinned</Chip>
  ) : showing ? (
    <Chip tone="info">Following the schedule</Chip>
  ) : (
    <Chip tone="neutral">Nothing on now</Chip>
  )

  return (
    <div className="sl-pane">
      <div className="detail-head">
        <div className="detail-head__text">
          <Heading>{stage.name}</Heading>
          <div className="detail-head__sub">
            {status}
            {stage.channel && <span>{stage.channel}</span>}
          </div>
        </div>
        <ActionsMenu label="Actions" items={menu} />
      </div>

      {note && (
        <Banner tone={note.tone} action={<Button variant="ghost" onClick={() => setNote(null)}>Dismiss</Button>}>
          {note.text}
        </Banner>
      )}

      <Panel title="On the overlay now">
        <PanelRow>
          <div className="sl-now">
            <div className="sl-now__thumb">
              {preview.data ? (
                <OverlayView
                  width={preview.data.width}
                  height={preview.data.height}
                  artUrl={preview.data.art_url}
                  boxes={asBoxes(preview.data.boxes)}
                  values={preview.data.values}
                  label={`${stage.name}'s overlay right now`}
                />
              ) : preview.error ? (
                <span className="t-row__sub">{preview.error.message}</span>
              ) : (
                <Spinner label="Loading the overlay" />
              )}
            </div>
            <div className="sl-now__text">
              {showing ? (
                <>
                  <span className="t-row__title">{showing.who}</span>
                  <span className="t-row__sub">
                    {[
                      showing.tournament,
                      showing.started_at
                        ? `started ${formatDayTime(showing.started_at, zone)}`
                        : showing.scheduled_at
                          ? formatDayTime(showing.scheduled_at, zone)
                          : null,
                    ]
                      .filter(Boolean)
                      .join(' · ')}
                  </span>
                </>
              ) : (
                <span className="t-row__sub">Nothing on this stage right now.</span>
              )}
              {stage.up_next && (
                <span className="t-row__sub">
                  Up next: {stage.up_next.who}
                  {stage.up_next.scheduled_at ? `, ${formatDayTime(stage.up_next.scheduled_at, zone)}` : ''}
                </span>
              )}
              {!stage.layout_id && <span className="t-row__sub">No layout yet, so OBS shows nothing. Pick one below.</span>}
            </div>
          </div>
        </PanelRow>
        <PanelRow>
          <div className="t-actions sl-now__actions">
            <Button variant="outline" icon="keep" disabled={busy} onClick={() => setDialog('pin')}>
              Pin a match…
            </Button>
            {stage.pinned && (
              <Button
                variant="outline"
                icon="sync"
                disabled={busy}
                onClick={() =>
                  void run(
                    () => api.DELETE('/api/admin/stream-layouts/stages/{stage_id}/pin', path).then(done),
                    `${stage.name} follows its matches again.`,
                  )
                }
              >
                Back to automatic
              </Button>
            )}
            <Button icon="content_copy" onClick={() => void copyLink()}>
              Copy OBS link
            </Button>
          </div>
        </PanelRow>
      </Panel>

      <Panel title="Layout">
        <PanelRow>
          <label className="field sl-layout-pick">
            <span className="field__label">Layout for {stage.name}</span>
            <select
              className="input"
              value={stage.layout_id ?? ''}
              disabled={busy}
              onChange={(e) => {
                const layoutId = e.target.value ? Number(e.target.value) : null
                const name = layouts.find((l) => l.id === layoutId)?.name
                void run(
                  () => api.PUT('/api/admin/stream-layouts/stages/{stage_id}/layout', { ...path, body: { layout_id: layoutId } }).then(done),
                  name ? `${stage.name} now uses ${name}.` : `${stage.name} has no layout; its overlay is blank.`,
                )
              }}
            >
              <option value="">No layout (blank overlay)</option>
              {layouts.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name}
                </option>
              ))}
            </select>
            <span className="field__hint">Changing it updates OBS within two seconds.</span>
          </label>
        </PanelRow>
      </Panel>

      {dialog === 'pin' && (
        <PinDialog
          stage={stage}
          zone={zone}
          onClose={() => setDialog(null)}
          onPinned={() => {
            setDialog(null)
            setNote({ tone: 'info', text: `Pinned to ${stage.name}. It stays until you set it back to automatic.` })
            onChange()
            preview.reload()
          }}
        />
      )}
      {dialog === 'renew' && (
        <FormDialog
          open
          size="sm"
          onOpenChange={(o) => !o && setDialog(null)}
          title="New overlay link?"
          description={`${stage.name}'s current link stops working. OBS shows nothing until the new one is pasted into its Browser source.`}
          primaryLabel="Make new link"
          destructive
          busy={busy}
          busyLabel="Making…"
          onPrimary={() => {
            setDialog(null)
            void run(
              () => api.POST('/api/admin/stream-layouts/stages/{stage_id}/renew-link', path).then(unwrap),
              `New link for ${stage.name}. Copy it into OBS; the old one has stopped working.`,
            )
          }}
        >
          <span />
        </FormDialog>
      )}
    </div>
  )
}

function PinDialog({ stage, zone, onClose, onPinned }: { stage: StageLine; zone: string; onClose: () => void; onPinned: () => void }) {
  const matches = useAdminQuery(`stream-layouts/stages/${stage.id}/matches`, () =>
    api.GET('/api/admin/stream-layouts/stages/{stage_id}/matches', { params: { path: { stage_id: stage.id } } }).then(unwrap),
  )
  const [picked, setPicked] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function pin() {
    if (picked == null) {
      setError('Pick a match.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      done(await api.PUT('/api/admin/stream-layouts/stages/{stage_id}/pin', { params: { path: { stage_id: stage.id } }, body: { match_id: picked } }))
      onPinned()
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <FormDialog
      open
      size="sm"
      onOpenChange={(o) => !o && onClose()}
      title={`Pin a match to ${stage.name}`}
      description="The overlay shows this match until you set it back to automatic."
      primaryLabel="Pin"
      busy={busy}
      busyLabel="Pinning…"
      onPrimary={() => void pin()}
    >
      <div className="t-form">
        {(error || matches.error) && <Banner tone="error">{error ?? matches.error?.message}</Banner>}
        {!matches.data && !matches.error && <Spinner label="Loading matches" />}
        {matches.data?.length === 0 && <p className="t-row__sub">No matches are booked on {stage.name} around now.</p>}
        {matches.data && matches.data.length > 0 && (
          <label className="field">
            <span className="field__label">Match</span>
            <select className="input" value={picked ?? ''} onChange={(e) => setPicked(e.target.value ? Number(e.target.value) : null)}>
              <option value="">Pick a match</option>
              {matches.data.map((m) => (
                <option key={m.id} value={m.id}>
                  {matchLabel(m, m.scheduled_at ? formatDayTime(m.scheduled_at, zone) : undefined)}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
    </FormDialog>
  )
}
