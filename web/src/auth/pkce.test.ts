import { describe, expect, it } from 'vitest'
import { challengeFor, createVerifier } from './pkce'

describe('pkce', () => {
  it('matches the server-side S256 for a known verifier', async () => {
    // Expected value computed with Python's hashlib, as OAuthGrantService._s256 does.
    expect(await challengeFor('dBjftJeZ4CVP-mJ92ZvsWdBb9Zp6Uq96sZXN2nDDBXg')).toBe(
      'CdYhkrndsdkGlFy--h6MZrGA29ykmtJHGgspl8LYOdo',
    )
  })

  it('makes verifiers the server accepts', () => {
    const verifier = createVerifier()
    expect(verifier).toMatch(/^[A-Za-z0-9\-._~]{43,128}$/)
    expect(createVerifier()).not.toBe(verifier)
  })

  it('makes challenges the server accepts', async () => {
    expect(await challengeFor(createVerifier())).toMatch(/^[A-Za-z0-9\-_]{43}$/)
  })
})
