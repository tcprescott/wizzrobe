// RFC 7636 PKCE with S256, on WebCrypto. The verifier never leaves this browser
// until the code exchange, so a stolen authorization code is useless alone.

function base64url(bytes: Uint8Array): string {
  let binary = ''
  for (const byte of bytes) binary += String.fromCharCode(byte)
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

export function randomToken(byteLength = 32): string {
  const bytes = new Uint8Array(byteLength)
  crypto.getRandomValues(bytes)
  return base64url(bytes)
}

/** 32 random bytes → a 43-character verifier, the shortest the server accepts. */
export function createVerifier(): string {
  return randomToken(32)
}

export async function challengeFor(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))
  return base64url(new Uint8Array(digest))
}
