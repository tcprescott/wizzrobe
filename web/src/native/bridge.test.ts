import { describe, expect, it } from 'vitest'
import { inAppPath } from './bridge'

const APP = 'org.example.wizzrobe'

describe('inAppPath', () => {
  it('keeps the path, query and hash of a community link', () => {
    expect(inAppPath('https://onsite.example.org/t/sgl/home?schedule=4#x', APP)).toBe('/t/sgl/home?schedule=4#x')
  })

  it('takes the sign-in return on the app scheme', () => {
    expect(inAppPath(`${APP}:/auth/callback?code=c&state=s`, APP)).toBe('/auth/callback?code=c&state=s')
  })

  it('ignores anything else on the app scheme', () => {
    expect(inAppPath(`${APP}:/t/sgl/me`, APP)).toBeNull()
  })

  it('ignores other schemes', () => {
    expect(inAppPath('mailto:someone@example.org', APP)).toBeNull()
    expect(inAppPath('javascript:alert(1)', APP)).toBeNull()
  })

  it('passes a bare path through', () => {
    expect(inAppPath('/t/sgl/schedule', APP)).toBe('/t/sgl/schedule')
    expect(inAppPath('//evil.example/t', APP)).toBeNull()
  })
})
