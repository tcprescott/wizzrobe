import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Banner, Button, Dialog, Icon, PageHeader, Spinner } from '../components'
import { errorMessage } from '../api/client'
import { Loaded } from './ArticleView'
import { Tile } from './HelpScreens'
import { contentIcon, Prose } from './Prose'
import { loadNotice, useAbout, useChangelog, useFacts, useNotices, usePrivacy, useReadingBase, type Schemas } from './data'
import './reading.css'

function Back({ to, label }: { to: string; label: string }) {
  return (
    <Link to={to} className="back-link">
      <Icon name="chevron_left" size={20} />
      {label}
    </Link>
  )
}

const TONE: Record<string, string> = { New: 'info', Improved: 'ok', Fixed: 'pending', Security: 'cancelled' }

export function ChangelogScreen() {
  const { base } = useReadingBase()
  const query = useChangelog()
  return (
    <div className="read-page">
      <Back to={`${base}/help`} label="All help" />
      <PageHeader title="What's new" lede="The bigger changes to Wizzrobe, about a week at a time, newest first." />
      <Loaded query={query} what="what's new">
        {({ weeks }) =>
          weeks.length ? (
            <div className="read-weeks">
              {weeks.map((w) => (
                <section key={w.anchor} id={w.anchor} className="read-week" aria-labelledby={`${w.anchor}-title`}>
                  <h2 id={`${w.anchor}-title`}>{w.title}</h2>
                  <p className="read-week__period">{w.period}</p>
                  {w.sections.map((s) => (
                    <div key={s.category}>
                      <h3 className="read-week__category">
                        <span className={`read-dot read-dot--${TONE[s.category] ?? 'neutral'}`} aria-hidden="true" />
                        {s.category}
                        <span className="read-week__count">{s.count}</span>
                      </h3>
                      <Prose blocks={s.blocks} />
                    </div>
                  ))}
                </section>
              ))}
            </div>
          ) : (
            <p className="muted">Nothing to show yet.</p>
          )
        }
      </Loaded>
    </div>
  )
}

export function AboutScreen() {
  const { base } = useReadingBase()
  const query = useAbout()
  return (
    <div className="read-page">
      <Back to={`${base}/help`} label="All help" />
      <PageHeader title="About Wizzrobe" lede="The app behind this community's schedule." />
      <Loaded query={query} what="About">
        {(about) => (
          <>
            <section className="read-card">
              <Prose blocks={about.intro} />
              <Banner tone="info">
                <strong>Built with Claude.</strong> {about.built_with_claude}
              </Banner>
              <p className="read-build">
                Version {about.version}
                {about.commit_short && (
                  <>
                    {' · '}
                    <a href={about.source_url} target="_blank" rel="noopener noreferrer">
                      Commit {about.commit_short}
                      <span className="visually-hidden"> (opens in a new tab)</span>
                    </a>
                  </>
                )}
              </p>
            </section>
            <div className="read-tiles">
              <Tile to={`${base}/about/privacy`} icon="privacy_tip" title="Privacy policy" summary="What we keep when you sign in and use the app, who sees it, and how to have it removed." />
              <Tile to={`${base}/about/notices`} icon="description" title="Third-party notices" summary={`The ${about.notice_count} open-source libraries, fonts and icons Wizzrobe ships, with their licences.`} />
              <Tile to={`${base}/changelog`} icon="release_alert" title="What's new" summary="The bigger changes, a week at a time." />
              <Tile to={`${base}/help`} icon="help" title="Help" summary="Guides for players, crew, volunteers and staff." />
            </div>
          </>
        )}
      </Loaded>
    </div>
  )
}

function formatUpdated(iso: string): string {
  return new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' }).format(new Date(iso))
}

export function PrivacyScreen() {
  const { base } = useReadingBase()
  const query = usePrivacy()
  return (
    <div className="read-page read-page--prose">
      <Back to={`${base}/about`} label="About" />
      <Loaded query={query} what="the privacy policy">
        {(policy) => (
          <>
            <PageHeader title="Privacy policy" lede={`Last updated ${formatUpdated(policy.updated)}`} />
            <Prose blocks={policy.blocks} />
          </>
        )}
      </Loaded>
    </div>
  )
}

type Notice = Schemas['NoticeOut']
type NoticeText = Schemas['NoticeText']

function NoticeDialog({ notice, onClose }: { notice: Notice | null; onClose: () => void }) {
  const [text, setText] = useState<{ key: string; body?: NoticeText; error?: string } | null>(null)
  useEffect(() => {
    if (!notice) return
    let live = true
    loadNotice(notice.key)
      .then((body) => live && setText({ key: notice.key, body }))
      .catch((err: unknown) => live && setText({ key: notice.key, error: errorMessage(err, 0) }))
    return () => {
      live = false
    }
  }, [notice])
  const current = notice && text?.key === notice.key ? text : null
  return (
    <Dialog open={notice !== null} onOpenChange={(open) => !open && onClose()} title={notice?.name ?? ''} size="lg">
      {notice && (
        <div className="read-notice">
          <p className="read-notice__meta">
            {notice.version && <span className="read-mono">{notice.version}</span>}
            <span>{notice.license}</span>
            {notice.url && (
              <a href={notice.url} target="_blank" rel="noopener noreferrer">
                Project page<span className="visually-hidden"> (opens in a new tab)</span>
              </a>
            )}
          </p>
          {current?.body ? (
            <pre className="read-notice__text" tabIndex={0} aria-label="Licence text">
              {current.body.text}
            </pre>
          ) : current?.error ? (
            <Banner tone="error">{current.error}</Banner>
          ) : (
            <Spinner label="Loading the licence" />
          )}
        </div>
      )}
    </Dialog>
  )
}

export function NoticesScreen() {
  const { base } = useReadingBase()
  const query = useNotices()
  const [open, setOpen] = useState<Notice | null>(null)
  return (
    <div className="read-page">
      <Back to={`${base}/about`} label="About" />
      <PageHeader
        title="Third-party notices"
        lede="Wizzrobe ships the open-source software below, and each keeps its own licence. Pick one to read its licence and copyright notice."
      />
      <Loaded query={query} what="the notices">
        {({ groups }) => (
          <>
            {groups.map((g) => (
              <section key={g.key} className="read-card" aria-labelledby={`notices-${g.key}`}>
                <h2 id={`notices-${g.key}`} className="read-card__title">
                  {g.heading} <span className="muted">{g.notices.length}</span>
                </h2>
                <p className="muted read-card__blurb">{g.blurb}</p>
                <ul className="read-notices">
                  {g.notices.map((n) => (
                    <li key={n.key}>
                      <button type="button" onClick={() => setOpen(n)}>
                        <span className="read-notices__name">{n.name}</span>
                        {n.version && <span className="read-mono muted">{n.version}</span>}
                        <span className="read-notices__licence">{n.license}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
            <NoticeDialog notice={open} onClose={() => setOpen(null)} />
          </>
        )}
      </Loaded>
    </div>
  )
}

export function FactsScreen() {
  const query = useFacts()
  const all = useMemo(() => Object.values(query.data?.sections ?? {}).flat(), [query.data])
  const [pick, setPick] = useState<number | null>(null)
  const index = pick ?? (all.length ? Math.floor(Math.random() * all.length) : 0)
  const shuffle = () => {
    if (all.length < 2) return
    let next = index
    while (next === index) next = Math.floor(Math.random() * all.length)
    setPick(next)
  }
  return (
    <div className="read-page">
      <Loaded query={query} what="the facts">
        {(facts) => (
          <>
            <PageHeader
              title={facts.title}
              lede={`You found them. All ${facts.total} of the facts this app hides in its empty lists, its spinners and its 404 screen.`}
            />
            <section className="read-fact" aria-label={`Featured ${facts.noun.toLowerCase()}`}>
              <Icon name={contentIcon(facts.icon)} size={40} />
              <p className="read-fact__text" aria-live="polite">
                {all[index]}
              </p>
              <Button variant="outline" icon="refresh" onClick={shuffle}>
                {facts.another}
              </Button>
            </section>
            {Object.entries(facts.sections).map(([name, list]) => (
              <section key={name} className="read-group" aria-label={name}>
                <h2 className="read-group__title">
                  {name} · {list.length}
                </h2>
                <ul className="read-fact-list">
                  {list.map((f) => (
                    <li key={f}>
                      <Icon name={contentIcon(facts.icon)} size={18} />
                      <span>{f}</span>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </>
        )}
      </Loaded>
    </div>
  )
}
