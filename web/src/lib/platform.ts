interface CapacitorGlobal {
  isNativePlatform?: () => boolean
  getPlatform?: () => string
}

function capacitor(): CapacitorGlobal | undefined {
  return typeof window === 'undefined' ? undefined : (window as { Capacitor?: CapacitorGlobal }).Capacitor
}

/** True inside the iOS or Android store app, false on the website. */
export function isNative(): boolean {
  return capacitor()?.isNativePlatform?.() === true
}

export type NativePlatform = 'ios' | 'android'

export function nativePlatform(): NativePlatform | null {
  const platform = capacitor()?.getPlatform?.()
  return isNative() && (platform === 'ios' || platform === 'android') ? platform : null
}
