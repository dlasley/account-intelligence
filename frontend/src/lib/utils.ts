import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

// Score band → health token classes, backed by the --color-health-* ramp in
// globals.css. scoreBadge and scoreBarColor both derive from this table so the
// badge and the bar cannot drift.
//
// Class names are written out literally per band (not template-interpolated,
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

/** The fixed set of health band labels `scoreBadge` can return, for building
 * filter UIs without duplicating the band list. */
export const HEALTH_BAND_LABELS = [...HEALTH_BANDS.map((b) => b.label), UNKNOWN_BAND.label] as const

/** Renders a snake_case vertical as title case — `public_sector` → `Public Sector`. */
export function verticalLabel(vertical: string | null): string {
  if (!vertical) return '—'
  return vertical
    .split('_')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')
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
