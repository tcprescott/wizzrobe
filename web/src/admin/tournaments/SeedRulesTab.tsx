import { useEffect, useState } from 'react'
import { Navigate, useNavigate, useParams } from 'react-router-dom'
import { api, unwrap } from '../../api/client'
import {
  ActionsMenu,
  Banner,
  Button,
  ConfirmDialog,
  Dialog,
  EmptyState,
  FormDialog,
  Icon,
  Spinner,
} from '../../components'
import { usePhone } from '../../lib/usePhone'
import { message } from '../../me/data'
import { DetailEmpty, SideListCard, SideListLayout, SideListRow } from '../kit'
import { useAdminQuery, type Board, type BoardRow } from './data'
import { SeedRuleEditor } from './SeedRuleEditor'
import { newRuleId, thenPhrase, tidy, tweakText, whenPhrases, type Rule, type SeedEditor } from './seedRules'
import './seed-rules.css'

interface Props {
  row: BoardRow
  board: Board
  zone: string | null
  /** `<tournament base>/seed-rules`. */
  base: string
}

type Note = { tone: 'info' | 'error'; text: string }

/** Which preset each match rolls: the rule list beside the open rule. */
export function SeedRulesTab({ row, board, zone, base }: Props) {
  const editor = useAdminQuery(`tournaments/${row.id}/seed-rules`, () =>
    api.GET('/api/admin/tournaments/{tournament_id}/seed-rules', { params: { path: { tournament_id: row.id } } }).then(unwrap),
  )
  const openId = useParams()['*'] || null
  const phone = usePhone()
  const navigate = useNavigate()
  const [draft, setDraft] = useState<Rule[] | null>(null)
  const [version, setVersion] = useState(0)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<Note | null>(null)
  const [dialog, setDialog] = useState<'text' | 'import' | 'copy' | null>(null)
  const [deleting, setDeleting] = useState<Rule | null>(null)

  useEffect(() => {
    if (editor.data && draft === null) setDraft(editor.data.rules)
  }, [editor.data, draft])

  if (editor.error && !editor.data) {
    return (
      <Banner tone="error" action={<Button variant="outline" onClick={editor.reload}>Try again</Button>}>
        {editor.error.message}
      </Banner>
    )
  }
  if (!editor.data || draft === null) return <Spinner label="Loading seed rules" />
  const data = editor.data
  if (!row.seed_generator) {
    return (
      <EmptyState icon="casino" title="Pick a randomizer first">
        Seed rules choose among a randomizer's presets. Set one on the Settings tab.
      </EmptyState>
    )
  }

  const rules = draft
  const dirty = JSON.stringify(rules.map(tidy)) !== JSON.stringify(data.rules.map(tidy))
  const index = rules.findIndex((r) => r.id === openId)
  const open = index >= 0 ? rules[index] : null
  if (!phone && openId == null && rules[0]?.id) return <Navigate to={`${base}/${rules[0].id}`} replace />

  const replace = (next: Rule[], text?: string) => {
    setDraft(next)
    setVersion((v) => v + 1)
    if (text) setNote({ tone: 'info', text })
  }
  const update = (rule: Rule) => setDraft(rules.map((r) => (r.id === rule.id ? rule : r)))
  const move = (i: number, by: number) => {
    const next = [...rules]
    const [r] = next.splice(i, 1)
    if (r) next.splice(i + by, 0, r)
    setDraft(next)
  }
  const taken = () => new Set(rules.map((r) => r.id ?? ''))

  function addRule() {
    const preset = data.default_preset?.id ?? data.presets[0]?.id ?? null
    const rule: Rule = { id: newRuleId(taken()), name: '', when: {}, then: { preset }, tweaks: {} }
    setDraft([...rules, rule])
    navigate(`${base}/${rule.id}`)
  }

  async function save() {
    setBusy(true)
    setNote(null)
    try {
      const saved = await api
        .PUT('/api/admin/tournaments/{tournament_id}/seed-rules', {
          params: { path: { tournament_id: row.id } },
          body: { rules: rules.map(tidy) },
        })
        .then(unwrap)
      replace(saved.rules, 'Saved. The next seed each match rolls uses these rules.')
      editor.reload()
    } catch (err) {
      setNote({ tone: 'error', text: message(err) })
    } finally {
      setBusy(false)
    }
  }

  const ruleMenu = (i: number, rule: Rule) => [
    { label: 'Move up', icon: 'arrow_upward' as const, disabled: i === 0, onSelect: () => move(i, -1) },
    { label: 'Move down', icon: 'arrow_downward' as const, disabled: i === rules.length - 1, onSelect: () => move(i, 1) },
    {
      label: 'Duplicate',
      icon: 'content_copy' as const,
      disabled: rules.length >= data.max_rules,
      onSelect: () => {
        const copy = { ...structuredClone(rule), id: newRuleId(taken()), name: `${rule.name || `Rule ${i + 1}`} copy` }
        setDraft([...rules.slice(0, i + 1), copy, ...rules.slice(i + 1)])
        navigate(`${base}/${copy.id}`)
      },
    },
    { label: 'Delete rule…', icon: 'delete' as const, destructive: true, onSelect: () => setDeleting(rule) },
  ]

  const list = (
    <div className="side-list__col">
      <SideListCard label="Seed rules">
        {rules.map((r, i) => (
          <SideListRow
            key={r.id}
            to={`${base}/${r.id}`}
            leading={<span className="sr-num">{i + 1}</span>}
            title={r.name || `Rule ${i + 1}`}
            sub={<RuleSummary rule={r} data={data} />}
          />
        ))}
        <li>
          <div className="side-list__row sr-otherwise">
            <Icon name="subdirectory_arrow_right" className="sr-num sr-num--plain" />
            <span className="side-list__text">
              <span className="side-list__title">Otherwise</span>
              <span className="side-list__sub">
                Every other match rolls {data.default_preset ? <strong>{data.default_preset.name}</strong> : 'nothing yet: set a preset on the Settings tab'}.
              </span>
            </span>
          </div>
        </li>
      </SideListCard>
    </div>
  )

  const detail = open ? (
    <SeedRuleEditor
      key={`${open.id}-${version}`}
      rule={open}
      index={index}
      rules={rules}
      editor={data}
      tournamentId={row.id}
      zone={zone}
      onChange={update}
      menu={ruleMenu(index, open)}
    />
  ) : openId ? (
    <EmptyState icon="info" title="That rule isn't here">
      It may have been deleted. Pick another from the list.
    </EmptyState>
  ) : (
    <DetailEmpty>
      <EmptyState icon="casino" title="No seed rules yet">
        Every match rolls {data.default_preset?.name ?? "the tournament's preset"}. Add a rule to roll something else for some matches.
      </EmptyState>
    </DetailEmpty>
  )

  return (
    <div className="sr-tab">
      {!(phone && open) && (
        <div className="sr-toolbar">
          <p className="sr-lede">The first rule that fits a match decides what it rolls. A staff override on a match still wins.</p>
          <div className="sr-toolbar__actions">
            <Button icon="add" onClick={addRule} disabled={rules.length >= data.max_rules}>
              Add rule
            </Button>
            <ActionsMenu
              items={[
                { label: 'View as text', icon: 'code', onSelect: () => setDialog('text') },
                { label: 'Import text…', icon: 'upload', onSelect: () => setDialog('import') },
                { label: 'Copy from tournament…', icon: 'content_copy', onSelect: () => setDialog('copy') },
              ]}
            />
          </div>
        </div>
      )}
      {note && (
        <Banner tone={note.tone} action={<Button variant="ghost" onClick={() => setNote(null)}>Dismiss</Button>}>
          {note.text}
        </Banner>
      )}
      <SideListLayout list={list} detail={detail} open={openId != null} backTo={base} backLabel="Seed rules" />
      {dirty && (
        <div className="sr-savebar" role="region" aria-label="Unsaved seed rules">
          <span>Unsaved changes to the seed rules.</span>
          <span className="dialog__spacer" />
          <Button variant="ghost" disabled={busy} onClick={() => replace(data.rules)}>
            Undo changes
          </Button>
          <Button onClick={() => void save()} busy={busy} busyLabel="Saving…">
            Save rules
          </Button>
        </div>
      )}
      {dialog === 'text' && <TextDialog tournamentId={row.id} dirty={dirty} onClose={() => setDialog(null)} />}
      {dialog === 'import' && (
        <ImportDialog
          tournamentId={row.id}
          onClose={() => setDialog(null)}
          onRead={(next) => replace(next, `Read ${next.length} ${next.length === 1 ? 'rule' : 'rules'} from the text. Save to keep them.`)}
        />
      )}
      {dialog === 'copy' && (
        <CopyDialog
          row={row}
          board={board}
          onClose={() => setDialog(null)}
          onCopy={(next, from) => replace(next, `Copied ${next.length} ${next.length === 1 ? 'rule' : 'rules'} from ${from}. Save to keep them.`)}
        />
      )}
      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(o) => !o && setDeleting(null)}
        title={`Delete ${deleting?.name || 'this rule'}?`}
        action="Delete rule"
        onConfirm={async () => {
          if (deleting) {
            setDraft(rules.filter((r) => r.id !== deleting.id))
            navigate(base)
          }
          return true
        }}
      >
        Matches it would have caught fall through to the next rule. Nothing changes until you save.
      </ConfirmDialog>
    </div>
  )
}

function RuleSummary({ rule, data }: { rule: Rule; data: SeedEditor }) {
  const when = whenPhrases(rule, data.rounds, data.questions)
  const tweaks = Object.entries(rule.tweaks ?? {})
  return (
    <span className="sr-summary">
      <span>
        <span className="sr-k">When</span> {when.length ? when.join(' · ') : 'Every match'}
      </span>
      <span>
        <span className="sr-k">Then</span> {thenPhrase(rule, data.presets, data.questions)}
        {rule.then?.weighted?.length ? <Icon name="shuffle" size={16} className="sr-inline-icon" /> : null}
      </span>
      {tweaks.length > 0 && (
        <span className="sr-tweaks">
          <Icon name="tune" size={16} className="sr-inline-icon" />
          {tweaks.map(([k, v]) => `${k}: ${tweakText(v)}`).join(', ')}
        </span>
      )}
    </span>
  )
}

function TextDialog({ tournamentId, dirty, onClose }: { tournamentId: number; dirty: boolean; onClose: () => void }) {
  const text = useAdminQuery(`tournaments/${tournamentId}/seed-rules/text`, () =>
    api.GET('/api/admin/tournaments/{tournament_id}/seed-rules/text', { params: { path: { tournament_id: tournamentId } } }).then(unwrap),
  )
  const [copied, setCopied] = useState(false)
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Seed rules as text"
      description={
        dirty
          ? "These are the saved rules. Save first to include your changes."
          : 'Paste this into Import text on another tournament, or share it with another community.'
      }
      size="lg"
      footer={
        <>
          <span className="dialog__spacer" />
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
          <Button
            icon="content_copy"
            disabled={!text.data}
            onClick={() => void navigator.clipboard?.writeText(text.data?.text ?? '').then(() => setCopied(true))}
          >
            {copied ? 'Copied' : 'Copy text'}
          </Button>
        </>
      }
    >
      {text.error ? (
        <Banner tone="error">{text.error.message}</Banner>
      ) : text.data ? (
        <pre className="sr-yaml">{text.data.text}</pre>
      ) : (
        <Spinner label="Loading text" />
      )}
    </Dialog>
  )
}

function ImportDialog({ tournamentId, onClose, onRead }: { tournamentId: number; onClose: () => void; onRead: (rules: Rule[]) => void }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  async function read() {
    setBusy(true)
    setError(null)
    try {
      const parsed = await api
        .POST('/api/admin/tournaments/{tournament_id}/seed-rules/text', {
          params: { path: { tournament_id: tournamentId } },
          body: { text },
        })
        .then(unwrap)
      onRead(parsed.rules)
      onClose()
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }
  return (
    <FormDialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Import seed rules"
      description="Paste rules from View as text. They replace the list here; nothing is saved until you save."
      size="lg"
      primaryLabel="Read rules"
      onPrimary={() => void read()}
      busy={busy}
      busyLabel="Reading…"
    >
      <div className="t-form">
        {error && <Banner tone="error">{error}</Banner>}
        <label className="field">
          <span className="field__label">Rules as text</span>
          <textarea className="input sr-yaml-input" rows={14} spellCheck={false} value={text} onChange={(e) => setText(e.target.value)} />
        </label>
      </div>
    </FormDialog>
  )
}

function CopyDialog({
  row,
  board,
  onClose,
  onCopy,
}: {
  row: BoardRow
  board: Board
  onClose: () => void
  onCopy: (rules: Rule[], from: string) => void
}) {
  const options = board.rows.filter((r) => r.id !== row.id && r.seed_generator === row.seed_generator)
  const [pick, setPick] = useState<number | null>(options[0]?.id ?? null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  async function copy() {
    if (pick == null) return
    setBusy(true)
    setError(null)
    try {
      const other = await api
        .GET('/api/admin/tournaments/{tournament_id}/seed-rules', { params: { path: { tournament_id: pick } } })
        .then(unwrap)
      onCopy(other.rules, options.find((o) => o.id === pick)?.name ?? 'that tournament')
      onClose()
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }
  return (
    <FormDialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Copy seed rules"
      description="Replaces the list here with another tournament's rules. Nothing is saved until you save."
      primaryLabel="Copy rules"
      onPrimary={() => void copy()}
      busy={busy}
      busyLabel="Copying…"
    >
      <div className="t-form">
        {error && <Banner tone="error">{error}</Banner>}
        {options.length === 0 ? (
          <p className="sr-hint">No other tournament here rolls the same randomizer.</p>
        ) : (
          <label className="field">
            <span className="field__label">Copy from</span>
            <select className="input" value={pick ?? ''} onChange={(e) => setPick(Number(e.target.value))}>
              {options.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
    </FormDialog>
  )
}
