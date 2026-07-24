import { describe, it, expect } from 'vitest'
import { scoreBadge, scoreBarColor, relativeTime } from '@/lib/utils'

// Bands rewritten for Phase 1 (SP-2 redesign): 5 bands -> 4 + unknown, backed by
// the --color-health-* token ramp instead of ad hoc Tailwind palette classes.
// Old: high(80)/good(60)/medium(40)/fair(20)/low(0), green/emerald/yellow/orange/red.
// New: Healthy(70)/Moderate(45)/At risk(25)/Critical(0)/Unknown(null), thresholds
// approved by the user alongside the handoff's health ramp.
describe('scoreBadge', () => {
  it('returns Healthy for score >= 70', () => {
    expect(scoreBadge(90).label).toBe('Healthy')
    expect(scoreBadge(90).color).toContain('health-strong')
    expect(scoreBadge(70).label).toBe('Healthy')
  })
  it('returns Moderate for score 45-69', () => {
    expect(scoreBadge(69).label).toBe('Moderate')
    expect(scoreBadge(50).color).toContain('health-moderate')
    expect(scoreBadge(45).label).toBe('Moderate')
  })
  it('returns At risk for score 25-44', () => {
    expect(scoreBadge(44).label).toBe('At risk')
    expect(scoreBadge(30).color).toContain('health-risk')
    expect(scoreBadge(25).label).toBe('At risk')
  })
  it('returns Critical for score 0-24', () => {
    expect(scoreBadge(24).label).toBe('Critical')
    expect(scoreBadge(10).color).toContain('health-critical')
    expect(scoreBadge(0).label).toBe('Critical')
  })
  it('returns Unknown for null', () => {
    expect(scoreBadge(null).label).toBe('Unknown')
    expect(scoreBadge(null).color).toContain('health-unknown')
  })
  it('color carries both the badge bg-soft and text-on token classes', () => {
    expect(scoreBadge(90).color).toBe('bg-health-strong-soft text-health-strong-on')
  })
})

describe('scoreBarColor', () => {
  it('returns the solid health token (not the soft badge bg) per band', () => {
    expect(scoreBarColor(90)).toBe('bg-health-strong')
    expect(scoreBarColor(50)).toBe('bg-health-moderate')
    expect(scoreBarColor(30)).toBe('bg-health-risk')
    expect(scoreBarColor(10)).toBe('bg-health-critical')
    expect(scoreBarColor(null)).toBe('bg-health-unknown')
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
