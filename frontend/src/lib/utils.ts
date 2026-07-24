import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

// Score band → health token classes. 4 bands + unknown, backed by the
// --color-health-* ramp in globals.css (Healthy 70-100 / Moderate 45-69 /
// At risk 25-44 / Critical 0-24 / Unknown null). Both scoreBadge and
// scoreBarColor derive from this single table so the badge and the bar can
// never drift out of sync the way the old string-keyed barColorMap could.
//
// Class names are written out literal per band (not template-interpolated,
// e.g. NOT `bg-health-${token}-soft`) because Tailwind v4 statically scans
// source text for complete utility-class strings — an interpolated class
// name never appears as one, so the utility silently never compiles.
const HEALTH_BANDS = [
  { minScore: 70, label: 'Healthy',  badge: 'bg-health-strong-soft text-health-strong-on',     bar: 'bg-health-strong' },
  { minScore: 45, label: 'Moderate', badge: 'bg-health-moderate-soft text-health-moderate-on', bar: 'bg-health-moderate' },
  { minScore: 25, label: 'At risk',  badge: 'bg-health-risk-soft text-health-risk-on',         bar: 'bg-health-risk' },
  { minScore: 0,  label: 'Critical', badge: 'bg-health-critical-soft text-health-critical-on', bar: 'bg-health-critical' },
] as const

const UNKNOWN_BAND = {
  label: 'Unknown',
  badge: 'bg-health-unknown-soft text-health-unknown-on',
  bar: 'bg-health-unknown',
} as const

function healthBand(score: number | null) {
  if (score === null) return UNKNOWN_BAND
  return HEALTH_BANDS.find((b) => score >= b.minScore) ?? HEALTH_BANDS.at(-1)!
}

/** `color` carries both the badge bg+text soft/on token classes (e.g. for use
 * as a Badge `className`, since these are dynamic per-score, not one of the
 * fixed presets in badge.tsx's cva). */
export function scoreBadge(score: number | null): { label: string; color: string } {
  const band = healthBand(score)
  return { label: band.label, color: band.badge }
}

/** Solid fill for the dimension bar — the base health token, not the soft badge bg. */
export function scoreBarColor(score: number | null): string {
  return healthBand(score).bar
}

export function relativeTime(isoString: string | null): string {
  if (!isoString) return '—'
  const diff = Date.now() - new Date(isoString).getTime()
  const seconds = Math.floor(diff / 1000)
  if (seconds < 60) return 'just now'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days} day${days !== 1 ? 's' : ''} ago`
  const months = Math.floor(days / 30)
  if (months < 12) return `${months} month${months !== 1 ? 's' : ''} ago`
  return `${Math.floor(months / 12)} year${Math.floor(months / 12) !== 1 ? 's' : ''} ago`
}
