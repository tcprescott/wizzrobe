import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, unwrap } from '../../api/client'
import { ActionsMenu, Banner, Button, Icon, Panel, PanelRow, Spinner, type MenuItem } from '../../components'
import { usePhone } from '../../lib/usePhone'
import { message } from '../../me/data'
import { useAdminQuery } from '../kit'
import { asBoxes, boxLabel, type LayoutLine } from './data'
import { BackgroundDialog, DeleteLayoutDialog, RenameDialog, UploadArtDialog, useEditorOptions } from './LayoutDialogs'
import { OverlayView } from './OverlayView'

type Dialog = 'rename' | 'background' | 'art' | 'delete' | null

interface Props {
  layout: LayoutLine
  base: string
  onChange: () => void
  onDeleted: () => void
}

/** One layout: its preview with sample text and its boxes; Edit layout (desktop) opens the box editor. */
export function LayoutPane({ layout, base, onChange, onDeleted }: Props) {
  const phone = usePhone()
  const Heading = phone ? 'h1' : 'h2'
  const navigate = useNavigate()
  const options = useEditorOptions()
  const detail = useAdminQuery(`stream-layouts/layouts/${layout.id}@${layout.updated_at}`, async () => {
    const path = { params: { path: { layout_id: layout.id } } }
    const [d, preview] = await Promise.all([
      api.GET('/api/admin/stream-layouts/layouts/{layout_id}', path).then(unwrap),
      api.POST('/api/admin/stream-layouts/layouts/{layout_id}/preview', { ...path, body: {} }).then(unwrap),
    ])
    return { ...d, values: preview.values }
  })
  const [dialog, setDialog] = useState<Dialog>(null)
  const [note, setNote] = useState<{ tone: 'info' | 'error'; text: string } | null>(null)
  const changed = (text: string) => {
    setDialog(null)
    setNote({ tone: 'info', text })
    onChange()
    detail.reload()
  }

  async function act(call: () => Promise<unknown>, text: string) {
    setNote(null)
    try {
      await call()
      changed(text)
    } catch (err) {
      setNote({ tone: 'error', text: message(err) })
    }
  }

  const path = { params: { path: { layout_id: layout.id } } }
  const menu: MenuItem[] = [
    { label: 'Rename…', icon: 'edit', onSelect: () => setDialog('rename') },
    {
      label: 'Duplicate',
      icon: 'content_copy',
      onSelect: () =>
        void act(async () => {
          const r = await api.POST('/api/admin/stream-layouts/layouts/{layout_id}/duplicate', path).then(unwrap)
          navigate(`${base}/layouts/${r.layout.id}`)
        }, 'Made a copy.'),
    },
    { label: 'Upload art…', icon: 'upload', onSelect: () => setDialog('art') },
    { label: 'Plain background…', icon: 'palette', onSelect: () => setDialog('background') },
  ]
  if (layout.art_url) {
    menu.push({
      label: 'Remove art',
      icon: 'hide_image',
      onSelect: () => void act(() => api.DELETE('/api/admin/stream-layouts/layouts/{layout_id}/art', path).then(unwrap), 'Art removed.'),
    })
  }
  menu.push({ label: 'Delete layout…', icon: 'delete', destructive: true, onSelect: () => setDialog('delete') })

  const sub = [
    `${layout.width}×${layout.height}`,
    `${layout.box_count} ${layout.box_count === 1 ? 'box' : 'boxes'}`,
    layout.stages.length ? `on ${layout.stages.join(', ')}` : 'not on a stage',
  ].join(' · ')

  return (
    <div className="sl-pane">
      <div className="detail-head">
        <div className="detail-head__text">
          <Heading>{layout.name}</Heading>
          <div className="detail-head__sub">
            <span>{sub}</span>
          </div>
        </div>
        {!phone && (
          <Button icon="edit" onClick={() => navigate(`${base}/layouts/${layout.id}/edit`)}>
            Edit layout
          </Button>
        )}
        <ActionsMenu label="Actions" items={menu} />
      </div>

      {note && (
        <Banner tone={note.tone} action={<Button variant="ghost" onClick={() => setNote(null)}>Dismiss</Button>}>
          {note.text}
        </Banner>
      )}
      {phone && <Banner tone="info">Moving and styling boxes needs a bigger screen. Open this layout on a computer to edit it.</Banner>}

      <Panel title="Preview">
        <PanelRow>
          {detail.data ? (
            <OverlayView
              width={detail.data.layout.width}
              height={detail.data.layout.height}
              artUrl={detail.data.layout.art_url}
              boxes={asBoxes(detail.data.boxes)}
              values={detail.data.values}
              outlines
              label={`${layout.name} with sample text`}
              className="sl-preview"
            />
          ) : detail.error ? (
            <Banner tone="error">{detail.error.message}</Banner>
          ) : (
            <Spinner label="Loading the layout" />
          )}
        </PanelRow>
        <PanelRow>
          <span className="t-row__sub">Sample text. Each stage fills it from the match on it.</span>
        </PanelRow>
      </Panel>

      <Panel title="Boxes">
        {detail.data && detail.data.boxes.length === 0 && (
          <PanelRow>
            <span className="t-row__sub">No boxes yet.{phone ? '' : ' Press Edit layout to add player names, the round and more.'}</span>
          </PanelRow>
        )}
        {detail.data &&
          asBoxes(detail.data.boxes).map((b) => (
            <PanelRow key={b.id}>
              <div className="t-row">
                <Icon name={b.kind === 'hole' ? 'tv' : 'text_fields'} size={20} />
                <span className="t-row__text">
                  <span className="t-row__title">{boxLabel(b, options.data)}</span>
                  <span className="t-row__sub">
                    {b.w}×{b.h} at {b.x}, {b.y}
                  </span>
                </span>
              </div>
            </PanelRow>
          ))}
      </Panel>

      {dialog === 'rename' && <RenameDialog layout={layout} onClose={() => setDialog(null)} onDone={changed} />}
      {dialog === 'background' && <BackgroundDialog layout={layout} onClose={() => setDialog(null)} onDone={changed} />}
      {dialog === 'art' && <UploadArtDialog layout={layout} onClose={() => setDialog(null)} onDone={changed} />}
      {dialog === 'delete' && (
        <DeleteLayoutDialog
          layout={layout}
          onClose={() => setDialog(null)}
          onDone={() => {
            setDialog(null)
            onDeleted()
          }}
        />
      )}
    </div>
  )
}

