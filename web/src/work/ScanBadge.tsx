import { useEffect, useRef, useState } from 'react'
import { Banner, Dialog } from '../components'

interface Detected {
  rawValue: string
}
interface Detector {
  detect(source: HTMLVideoElement): Promise<Detected[]>
}
type DetectorClass = new (options: { formats: string[] }) => Detector

function detectorClass(): DetectorClass | null {
  const found = (globalThis as { BarcodeDetector?: DetectorClass }).BarcodeDetector
  return found && typeof navigator.mediaDevices?.getUserMedia === 'function' ? found : null
}

/** True where the browser can read a QR from the camera. A handheld scanner types into the search box everywhere else. */
export const canScanBadges = (): boolean => detectorClass() !== null

const EVERY_MS = 250

/** The camera, reading badge QRs until one turns up; the code goes back to the desk search. */
export function ScanBadgeDialog({ open, onOpenChange, onCode }: { open: boolean; onOpenChange: (open: boolean) => void; onCode: (code: string) => void }) {
  const video = useRef<HTMLVideoElement>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    const Klass = detectorClass()
    if (!Klass) return
    let live = true
    let stream: MediaStream | null = null
    let timer = 0
    const detector = new Klass({ formats: ['qr_code'] })
    setError(null)

    async function look() {
      const el = video.current
      if (!live || !el) return
      try {
        if (el.readyState >= 2) {
          const code = (await detector.detect(el))[0]?.rawValue.trim()
          if (code && live) {
            onCode(code)
            onOpenChange(false)
            return
          }
        }
      } catch {
        // A frame that can't be read yet; try the next one.
      }
      timer = window.setTimeout(() => void look(), EVERY_MS)
    }

    navigator.mediaDevices
      .getUserMedia({ video: { facingMode: 'environment' }, audio: false })
      .then((s) => {
        stream = s
        if (!live || !video.current) return
        video.current.srcObject = s
        void video.current.play()
        void look()
      })
      .catch(() => {
        if (live) setError("Couldn't open the camera. Allow camera access for this site, or type the badge code into the search.")
      })

    return () => {
      live = false
      window.clearTimeout(timer)
      stream?.getTracks().forEach((t) => t.stop())
    }
  }, [open, onCode, onOpenChange])

  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Scan a badge" description="Hold the badge QR up to the camera. The roster jumps to whoever holds it.">
      {error ? <Banner tone="error">{error}</Banner> : <video ref={video} className="scan-video" muted playsInline aria-label="Camera view" />}
    </Dialog>
  )
}
