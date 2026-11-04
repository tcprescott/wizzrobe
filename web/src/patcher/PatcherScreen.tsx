import { useRef, useState } from 'react'
import { Banner, Button, PageHeader } from '../components'
import { crc32 } from './bps'
import { applyPatch, crcHex, matchRom, patchInfo, wrongRom, type PatchFormat } from './formats'
import { baseName, extension, readFile, ROM_ACCEPT, saveBytes, savedRoms } from './files'
import './patcher.css'

const FORMAT: Record<PatchFormat, string> = { bps: 'BPS patch', ips: 'IPS patch', ups: 'UPS patch' }

interface Picked {
  name: string
  bytes: Uint8Array
}

function message(e: unknown): string {
  return e instanceof Error ? e.message : "That file couldn't be read."
}

function FileRow({
  kind,
  name,
  detail,
  pick,
  onPick,
  disabled,
  busy,
}: {
  kind: string
  name: string | null
  detail: string | null
  pick: string
  onPick: () => void
  disabled?: boolean
  busy?: boolean
}) {
  return (
    <div className="patcher-rom">
      <div className="patcher-rom__text">
        <h2 className="patcher-label">{kind}</h2>
        {name ? (
          <>
            <p className="patcher-summary__main">{name}</p>
            <p className="patcher-hint">{detail}</p>
          </>
        ) : (
          <p className="patcher-hint">None chosen</p>
        )}
      </div>
      <Button variant="outline" size="sm" disabled={disabled} busy={busy} busyLabel="Reading…" onClick={onPick}>
        {name ? 'Change' : pick}
      </Button>
    </div>
  )
}

/** /t/<slug>/patcher: any BPS, IPS or UPS patch onto any ROM, in the browser. */
export function PatcherScreen() {
  const [patch, setPatch] = useState<(Picked & { format: PatchFormat; crc: number | null }) | null>(null)
  const [rom, setRom] = useState<Picked | null>(null)
  const [busy, setBusy] = useState<'patch' | 'rom' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)
  const patchInput = useRef<HTMLInputElement>(null)
  const romInput = useRef<HTMLInputElement>(null)

  async function pickPatch(file: File) {
    setBusy('patch')
    setError(null)
    setDone(false)
    try {
      const bytes = await readFile(file)
      const info = patchInfo(bytes)
      setPatch({ name: file.name, bytes, format: info.format, crc: info.sourceCrc })
      const saved = info.sourceCrc != null ? await savedRoms.get(crcHex(info.sourceCrc)) : null
      setRom(saved ? { name: saved.name, bytes: saved.bytes } : rom && matchRom(rom.bytes, info.sourceCrc) ? rom : null)
    } catch (e) {
      setError(message(e))
    } finally {
      setBusy(null)
      if (patchInput.current) patchInput.current.value = ''
    }
  }

  async function pickRom(file: File) {
    if (!patch) return
    setBusy('rom')
    setError(null)
    setDone(false)
    try {
      const bytes = await readFile(file)
      const matched = matchRom(bytes, patch.crc)
      if (!matched) throw wrongRom(bytes, patch.crc!)
      if (patch.crc != null) await savedRoms.put({ name: file.name, crc: crcHex(crc32(matched)), bytes: matched.slice() })
      setRom({ name: file.name, bytes: matched })
    } catch (e) {
      setError(message(e))
    } finally {
      setBusy(null)
      if (romInput.current) romInput.current.value = ''
    }
  }

  function download() {
    if (!patch || !rom) return
    setError(null)
    try {
      saveBytes(applyPatch(patch.bytes, rom.bytes), `${baseName(patch.name)}${extension(rom.name)}`)
      setDone(true)
    } catch (e) {
      setError(message(e))
    }
  }

  return (
    <div className="patcher">
      <PageHeader title="ROM patcher" lede="Apply a BPS, IPS or UPS patch to your own ROM, right in your browser. Nothing is uploaded." />
      <section className="panel patcher-card" aria-label="Patch a ROM">
        <input
          ref={patchInput}
          type="file"
          accept=".bps,.ips,.ups"
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0]
            if (file) void pickPatch(file)
          }}
        />
        <input
          ref={romInput}
          type="file"
          accept={ROM_ACCEPT}
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0]
            if (file) void pickRom(file)
          }}
        />
        <FileRow
          kind={patch ? FORMAT[patch.format] : 'Patch'}
          name={patch?.name ?? null}
          detail={patch ? (patch.crc != null ? `Needs CRC32 ${crcHex(patch.crc)}` : "Doesn't name a ROM") : null}
          pick="Choose patch…"
          busy={busy === 'patch'}
          onPick={() => patchInput.current?.click()}
        />
        <FileRow
          kind="ROM"
          name={rom?.name ?? null}
          detail={patch?.crc != null ? 'Saved on this device · matches the patch' : 'Ready to patch'}
          pick="Choose ROM…"
          disabled={!patch}
          busy={busy === 'rom'}
          onPick={() => romInput.current?.click()}
        />
        {error ? (
          <Banner tone="error">{error}</Banner>
        ) : patch && rom && patch.crc != null ? (
          <p className="patcher-hint">Ready: the ROM matches what this patch was made for.</p>
        ) : patch && patch.crc == null && !rom ? (
          <p className="patcher-hint">An IPS patch doesn't say which ROM it needs, so check you've got the right one.</p>
        ) : null}
        {done && <Banner tone="info">Saved. Check your downloads.</Banner>}
        <Button className="patcher-primary" icon="download" disabled={!patch || !rom} onClick={download}>
          Download patched ROM
        </Button>
      </section>
    </div>
  )
}
