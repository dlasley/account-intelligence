import { describe, it, expect } from 'vitest'
import { scoreBadge, relativeTime } from '@/lib/utils'

describe('scoreBadge', () => {
  it('returns green for score >= 80 (high)', () => {
    expect(scoreBadge(90).color).toContain('green')
    expect(scoreBadge(90).label).toBe('high')
  })
  it('returns emerald for score 60-79 (good)', () => {
    expect(scoreBadge(70).color).toContain('emerald')
    expect(scoreBadge(70).label).toBe('good')
  })
  it('returns yellow for score 40-59 (medium)', () => {
    expect(scoreBadge(50).color).toContain('yellow')
    expect(scoreBadge(50).label).toBe('medium')
  })
  it('returns orange for score 20-39 (fair)', () => {
    expect(scoreBadge(30).color).toContain('orange')
    expect(scoreBadge(30).label).toBe('fair')
  })
  it('returns red for score < 20 (low)', () => {
    expect(scoreBadge(10).color).toContain('red')
    expect(scoreBadge(10).label).toBe('low')
  })
  it('returns gray for null', () => {
    expect(scoreBadge(null).color).toContain('gray')
    expect(scoreBadge(null).label).toBe('—')
  })
})

describe('relativeTime', () => {
  it('returns — for null', () => {
    expect(relativeTime(null)).toBe('—')
  })
  it('returns just now for recent timestamps', () => {
    const recent = new Date(Date.now() - 5000).toISOString()
    expect(relativeTime(recent)).toBe('just now')
  })
  it('returns minutes ago', () => {
    const ago = new Date(Date.now() - 3 * 60 * 1000).toISOString()
    expect(relativeTime(ago)).toBe('3m ago')
  })
  it('returns days ago', () => {
    const ago = new Date(Date.now() - 3 * 24 * 60 * 60 * 1000).toISOString()
    expect(relativeTime(ago)).toBe('3 days ago')
  })
})
