import { useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api, unwrap, type Schemas } from '../api/client'
import { Banner, Button, Chip, EmptyState, PageHeader, Spinner } from '../components'
import { effectiveZone, formatDay } from '../lib/time'
import { crc32 } from './bps'
import { applyPatch, crcHex, matchRom, wrongRom } from './formats'
import { baseName, extension, fetchBytes, readFile, ROM_ACCEPT, saveBytes, savedRoms, type SavedRom } from './files'
import { CopyLinkButton, RomLine } from './parts'
import './patcher.css'

type Patch = Schemas['HostedPatchResponse']

function message(e: unknown): string {
  return e instanceof Error ? e.message : "The ROM couldn't be patched. Try again."
}

async function rememberedRom(patch: Patch): Promise<SavedRom | null> {
  return savedRoms.get(patch.source_crc ?? savedRoms.forGame(patch.game))
}

/** /t/<slug>/patch/<token>: a patch staff host, applied to the player's own ROM on this device. */
export function PatchScreen() {
  const { token = '' } = useParams()
  const [patch, setPatch] = useState<Patch | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [rom, setRom] = useState<SavedRom | null | undefined>(undefined)
  const [busy, setBusy] = useState<'rom' | 'download' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)
  const input = useRef<HTMLInputElement>(null)

  useEffect(() => {
    let live = true
    api
      .GET('/api/patches/{token}', { params: { path: { token } } })
      .then(unwrap)
      .then(async (p) => {
        if (!live) return
        setPatch(p)
        const saved = await rememberedRom(p)
        if (live) setRom(saved)
      })
      .catch((e: unknown) => live && setLoadError(message(e)))
    return () => {
      live = false
    }
  }, [token])

  if (loadError) {
    return (
      <EmptyState icon="link_off" title="This patch isn't here">
        It may have been taken down, or the link is cut short.
      </EmptyState>
    )
  }
  if (!patch) return <Spinner label="Loading the patch" />

  async function pickRom(file: File) {
    setBusy('rom')
    setError(null)
    setDone(false)
    try {
      const bytes = await readFile(file)
      const want = patch!.source_crc ? parseInt(patch!.source_crc, 16) : null
      const matched = matchRom(bytes, want)
      if (!matched) throw wrongRom(bytes, want!)
      const saved: SavedRom = { name: file.name, crc: crcHex(crc32(matched)), bytes: matched.slice() }
      await savedRoms.put(saved)
      if (!patch!.source_crc) savedRoms.rememberGame(patch!.game, saved.crc)
      setRom(saved)
    } catch (e) {
      setError(message(e))
    } finally {
      setBusy(null)
      if (input.current) input.current.value = ''
    }
  }

  async function download() {
    if (!rom) return
    setBusy('download')
    setError(null)
    setDone(false)
    try {
      const file = await fetchBytes(`/api/patches/${encodeURIComponent(patch!.token)}/file`, 'patch')
      saveBytes(applyPatch(file, rom.bytes), `${patch!.filename_base}${extension(rom.name)}`)
      setDone(true)
      void api.POST('/api/patches/{token}/downloaded', { params: { path: { token: patch!.token } } }).catch(() => {})
    } catch (e) {
      setError(message(e))
    } finally {
      setBusy(null)
    }
  }

  const byline = [patch.community, formatDay(patch.updated_at, effectiveZone(null))].filter(Boolean).join(' · ')
  return (
    <div className="patcher">
      <PageHeader title={patch.title} lede={byline} />
      <section className="panel patcher-card" aria-label="Patch">
        {patch.tournament && (
          <div className="patcher-meta">
            <Chip>{patch.tournament}</Chip>
          </div>
        )}
        {patch.description && <p className="patcher-description">{patch.description}</p>}
        <div>
          <h2 className="patcher-label">Needs</h2>
          <p className="patcher-summary__main">{patch.game}</p>
          <p className="patcher-hint">{patch.source_crc ? `CRC32 ${patch.source_crc}` : "This patch doesn't say which exact ROM it needs."}</p>
        </div>
        <input
          ref={input}
          type="file"
          accept={ROM_ACCEPT}
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0]
            if (file) void pickRom(file)
          }}
        />
        {rom === undefined ? (
          <Spinner label="Checking for your ROM" />
        ) : rom ? (
          <Button className="patcher-primary" icon="download" busy={busy === 'download'} busyLabel="Patching…" onClick={() => void download()}>
            Download patched ROM
          </Button>
        ) : (
          <div className="patcher-stack">
            <Button className="patcher-primary" icon="upload_file" busy={busy === 'rom'} busyLabel="Checking your ROM…" onClick={() => input.current?.click()}>
              Choose your ROM…
            </Button>
            <p className="patcher-hint">Pick it once on this device and it's remembered for every patch that needs it. Nothing is uploaded.</p>
          </div>
        )}
        {error && <Banner tone="error">{error}</Banner>}
        {done && <Banner tone="info">Saved. Check your downloads.</Banner>}
        {rom && <RomLine name={baseName(rom.name)} detail="Saved on this device" busy={busy === 'rom'} onChange={() => input.current?.click()} />}
        <div className="patcher-links">
          <CopyLinkButton />
        </div>
      </section>
    </div>
  )
}
