/**
 * The organisers' event guide, drawn page by page in the app. A phone's home
 * screen app opens a PDF full screen with no way back, so it stays in here,
 * with Download beside the title for anyone who wants the file.
 */
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { API_BASE } from '../auth/config'
import { authedFetch } from '../api/client'
import { Banner, Button, Icon, PageHeader, Spinner } from '../components'
import { Loaded, NotHere } from './ArticleView'
import { useEventInfo, useReadingBase } from './data'

const SAFE_LINK = /^(https?:|mailto:|tel:)/i

type Pdf = import('pdfjs-dist').PDFDocumentProxy

async function openPdf(data: ArrayBuffer): Promise<Pdf> {
  const [pdfjs, worker] = await Promise.all([import('pdfjs-dist'), import('pdfjs-dist/build/pdf.worker.min.mjs?url')])
  pdfjs.GlobalWorkerOptions.workerSrc = worker.default
  return pdfjs.getDocument({ data, isEvalSupported: false }).promise
}

async function drawPages(box: HTMLElement, pdf: Pdf, width: number, alive: () => boolean): Promise<void> {
  box.textContent = ''
  const ratio = Math.min(window.devicePixelRatio || 1, 2)
  for (let n = 1; n <= pdf.numPages && alive(); n++) {
    const page = await pdf.getPage(n)
    const viewport = page.getViewport({ scale: width / page.getViewport({ scale: 1 }).width })
    const frame = document.createElement('div')
    frame.className = 'guide-page'
    frame.style.width = `${viewport.width}px`
    frame.style.height = `${viewport.height}px`
    frame.setAttribute('role', 'img')
    frame.setAttribute('aria-label', `Page ${n} of ${pdf.numPages}`)
    const canvas = document.createElement('canvas')
    canvas.width = Math.floor(viewport.width * ratio)
    canvas.height = Math.floor(viewport.height * ratio)
    canvas.style.width = `${viewport.width}px`
    canvas.style.height = `${viewport.height}px`
    frame.appendChild(canvas)
    box.appendChild(frame)
    const context = canvas.getContext('2d')
    if (!context) continue
    await page.render({ canvasContext: context, viewport, transform: ratio !== 1 ? [ratio, 0, 0, ratio, 0, 0] : undefined }).promise
    for (const a of await page.getAnnotations()) {
      if (a.subtype !== 'Link' || !a.url || !SAFE_LINK.test(a.url)) continue
      const r = viewport.convertToViewportRectangle(a.rect)
      const link = document.createElement('a')
      link.className = 'guide-link'
      link.href = a.url
      link.target = '_blank'
      link.rel = 'noopener noreferrer'
      link.setAttribute('aria-label', `${a.url} (opens in a new tab)`)
      link.style.left = `${Math.min(r[0], r[2])}px`
      link.style.top = `${Math.min(r[1], r[3])}px`
      link.style.width = `${Math.abs(r[2] - r[0])}px`
      link.style.height = `${Math.abs(r[3] - r[1])}px`
      frame.appendChild(link)
    }
  }
}

function Viewer({ filename, sizeLabel }: { filename: string; sizeLabel: string }) {
  const { event } = useReadingBase()
  const box = useRef<HTMLDivElement>(null)
  const [state, setState] = useState<{ status: 'loading' | 'ready' | 'failed'; file?: string }>({ status: 'loading' })

  useEffect(() => {
    let live = true
    let objectUrl: string | undefined
    let pdf: Pdf | undefined
    let drawnAt = 0
    let timer: number | undefined
    const alive = () => live
    const url = `${API_BASE}/api/event-info/guide.pdf${event ? `?event=${encodeURIComponent(event)}` : ''}`
    const onResize = () => {
      window.clearTimeout(timer)
      timer = window.setTimeout(() => {
        const width = box.current?.clientWidth ?? 0
        if (pdf && box.current && Math.abs(width - drawnAt) >= 24) {
          drawnAt = width
          void drawPages(box.current, pdf, width, alive)
        }
      }, 250)
    }
    ;(async () => {
      const response = await authedFetch(new Request(url))
      if (!response.ok) throw new Error(String(response.status))
      const bytes = await response.arrayBuffer()
      if (!live) return
      objectUrl = URL.createObjectURL(new Blob([bytes], { type: 'application/pdf' }))
      setState({ status: 'loading', file: objectUrl })
      pdf = await openPdf(bytes.slice(0))
      if (!live || !box.current) return
      drawnAt = box.current.clientWidth
      await drawPages(box.current, pdf, drawnAt, alive)
      if (live) setState({ status: 'ready', file: objectUrl })
      window.addEventListener('resize', onResize)
    })().catch(() => live && setState((s) => ({ ...s, status: 'failed' })))
    return () => {
      live = false
      window.clearTimeout(timer)
      window.removeEventListener('resize', onResize)
      if (objectUrl) URL.revokeObjectURL(objectUrl)
      void pdf?.destroy()
    }
  }, [event])

  return (
    <>
      <PageHeader
        title="Event guide"
        actions={
          state.file ? (
            <Button variant="outline" icon="download" asChild>
              <a href={state.file} download={filename} aria-label={`Download the event guide PDF (${sizeLabel})`}>
                PDF
              </a>
            </Button>
          ) : undefined
        }
      />
      {state.status === 'failed' && (
        <Banner tone="error">The guide didn't load here. Check your connection and try again.</Banner>
      )}
      {state.status === 'loading' && <Spinner label="Loading the guide" />}
      <div ref={box} className="guide-pages" />
    </>
  )
}

export function GuideScreen() {
  const { base } = useReadingBase()
  const index = useEventInfo()
  return (
    <div className="read-page">
      <Link to={`${base}/event-info`} className="back-link">
        <Icon name="chevron_left" size={20} />
        Event information
      </Link>
      <Loaded
        query={index}
        what="the guide"
        missing={<NotHere title="This community has no event guide" back="How the app works" to={`${base}/help`} />}
      >
        {({ guide }) =>
          guide ? (
            <Viewer filename={guide.filename} sizeLabel={guide.size_label} />
          ) : (
            <NotHere title="This event has no guide" back="All event information" to={`${base}/event-info`} />
          )
        }
      </Loaded>
    </div>
  )
}
